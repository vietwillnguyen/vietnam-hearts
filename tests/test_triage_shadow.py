"""The shadow classifier records a second opinion and never acts on one."""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.channels.gmail import GmailAdapter
from app.services.triage.protocol import TriageUnavailable
from app.services.triage.shadow import ShadowingClassifier, agreement
from tests.fixtures.email_bot import (
    FakeClassifier,
    FakeTransport,
    load_gmail,  # noqa: F401  (fixture loader)
    signals,
)


@pytest.fixture
def message():
    transport = FakeTransport(mails=[load_gmail("signup_en.json")])
    return GmailAdapter(transport).parse(
        transport.list_unprocessed(
            newer_than_days=7, exclude_label="VH-Bot/Seen", limit=1
        )[0]
    )


class TestTheDeciderDecides:
    def test_the_deciders_answer_is_returned(self, message):
        decider = FakeClassifier(default=signals(category="signup", confidence=0.91))
        shadow = FakeClassifier(default=signals(category="faq", confidence=0.4))

        result = ShadowingClassifier(decider, shadow).classify(message)

        assert result.category == "signup"
        assert result.confidence == 0.91

    def test_both_classifiers_see_the_message(self, message):
        decider = FakeClassifier(default=signals())
        shadow = FakeClassifier(default=signals(category="faq"))

        ShadowingClassifier(decider, shadow).classify(message)

        assert decider.seen == [message.provider_message_id]
        assert shadow.seen == [message.provider_message_id]

    def test_the_shadows_answer_is_recorded(self, message):
        shadow_signals = signals(category="faq", confidence=0.42, classifier="shadow")
        classifier = ShadowingClassifier(
            FakeClassifier(default=signals()), FakeClassifier(default=shadow_signals)
        )

        classifier.classify(message)

        assert classifier.last_shadow == shadow_signals

    def test_the_name_is_the_deciders_name(self, message):
        decider = FakeClassifier(default=signals())
        decider.name = "jev"
        assert ShadowingClassifier(decider).name == "jev"


class TestFailureIsolation:
    def test_a_shadow_failure_never_affects_the_decision(self, message):
        # The shadow exists to be measured. If its key is missing or its model
        # is over quota, that must not cost the captain a triaged mail.
        classifier = ShadowingClassifier(
            FakeClassifier(default=signals(category="signup")),
            FakeClassifier(default=TriageUnavailable("shadow is down")),
        )

        result = classifier.classify(message)

        assert result.category == "signup"
        assert classifier.last_shadow is None

    def test_a_decider_failure_propagates(self, message):
        # The pipeline needs to see this so it can fail closed to needs_admin.
        classifier = ShadowingClassifier(
            FakeClassifier(default=TriageUnavailable("decider is down")),
            FakeClassifier(default=signals()),
        )
        with pytest.raises(TriageUnavailable):
            classifier.classify(message)

    def test_no_shadow_configured_is_not_an_error(self, message):
        classifier = ShadowingClassifier(FakeClassifier(default=signals()))
        assert classifier.classify(message).category == "signup"
        assert classifier.last_shadow is None

    def test_an_unexpected_shadow_exception_is_also_swallowed(self, message):
        classifier = ShadowingClassifier(
            FakeClassifier(default=signals()),
            FakeClassifier(default=ValueError("something else entirely")),
        )
        assert classifier.classify(message).category == "signup"
        assert classifier.last_shadow is None


class TestAgreement:
    def test_full_agreement_reports_every_field_true(self):
        decided = signals(category="faq", language="en")
        assert agreement(decided, decided) == {
            "category": True,
            "language": True,
            "asks_for_human": True,
            "mentions_money_or_commitment": True,
        }

    def test_a_category_disagreement_is_reported_field_by_field(self):
        # "They disagreed" is not actionable; which field disagreed is.
        decided = signals(category="donation", money=True)
        shadow = signals(category="faq", money=True)
        result = agreement(decided, shadow)
        assert result["category"] is False
        assert result["mentions_money_or_commitment"] is True

    def test_confidence_is_not_part_of_agreement(self):
        # Jev's probabilities are calibrated and LiteLLM's self-report is not,
        # so comparing the two numbers would manufacture disagreements.
        assert "confidence" not in agreement(signals(), signals(confidence=0.1))

    def test_no_shadow_yields_no_agreement_record(self):
        assert agreement(signals(), None) == {}
