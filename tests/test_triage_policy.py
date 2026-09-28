"""The tier rules, exhaustively.

Every category gets a row, and so does every interaction between the rules,
because the rule *order* is the design: executive before automated before the
fail-closed checks before the table. Get the order wrong and a low-confidence
safeguarding mail is answered from the FAQ.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import dataclasses

import pytest

from app.services.triage.policy import (
    CATEGORY_TIERS,
    EXECUTIVE_CATEGORIES,
    decide,
    derive_tier,
    escalation_reason,
)
from app.services.triage.protocol import CATEGORIES, LANGUAGES, TriageSignals

THRESHOLD = 0.6


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


class TestEveryCategoryHasATier:
    def test_the_table_covers_every_category_in_the_enum(self):
        # A category with no tier would fall through to the .get() default and
        # be silently handled as needs_admin, which reads as working.
        assert set(CATEGORY_TIERS) == set(CATEGORIES)

    @pytest.mark.parametrize("category,expected", sorted(CATEGORY_TIERS.items()))
    def test_a_confident_english_mail_gets_its_categorys_tier(self, category, expected):
        assert derive_tier(s(category=category), THRESHOLD) == expected

    def test_the_executive_set_matches_the_table(self):
        from_table = {
            category
            for category, tier in CATEGORY_TIERS.items()
            if tier == "needs_executive"
        }
        assert from_table == EXECUTIVE_CATEGORIES

    def test_only_signup_and_faq_are_ever_answered(self):
        answerable = {
            category
            for category, tier in CATEGORY_TIERS.items()
            if tier == "auto_answer"
        }
        assert answerable == {"signup", "faq"}


class TestRuleOneExecutiveBeatsEverything:
    @pytest.mark.parametrize("category", sorted(EXECUTIVE_CATEGORIES))
    def test_an_executive_category_escalates_even_at_zero_confidence(self, category):
        # The category gate runs before the confidence gate, so a hesitant
        # safeguarding guess still reaches the captain.
        assert derive_tier(s(category=category, confidence=0.0), THRESHOLD) == (
            "needs_executive"
        )

    @pytest.mark.parametrize("category", sorted(EXECUTIVE_CATEGORIES))
    def test_an_executive_category_escalates_in_any_language(self, category):
        for language in LANGUAGES:
            assert derive_tier(s(category=category, language=language), THRESHOLD) == (
                "needs_executive"
            )

    @pytest.mark.parametrize(
        "category", ["signup", "faq", "volunteer_ops", "human_other"]
    )
    def test_the_money_signal_forces_executive_from_any_category(self, category):
        assert derive_tier(s(category=category, money=True), THRESHOLD) == (
            "needs_executive"
        )

    def test_the_money_signal_beats_the_automated_skip(self):
        # An "invoice" notification that is really a payment demand must not be
        # dropped as machine noise.
        assert derive_tier(s(category="automated", money=True), THRESHOLD) == (
            "needs_executive"
        )


class TestRuleTwoAutomatedStaysSkipped:
    def test_automated_is_skipped_at_low_confidence(self):
        # Whatever the confidence: the second line of defence behind the header
        # guards must never produce a holding reply.
        assert (
            derive_tier(s(category="automated", confidence=0.01), THRESHOLD) == "skip"
        )

    def test_automated_is_skipped_even_when_it_asks_for_a_human(self):
        # "I am away, please contact my colleague" is a vacation responder, and
        # replying to it starts a loop.
        assert derive_tier(s(category="automated", asks_for_human=True), THRESHOLD) == (
            "skip"
        )

    def test_automated_is_skipped_in_an_unknown_language(self):
        assert (
            derive_tier(s(category="automated", language="other"), THRESHOLD) == "skip"
        )


class TestRuleThreeFailClosed:
    def test_an_unknown_language_goes_to_a_person(self):
        assert derive_tier(s(category="signup", language="other"), THRESHOLD) == (
            "needs_admin"
        )

    def test_asking_for_a_human_goes_to_a_person(self):
        assert derive_tier(s(category="signup", asks_for_human=True), THRESHOLD) == (
            "needs_admin"
        )

    def test_confidence_below_the_threshold_goes_to_a_person(self):
        assert derive_tier(s(category="signup", confidence=0.59), THRESHOLD) == (
            "needs_admin"
        )

    def test_confidence_exactly_at_the_threshold_is_accepted(self):
        # The threshold is a floor, not an exclusive bound, so a calibrated
        # value of 0.6 means "0.6 is good enough".
        assert derive_tier(s(category="signup", confidence=THRESHOLD), THRESHOLD) == (
            "auto_answer"
        )

    def test_a_zero_threshold_accepts_anything_non_executive(self):
        assert derive_tier(s(category="faq", confidence=0.0), 0.0) == "auto_answer"

    def test_a_threshold_of_one_sends_everything_answerable_to_a_person(self):
        assert derive_tier(s(category="faq", confidence=0.99), 1.0) == "needs_admin"


class TestDecide:
    def test_decide_carries_every_signal_through_unchanged(self):
        signals = s(category="donation", language="vi", confidence=0.77, money=True)
        decision = decide(signals, THRESHOLD)

        assert decision.category == "donation"
        assert decision.language == "vi"
        assert decision.confidence == 0.77
        assert decision.mentions_money_or_commitment is True
        assert decision.classifier == "test"
        assert decision.tier == "needs_executive"

    def test_the_decision_round_trips_back_to_its_signals(self):
        signals = s(category="faq", confidence=0.8)
        assert decide(signals, THRESHOLD).signals() == signals

    def test_with_tier_overrides_only_the_tier(self):
        # The pipeline downgrades auto_answer to needs_admin when the confidence
        # gate or the knowledge base declines; nothing else may change.
        decision = decide(s(category="faq"), THRESHOLD)
        downgraded = decision.with_tier("needs_admin")
        assert downgraded.tier == "needs_admin"
        assert downgraded.signals() == decision.signals()

    def test_a_decision_is_immutable(self):
        # A pipeline that could rewrite the decision it is acting on would make
        # the audit row a guess about what was actually classified.
        decision = decide(s(), THRESHOLD)
        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.tier = "auto_answer"


class TestEscalationReason:
    @pytest.mark.parametrize("category", sorted(EXECUTIVE_CATEGORIES))
    def test_an_executive_category_names_itself(self, category):
        assert category in escalation_reason(s(category=category), THRESHOLD)

    def test_the_money_signal_is_named(self):
        reason = escalation_reason(s(category="faq", money=True), THRESHOLD)
        assert "money" in reason

    def test_a_low_confidence_reason_carries_both_numbers(self):
        reason = escalation_reason(s(category="faq", confidence=0.3), THRESHOLD)
        assert "0.30" in reason
        assert "0.60" in reason

    def test_the_reason_never_contains_mail_content(self):
        # It is written to the audit row, posted to Discord and put in the
        # forward, so it must be derivable from categories and numbers alone.
        for category in CATEGORIES:
            for language in LANGUAGES:
                reason = escalation_reason(
                    s(category=category, language=language, confidence=0.2), THRESHOLD
                )
                assert "@" not in reason
