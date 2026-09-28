"""``JevClassifier``: the decider, over the TypeSafe vendor SDK.

Jev is asked the four questions in one ``system_one`` call: the category and the
language as ``Choice`` questions, the two booleans as ``Noul`` questions. The
reason it is the decider rather than a general model is the confidence: a
``ChoiceAnswer`` carries real per-option probabilities, so
``TRIAGE_CONFIDENCE_THRESHOLD`` compares against a calibrated number rather than
a model's opinion of its own certainty.

It keeps the deciding role only if it reaches 100 percent recall on
``needs_executive`` over the bilingual golden set in E2. The vendor is early
access and its Vietnamese support is undocumented, which is precisely what the
golden-set gate and the shadow classifier exist to expose.
"""

from __future__ import annotations

from typing import Any

from app.services.channels.base import IncomingMessage
from app.services.triage.prompts import (
    ASKS_FOR_HUMAN_INSTRUCTIONS,
    CATEGORY_INSTRUCTIONS,
    LANGUAGE_INSTRUCTIONS,
    MONEY_INSTRUCTIONS,
)
from app.services.triage.protocol import (
    CATEGORIES,
    LANGUAGES,
    TriageSignals,
    TriageUnavailable,
)
from app.utils.logging_config import get_logger

logger = get_logger("triage_jev")

DEFAULT_MODEL = "jev-latest"

Q_CATEGORY = "category"
Q_LANGUAGE = "language"
Q_ASKS_FOR_HUMAN = "asks_for_human"
Q_MONEY = "mentions_money_or_commitment"

# A Noul answer is a probability of "yes", so it needs a cut. 0.5 is the
# neutral reading of the scale the vendor documents, and both of these
# questions are used to escalate rather than to answer, so the cost of a false
# positive is one extra forward.
NOUL_TRUE_AT = 0.5


def build_questions() -> dict[str, Any]:
    """The four questions as raw dictionaries.

    Dictionaries rather than the SDK's model classes so this function stays
    importable, and assertable, without constructing a client. The SDK accepts
    both forms.
    """
    return {
        Q_CATEGORY: {
            "type": "choice",
            "instructions": CATEGORY_INSTRUCTIONS,
            "criteria": dict.fromkeys(CATEGORIES),
        },
        Q_LANGUAGE: {
            "type": "choice",
            "instructions": LANGUAGE_INSTRUCTIONS,
            "criteria": dict.fromkeys(LANGUAGES),
        },
        Q_ASKS_FOR_HUMAN: {
            "type": "noul",
            "instructions": ASKS_FOR_HUMAN_INSTRUCTIONS,
        },
        Q_MONEY: {
            "type": "noul",
            "instructions": MONEY_INSTRUCTIONS,
        },
    }


def build_state(message: IncomingMessage) -> dict[str, str]:
    """What the classifier is shown: the subject and the body, nothing else.

    No sender address, deliberately. A classifier that can see the domain will
    learn to read it, and "this came from a gmail.com address" is not a fact
    the tier should ever turn on.
    """
    return {"subject": message.subject, "body": message.text}


class JevClassifier:
    """``TriageClassifier`` over ``typesafe_sdk``."""

    name = "jev"

    def __init__(self, client: Any, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_api_key(cls, api_key: str, model: str = DEFAULT_MODEL) -> JevClassifier:
        from typesafe_sdk import TypeSafeClient

        return cls(TypeSafeClient(api_key=api_key, model=model), model=model)

    def classify(self, message: IncomingMessage) -> TriageSignals:
        try:
            response = self._client.system_one(
                state=build_state(message),
                questions=build_questions(),
                model=self._model,
            )
        except Exception as exc:
            # The id is safe to carry; the message is not, and this exception
            # reaches Sentry, which runs with send_default_pii=True.
            raise TriageUnavailable(
                f"Jev classification failed for {message.provider_message_id}: "
                f"{type(exc).__name__}"
            ) from exc

        return self._to_signals(response, message.provider_message_id)

    def _to_signals(self, response: Any, message_id: str) -> TriageSignals:
        """Read the four answers, refusing anything that is not one of ours.

        A category outside the enum is a malformed response, not a new
        category: silently mapping it to ``human_other`` would let a model
        typo decide a tier. Raising sends it down the fail-closed path where
        the pipeline escalates it to a person.
        """
        try:
            choices = response.choices
            nouls = response.nouls
            category = choices[Q_CATEGORY].choice
            confidence = float(choices[Q_CATEGORY].confidence)
            language = choices[Q_LANGUAGE].choice
            asks_for_human = float(nouls[Q_ASKS_FOR_HUMAN].noul)
            money = float(nouls[Q_MONEY].noul)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise TriageUnavailable(
                f"Jev returned an unreadable answer set for {message_id}: "
                f"{type(exc).__name__}"
            ) from exc

        if category not in CATEGORIES:
            raise TriageUnavailable(
                f"Jev returned an unknown category for {message_id}: {category!r}"
            )
        if language not in LANGUAGES:
            raise TriageUnavailable(
                f"Jev returned an unknown language for {message_id}: {language!r}"
            )

        return TriageSignals(
            category=category,
            language=language,
            confidence=max(0.0, min(1.0, confidence)),
            asks_for_human=asks_for_human >= NOUL_TRUE_AT,
            mentions_money_or_commitment=money >= NOUL_TRUE_AT,
            classifier=self.name,
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
