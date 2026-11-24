"""Vietnam Hearts' category table, through the core's tier rules, exhaustively.

The rules themselves - executive before skip before the fail-closed checks
before the table - are inbox-core's and are tested there. What is ours is the
table in ``app/services/email_bot/inbox_client.toml``, and these tests pin
every row of it and every interaction a row can have with the rules, because a
one-word change to that file can put a safeguarding mail in front of the FAQ.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest
from inbox_core import TriageSignals, decide, derive_tier
from inbox_core.prompts import render_triage_prompts
from inbox_core.triage import escalation_reason

from app.services.email_bot.client import client_config
from app.services.email_bot.settings import EmailBotSettings

THRESHOLD = 0.6

# The table as the design states it. The test, not the file, is the authority:
# changing a row means changing this too, in the same review.
EXPECTED_TIERS = {
    "signup": "auto_answer",
    "faq": "auto_answer",
    "volunteer_ops": "needs_admin",
    "sponsorship": "needs_executive",
    "donation": "needs_executive",
    "partnership": "needs_executive",
    "press": "needs_executive",
    "safeguarding_legal": "needs_executive",
    "acceptance": "needs_executive",
    "human_other": "needs_admin",
    "automated": "skip",
}
EXECUTIVE = sorted(k for k, v in EXPECTED_TIERS.items() if v == "needs_executive")
LANGUAGES = ("en", "vi", "other")


@pytest.fixture(scope="module")
def policy():
    return client_config(EmailBotSettings(triage_confidence_threshold=THRESHOLD)).triage


def s(
    category: str = "faq",
    language: str = "en",
    confidence: float = 0.9,
    asks_for_human: bool = False,
    money: bool = False,
) -> TriageSignals:
    return TriageSignals(
        category=category,
        language=language,
        confidence=confidence,
        asks_for_human=asks_for_human,
        mentions_money_or_commitment=money,
        classifier="test",
    )


class TestTheTable:
    def test_it_lists_exactly_the_eleven_categories(self, policy):
        assert {rule.name for rule in policy.categories} == set(EXPECTED_TIERS)

    @pytest.mark.parametrize("category,expected", sorted(EXPECTED_TIERS.items()))
    def test_a_confident_english_mail_gets_its_categorys_tier(
        self, policy, category, expected
    ):
        assert derive_tier(s(category=category), policy) == expected

    def test_only_signup_and_faq_are_ever_answered(self, policy):
        assert policy.auto_answer_categories == {"signup", "faq"}

    def test_the_languages_are_english_and_vietnamese(self, policy):
        assert list(policy.languages) == ["en", "vi"]

    def test_an_unavailable_classifier_is_recorded_as_human_other(self, policy):
        assert policy.fallback_category == "human_other"

    def test_the_seriousness_order_the_classifier_is_given_is_the_designs(self, policy):
        # The multi-topic rule: a mail that asks about class times and also
        # offers a donation is classified as the donation.
        assert policy.seriousness_order() == (
            "safeguarding_legal",
            "acceptance",
            "sponsorship",
            "donation",
            "partnership",
            "press",
            "volunteer_ops",
            "human_other",
            "signup",
            "faq",
        )

    def test_the_classifier_is_told_who_it_classifies_for(self):
        prompts = render_triage_prompts(client_config())
        assert "Vietnam Hearts" in prompts.system
        assert "teaches English to underprivileged children" in prompts.system


class TestRuleOneExecutiveBeatsEverything:
    @pytest.mark.parametrize("category", EXECUTIVE)
    def test_an_executive_category_escalates_even_at_zero_confidence(
        self, policy, category
    ):
        assert derive_tier(s(category=category, confidence=0.0), policy) == (
            "needs_executive"
        )

    @pytest.mark.parametrize("category", EXECUTIVE)
    def test_an_executive_category_escalates_in_any_language(self, policy, category):
        for language in LANGUAGES:
            assert derive_tier(s(category=category, language=language), policy) == (
                "needs_executive"
            )

    @pytest.mark.parametrize(
        "category", ["signup", "faq", "volunteer_ops", "human_other", "automated"]
    )
    def test_the_money_signal_forces_executive_from_any_category(
        self, policy, category
    ):
        assert derive_tier(s(category=category, money=True), policy) == (
            "needs_executive"
        )


class TestRuleTwoAutomatedStaysSkipped:
    def test_automated_is_skipped_at_low_confidence(self, policy):
        assert derive_tier(s(category="automated", confidence=0.01), policy) == "skip"

    def test_automated_is_skipped_even_when_it_asks_for_a_human(self, policy):
        assert (
            derive_tier(s(category="automated", asks_for_human=True), policy) == "skip"
        )

    def test_automated_is_skipped_in_an_unknown_language(self, policy):
        assert derive_tier(s(category="automated", language="other"), policy) == "skip"


class TestRuleThreeFailClosed:
    def test_an_unknown_language_goes_to_a_person(self, policy):
        assert derive_tier(s(category="signup", language="other"), policy) == (
            "needs_admin"
        )

    def test_asking_for_a_human_goes_to_a_person(self, policy):
        assert derive_tier(s(category="signup", asks_for_human=True), policy) == (
            "needs_admin"
        )

    def test_confidence_below_the_threshold_goes_to_a_person(self, policy):
        assert derive_tier(s(category="signup", confidence=0.59), policy) == (
            "needs_admin"
        )

    def test_confidence_exactly_at_the_threshold_is_accepted(self, policy):
        assert derive_tier(s(category="signup", confidence=THRESHOLD), policy) == (
            "auto_answer"
        )

    def test_the_runs_threshold_comes_from_the_dashboard_setting(self):
        strict = client_config(EmailBotSettings(triage_confidence_threshold=1.0))
        assert derive_tier(s(category="faq", confidence=0.99), strict.triage) == (
            "needs_admin"
        )


class TestEscalationReason:
    @pytest.mark.parametrize("category", EXECUTIVE)
    def test_an_executive_category_names_itself(self, policy, category):
        assert category in escalation_reason(s(category=category), policy)

    def test_a_low_confidence_reason_carries_both_numbers(self, policy):
        reason = escalation_reason(s(category="faq", confidence=0.3), policy)
        assert "0.30" in reason
        assert "0.60" in reason

    def test_the_reason_never_contains_mail_content(self, policy):
        for category in EXPECTED_TIERS:
            for language in LANGUAGES:
                reason = escalation_reason(
                    s(category=category, language=language, confidence=0.2), policy
                )
                assert "@" not in reason

    def test_decide_attaches_the_tier_to_the_signals(self, policy):
        decision = decide(s(category="donation", language="vi", money=True), policy)
        assert decision.tier == "needs_executive"
        assert decision.language == "vi"
