"""``ShadowingClassifier``: run the decider, then the shadow, record both.

The decider's answer is what comes back, always. The shadow's answer is
recorded on the message row and never influences anything, which is what makes
the E2 disagreement report an honest measurement rather than an artefact of the
two having been blended.

A shadow failure is swallowed, on purpose. The shadow exists to be measured; if
its model is over quota or its key is missing, that must not cost the captain a
triaged mail. A decider failure propagates, because the pipeline needs to
fail closed on it.
"""

from __future__ import annotations

from app.services.channels.base import IncomingMessage
from app.services.triage.protocol import (
    TriageClassifier,
    TriageSignals,
)
from app.utils.logging_config import get_logger

logger = get_logger("triage_shadow")


class ShadowingClassifier:
    """Decorates a decider with a non-authoritative second opinion."""

    def __init__(
        self, decider: TriageClassifier, shadow: TriageClassifier | None = None
    ) -> None:
        self._decider = decider
        self._shadow = shadow
        self.last_shadow: TriageSignals | None = None

    @property
    def name(self) -> str:
        return self._decider.name

    def classify(self, message: IncomingMessage) -> TriageSignals:
        """The decider's signals. ``last_shadow`` holds the shadow's, if any.

        The shadow is returned out of band rather than in the return value so
        that ``TriageClassifier`` stays the single-answer protocol every other
        implementation satisfies, and so a caller that does not care about the
        shadow cannot accidentally act on it.
        """
        decided = self._decider.classify(message)
        self.last_shadow = self._run_shadow(message)
        return decided

    def _run_shadow(self, message: IncomingMessage) -> TriageSignals | None:
        if self._shadow is None:
            return None
        try:
            return self._shadow.classify(message)
        except Exception as exc:
            logger.warning(
                "Shadow classifier failed for %s: %s",
                message.provider_message_id,
                type(exc).__name__,
            )
            return None


def agreement(decided: TriageSignals, shadow: TriageSignals | None) -> dict[str, bool]:
    """Field-by-field agreement between the two, for the E2 report.

    Returned as a dict rather than a single bool because "they disagreed" is
    not actionable: a category disagreement on a safeguarding mail and a
    language disagreement on a bilingual one are different findings.
    """
    if shadow is None:
        return {}
    return {
        "category": decided.category == shadow.category,
        "language": decided.language == shadow.language,
        "asks_for_human": decided.asks_for_human == shadow.asks_for_human,
        "mentions_money_or_commitment": (
            decided.mentions_money_or_commitment == shadow.mentions_money_or_commitment
        ),
    }
