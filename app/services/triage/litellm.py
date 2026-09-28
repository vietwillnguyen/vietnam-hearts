"""``LiteLLMClassifier``: the shadow, over one JSON-schema structured call.

Asks one general model for an object with the same four fields Jev answers, so
the two are directly comparable on the same mail. LiteLLM is the seam rather
than a vendor SDK because the whole point is that the model is a setting:
``TRIAGE_FALLBACK_MODEL`` moves between ``gemini/gemini-3.5-flash-lite`` and
``anthropic/claude-haiku-4-5-20251001`` with no deploy and no code change.

It runs in shadow rather than deciding because its ``confidence`` is
self-reported and uncalibrated. That number is recorded and reported on, but it
is not what ``TRIAGE_CONFIDENCE_THRESHOLD`` is tuned against unless and until
E2 says Jev has failed its gate and this becomes the decider.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.channels.base import IncomingMessage
from app.services.triage.jev import build_state
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

logger = get_logger("triage_litellm")

DEFAULT_MODEL = "gemini/gemini-3.5-flash-lite"

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "language": {"type": "string", "enum": list(LANGUAGES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "asks_for_human": {"type": "boolean"},
        "mentions_money_or_commitment": {"type": "boolean"},
    },
    "required": [
        "category",
        "language",
        "confidence",
        "asks_for_human",
        "mentions_money_or_commitment",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You classify email for a charity's volunteer inbox. Answer only with the "
    "JSON object the schema describes.\n"
    "\n"
    f"category: {CATEGORY_INSTRUCTIONS}\n"
    f"language: {LANGUAGE_INSTRUCTIONS}\n"
    f"asks_for_human: {ASKS_FOR_HUMAN_INSTRUCTIONS}\n"
    f"mentions_money_or_commitment: {MONEY_INSTRUCTIONS}\n"
    "confidence: how certain you are of the category, from 0 to 1."
)


class LiteLLMClassifier:
    """``TriageClassifier`` over ``litellm.completion`` with a JSON schema."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        completion: Any = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._completion = completion
        self._api_key = api_key

    @property
    def name(self) -> str:
        """Includes the model, because the model is the setting that varies.

        A disagreement report that only said "litellm" would be unreadable the
        first time the model is swapped mid-window.
        """
        return f"litellm:{self._model}"

    def _call(self, message: IncomingMessage) -> str:
        completion = self._completion
        if completion is None:
            # Imported lazily: litellm is a heavy import and nothing needs it
            # while the shadow is unconfigured.
            from litellm import completion as litellm_completion

            completion = litellm_completion

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(build_state(message), ensure_ascii=False),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "triage",
                    "schema": RESPONSE_SCHEMA,
                    "strict": True,
                },
            },
            "temperature": 0,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key

        response = completion(**kwargs)
        return _first_content(response)

    def classify(self, message: IncomingMessage) -> TriageSignals:
        try:
            content = self._call(message)
        except Exception as exc:
            raise TriageUnavailable(
                f"LiteLLM classification failed for {message.provider_message_id}: "
                f"{type(exc).__name__}"
            ) from exc

        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TriageUnavailable(
                f"LiteLLM returned non-JSON for {message.provider_message_id}"
            ) from exc

        return self._to_signals(payload, message.provider_message_id)

    def _to_signals(self, payload: Any, message_id: str) -> TriageSignals:
        if not isinstance(payload, dict):
            raise TriageUnavailable(
                f"LiteLLM returned a {type(payload).__name__} for {message_id}"
            )

        category = payload.get("category")
        language = payload.get("language")
        if category not in CATEGORIES:
            raise TriageUnavailable(
                f"LiteLLM returned an unknown category for {message_id}: {category!r}"
            )
        if language not in LANGUAGES:
            raise TriageUnavailable(
                f"LiteLLM returned an unknown language for {message_id}: {language!r}"
            )

        try:
            confidence = float(payload["confidence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TriageUnavailable(
                f"LiteLLM returned an unreadable confidence for {message_id}"
            ) from exc

        for field in ("asks_for_human", "mentions_money_or_commitment"):
            if not isinstance(payload.get(field), bool):
                raise TriageUnavailable(
                    f"LiteLLM returned a non-boolean {field} for {message_id}"
                )

        return TriageSignals(
            category=category,
            language=language,
            confidence=max(0.0, min(1.0, confidence)),
            asks_for_human=payload["asks_for_human"],
            mentions_money_or_commitment=payload["mentions_money_or_commitment"],
            classifier=self.name,
        )


def _first_content(response: Any) -> str:
    """The text of the first choice, whether the response is an object or a dict.

    LiteLLM normalises providers onto an OpenAI-shaped object, but the recorded
    fixtures are committed as plain JSON so they stay readable and reviewable.
    Accepting both means the contract test runs against the real recorded body.
    """
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if not choices:
            raise TriageUnavailable("LiteLLM returned no choices")
        message = choices[0].get("message") or {}
        return message.get("content") or ""

    choices = getattr(response, "choices", None) or []
    if not choices:
        raise TriageUnavailable("LiteLLM returned no choices")
    return getattr(getattr(choices[0], "message", None), "content", "") or ""
