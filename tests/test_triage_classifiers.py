"""Contract tests for both classifiers against recorded response bodies.

The Jev fixtures are validated through ``typesafe_sdk.SystemOneResponse`` itself,
so a fixture that has drifted from the vendor's model fails here rather than
passing a hand-shaped fake - the parent spec's remedy for defect 2, applied to
the classifier layer.

Malformed responses raise instead of being coerced. A model typo must not be
allowed to pick a tier: an unknown category mapped to ``human_other`` "to be
safe" is how a safeguarding mail quietly becomes an admin task.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import json

import pytest

from app.services.channels.base import IncomingMessage
from app.services.triage.jev import (
    NOUL_TRUE_AT,
    JevClassifier,
    build_questions,
    build_state,
)
from app.services.triage.litellm import RESPONSE_SCHEMA, LiteLLMClassifier
from app.services.triage.protocol import CATEGORIES, LANGUAGES, TriageUnavailable
from tests.fixtures.email_bot import load_triage

MESSAGE = IncomingMessage(
    channel="email",
    thread_key="thread-1",
    provider_message_id="msg-1",
    sender_key="hash",
    sender_address="sender@example.com",
    subject="Volunteering with Vietnam Hearts",
    text="How do I sign up?",
    rfc_message_id="<abc@x>",
)


def jev_response(name: str, **edits):
    """Load a recorded body through the vendor's own model.

    This is the assertion that keeps the fixtures honest: if TypeSafe changes
    the response shape, ``model_validate`` fails and every test in this class
    fails with it, rather than the suite continuing to pass against a fake.

    Variants are expressed as edits to the raw body rather than to the parsed
    object, because the SDK's answer models are frozen - which is itself worth
    knowing, since it means nothing downstream can rewrite a classifier's answer
    after the fact.
    """
    from typesafe_sdk import SystemOneResponse

    body = load_triage(name)
    for question, changes in edits.items():
        if changes is None:
            del body["answers"][question]
        else:
            body["answers"][question].update(changes)
    return SystemOneResponse.model_validate(body)


class StubJevClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict] = []

    def system_one(self, state, questions, model=None):
        self.calls.append({"state": state, "questions": questions, "model": model})
        if self._error is not None:
            raise self._error
        return self._response


class TestJevQuestions:
    def test_all_four_questions_are_asked_in_one_call(self):
        questions = build_questions()
        assert set(questions) == {
            "category",
            "language",
            "asks_for_human",
            "mentions_money_or_commitment",
        }

    def test_the_category_criteria_are_exactly_the_enum(self):
        # A criterion the enum does not contain would be unparseable on the way
        # back; one it is missing could never be chosen.
        assert set(build_questions()["category"]["criteria"]) == set(CATEGORIES)

    def test_the_language_criteria_are_exactly_the_enum(self):
        assert set(build_questions()["language"]["criteria"]) == set(LANGUAGES)

    def test_the_two_booleans_are_noul_questions(self):
        questions = build_questions()
        assert questions["asks_for_human"]["type"] == "noul"
        assert questions["mentions_money_or_commitment"]["type"] == "noul"

    def test_the_multi_topic_rule_is_in_the_category_instructions(self):
        instructions = build_questions()["category"]["instructions"]
        assert "more than one topic" in instructions
        assert "most serious" in instructions

    def test_the_classifier_never_sees_the_sender(self):
        # A classifier that can read the sender's domain will learn to, and
        # "this came from a gmail.com address" must never decide a tier.
        state = build_state(MESSAGE)
        assert set(state) == {"subject", "body"}
        assert "example.com" not in json.dumps(state)


class TestJevParsing:
    @pytest.mark.parametrize(
        "fixture,category,language",
        [
            ("jev_signup_en.json", "signup", "en"),
            ("jev_signup_vi.json", "signup", "vi"),
            ("jev_faq_en.json", "faq", "en"),
            ("jev_safeguarding_vi.json", "safeguarding_legal", "vi"),
            ("jev_sponsorship_en.json", "sponsorship", "en"),
            ("jev_multi_topic_en.json", "donation", "en"),
            ("jev_automated.json", "automated", "en"),
        ],
    )
    def test_every_recorded_answer_parses(self, fixture, category, language):
        classifier = JevClassifier(StubJevClient(jev_response(fixture)))
        result = classifier.classify(MESSAGE)

        assert result.category == category
        assert result.language == language
        assert result.classifier == "jev"
        assert 0.0 <= result.confidence <= 1.0

    def test_the_confidence_is_the_choice_probability(self):
        # Jev's calibrated per-option probability is exactly why it decides
        # rather than shadows.
        classifier = JevClassifier(StubJevClient(jev_response("jev_signup_en.json")))
        assert classifier.classify(MESSAGE).confidence == pytest.approx(0.94)

    def test_a_high_noul_reads_as_true(self):
        classifier = JevClassifier(
            StubJevClient(jev_response("jev_safeguarding_vi.json"))
        )
        result = classifier.classify(MESSAGE)
        assert result.asks_for_human is True

    def test_a_low_noul_reads_as_false(self):
        classifier = JevClassifier(StubJevClient(jev_response("jev_signup_en.json")))
        result = classifier.classify(MESSAGE)
        assert result.asks_for_human is False
        assert result.mentions_money_or_commitment is False

    def test_the_money_noul_is_read(self):
        classifier = JevClassifier(
            StubJevClient(jev_response("jev_sponsorship_en.json"))
        )
        assert classifier.classify(MESSAGE).mentions_money_or_commitment is True

    def test_the_noul_cut_is_at_the_documented_midpoint(self):
        at_the_cut = jev_response(
            "jev_signup_en.json", asks_for_human={"noul": NOUL_TRUE_AT}
        )
        assert JevClassifier(StubJevClient(at_the_cut)).classify(MESSAGE).asks_for_human

        just_below = jev_response(
            "jev_signup_en.json", asks_for_human={"noul": NOUL_TRUE_AT - 0.01}
        )
        assert not (
            JevClassifier(StubJevClient(just_below)).classify(MESSAGE).asks_for_human
        )

    def test_the_model_override_is_passed_through(self):
        client = StubJevClient(jev_response("jev_signup_en.json"))
        JevClassifier(client, model="jev-pinned").classify(MESSAGE)
        assert client.calls[0]["model"] == "jev-pinned"


class TestJevRefusesToGuess:
    def test_an_unknown_category_raises(self):
        # Rather than mapping it onto something plausible.
        classifier = JevClassifier(
            StubJevClient(jev_response("jev_unknown_category.json"))
        )
        with pytest.raises(TriageUnavailable, match="unknown category"):
            classifier.classify(MESSAGE)

    def test_an_unknown_language_raises(self):
        response = jev_response("jev_signup_en.json", language={"choice": "fr"})
        with pytest.raises(TriageUnavailable, match="unknown language"):
            JevClassifier(StubJevClient(response)).classify(MESSAGE)

    def test_a_missing_answer_raises(self):
        response = jev_response("jev_signup_en.json", language=None)
        with pytest.raises(TriageUnavailable, match="unreadable answer set"):
            JevClassifier(StubJevClient(response)).classify(MESSAGE)

    def test_the_vendors_answer_models_are_frozen(self):
        # Relied on above, and worth pinning: nothing downstream can rewrite a
        # classifier's answer after it has been recorded.
        import pydantic

        response = jev_response("jev_signup_en.json")
        with pytest.raises(pydantic.ValidationError):
            response.choices["category"].choice = "faq"

    def test_a_transport_failure_raises_triage_unavailable(self):
        classifier = JevClassifier(
            StubJevClient(error=RuntimeError("connection reset"))
        )
        with pytest.raises(TriageUnavailable):
            classifier.classify(MESSAGE)

    def test_the_exception_carries_the_id_and_not_the_mail(self):
        # Exceptions reach Sentry, which runs with send_default_pii=True.
        classifier = JevClassifier(StubJevClient(error=RuntimeError("boom")))
        with pytest.raises(TriageUnavailable) as raised:
            classifier.classify(MESSAGE)

        text = str(raised.value)
        assert "msg-1" in text
        assert MESSAGE.text not in text
        assert MESSAGE.subject not in text
        assert "@" not in text


class StubCompletion:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.kwargs: list[dict] = []

    def __call__(self, **kwargs):
        self.kwargs.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class TestLiteLLMSchema:
    def test_the_schema_enumerates_exactly_the_category_enum(self):
        assert set(RESPONSE_SCHEMA["properties"]["category"]["enum"]) == set(CATEGORIES)

    def test_the_schema_enumerates_exactly_the_language_enum(self):
        assert set(RESPONSE_SCHEMA["properties"]["language"]["enum"]) == set(LANGUAGES)

    def test_all_four_fields_plus_confidence_are_required(self):
        assert set(RESPONSE_SCHEMA["required"]) == {
            "category",
            "language",
            "confidence",
            "asks_for_human",
            "mentions_money_or_commitment",
        }

    def test_additional_properties_are_refused(self):
        assert RESPONSE_SCHEMA["additionalProperties"] is False


class TestLiteLLMParsing:
    @pytest.mark.parametrize(
        "fixture,category,language",
        [
            ("litellm_signup_en.json", "signup", "en"),
            ("litellm_faq_en.json", "faq", "en"),
            ("litellm_safeguarding_vi.json", "safeguarding_legal", "vi"),
            ("litellm_multi_topic_en.json", "faq", "en"),
        ],
    )
    def test_every_recorded_body_parses(self, fixture, category, language):
        completion = StubCompletion(load_triage(fixture))
        result = LiteLLMClassifier(completion=completion).classify(MESSAGE)

        assert result.category == category
        assert result.language == language

    def test_the_name_carries_the_model(self):
        # A disagreement report that only said "litellm" would be unreadable the
        # first time the model is swapped mid-window.
        classifier = LiteLLMClassifier(model="anthropic/claude-haiku-4-5-20251001")
        assert classifier.name == "litellm:anthropic/claude-haiku-4-5-20251001"

    def test_the_request_asks_for_the_json_schema(self):
        completion = StubCompletion(load_triage("litellm_signup_en.json"))
        LiteLLMClassifier(completion=completion).classify(MESSAGE)

        sent = completion.kwargs[0]
        assert sent["response_format"]["type"] == "json_schema"
        assert sent["response_format"]["json_schema"]["schema"] == RESPONSE_SCHEMA
        assert sent["temperature"] == 0

    def test_the_prompt_never_carries_the_sender(self):
        completion = StubCompletion(load_triage("litellm_signup_en.json"))
        LiteLLMClassifier(completion=completion).classify(MESSAGE)
        assert "example.com" not in json.dumps(completion.kwargs[0]["messages"])

    def test_an_anthropic_key_is_only_sent_when_configured(self):
        completion = StubCompletion(load_triage("litellm_signup_en.json"))
        LiteLLMClassifier(completion=completion).classify(MESSAGE)
        assert "api_key" not in completion.kwargs[0]

        keyed = StubCompletion(load_triage("litellm_signup_en.json"))
        LiteLLMClassifier(completion=keyed, api_key="sk-test").classify(MESSAGE)
        assert keyed.kwargs[0]["api_key"] == "sk-test"

    def test_an_object_shaped_response_also_parses(self):
        # LiteLLM normalises providers onto an OpenAI-shaped object at runtime;
        # the fixtures are plain JSON so they stay reviewable.
        from types import SimpleNamespace

        body = load_triage("litellm_signup_en.json")
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=body["choices"][0]["message"]["content"]
                    )
                )
            ]
        )
        result = LiteLLMClassifier(completion=StubCompletion(response)).classify(
            MESSAGE
        )
        assert result.category == "signup"


class TestLiteLLMRefusesToGuess:
    def test_a_malformed_confidence_raises(self):
        completion = StubCompletion(load_triage("litellm_malformed.json"))
        with pytest.raises(TriageUnavailable, match="confidence"):
            LiteLLMClassifier(completion=completion).classify(MESSAGE)

    def test_non_json_content_raises(self):
        body = load_triage("litellm_signup_en.json")
        body["choices"][0]["message"]["content"] = "Sure! The category is signup."
        with pytest.raises(TriageUnavailable, match="non-JSON"):
            LiteLLMClassifier(completion=StubCompletion(body)).classify(MESSAGE)

    def test_an_unknown_category_raises(self):
        body = load_triage("litellm_signup_en.json")
        body["choices"][0]["message"]["content"] = json.dumps(
            {
                "category": "enquiry",
                "language": "en",
                "confidence": 0.9,
                "asks_for_human": False,
                "mentions_money_or_commitment": False,
            }
        )
        with pytest.raises(TriageUnavailable, match="unknown category"):
            LiteLLMClassifier(completion=StubCompletion(body)).classify(MESSAGE)

    def test_a_non_boolean_flag_raises(self):
        body = load_triage("litellm_signup_en.json")
        body["choices"][0]["message"]["content"] = json.dumps(
            {
                "category": "signup",
                "language": "en",
                "confidence": 0.9,
                "asks_for_human": "no",
                "mentions_money_or_commitment": False,
            }
        )
        with pytest.raises(TriageUnavailable, match="non-boolean"):
            LiteLLMClassifier(completion=StubCompletion(body)).classify(MESSAGE)

    def test_no_choices_raises(self):
        with pytest.raises(TriageUnavailable):
            LiteLLMClassifier(completion=StubCompletion({"choices": []})).classify(
                MESSAGE
            )

    def test_a_transport_failure_raises_triage_unavailable(self):
        completion = StubCompletion(error=RuntimeError("429 rate limited"))
        with pytest.raises(TriageUnavailable):
            LiteLLMClassifier(completion=completion).classify(MESSAGE)

    def test_the_exception_carries_the_id_and_not_the_mail(self):
        completion = StubCompletion(error=RuntimeError("boom"))
        with pytest.raises(TriageUnavailable) as raised:
            LiteLLMClassifier(completion=completion).classify(MESSAGE)
        assert "msg-1" in str(raised.value)
        assert MESSAGE.text not in str(raised.value)


class TestTheTwoImplementationsAreComparable:
    def test_both_answer_the_same_four_fields(self):
        jev = JevClassifier(StubJevClient(jev_response("jev_signup_en.json"))).classify(
            MESSAGE
        )
        shadow = LiteLLMClassifier(
            completion=StubCompletion(load_triage("litellm_signup_en.json"))
        ).classify(MESSAGE)

        # Same shape, so the E2 disagreement report compares like with like.
        assert jev.as_record().keys() == shadow.as_record().keys()
        assert jev.category == shadow.category

    def test_a_recorded_disagreement_is_visible(self):
        # The multi-topic mail: Jev reads it as a donation, the shadow as an
        # FAQ. Exactly the kind of case the E2 report exists to surface.
        jev = JevClassifier(
            StubJevClient(jev_response("jev_multi_topic_en.json"))
        ).classify(MESSAGE)
        shadow = LiteLLMClassifier(
            completion=StubCompletion(load_triage("litellm_multi_topic_en.json"))
        ).classify(MESSAGE)

        assert jev.category == "donation"
        assert shadow.category == "faq"

    def test_the_shadow_record_contains_no_mail_content(self):
        record = (
            LiteLLMClassifier(
                completion=StubCompletion(load_triage("litellm_signup_en.json"))
            )
            .classify(MESSAGE)
            .as_record()
        )
        assert set(record) == {
            "category",
            "language",
            "confidence",
            "asks_for_human",
            "mentions_money_or_commitment",
            "classifier",
        }
