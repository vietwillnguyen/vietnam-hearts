"""The triage contract: four typed answers in, one tier out.

The classifier is asked only what a classifier can be trusted with - what the
mail is about, what language it is in, whether the writer asked for a person,
and whether money or a commitment is mentioned. It is never asked what to do
about any of that. ``policy.derive_tier`` owns that, in code, because the
consequence of a wrong tier on a safeguarding mail is not a quality problem.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Protocol

from app.services.channels.base import IncomingMessage

Category = Literal[
    "signup",
    "faq",
    "volunteer_ops",
    "sponsorship",
    "donation",
    "partnership",
    "press",
    "safeguarding_legal",
    "acceptance",
    "human_other",
    "automated",
]

Tier = Literal["auto_answer", "needs_admin", "needs_executive", "skip"]

Language = Literal["en", "vi", "other"]

CATEGORIES: tuple[Category, ...] = (
    "signup",
    "faq",
    "volunteer_ops",
    "sponsorship",
    "donation",
    "partnership",
    "press",
    "safeguarding_legal",
    "acceptance",
    "human_other",
    "automated",
)

LANGUAGES: tuple[Language, ...] = ("en", "vi", "other")


class TriageUnavailable(RuntimeError):
    """The classifier could not produce a usable answer.

    Raised rather than guessed, for both a transport failure and a malformed
    response. The pipeline retries with backoff and then treats it as
    ``needs_admin``, which is the fail-closed behaviour the design requires:
    an unavailable classifier must never silently become "probably an FAQ".
    """


@dataclass(frozen=True)
class TriageSignals:
    """What a classifier reports. No tier, deliberately."""

    category: Category
    language: Language
    confidence: float
    asks_for_human: bool
    mentions_money_or_commitment: bool
    classifier: str

    def as_record(self) -> dict[str, object]:
        """The JSON form stored in ``messages.triage_shadow``.

        Ids, categories and numbers only - there is nothing here that could
        carry a subject, a body or an address into the database.
        """
        return {
            "category": self.category,
            "language": self.language,
            "confidence": round(float(self.confidence), 4),
            "asks_for_human": bool(self.asks_for_human),
            "mentions_money_or_commitment": bool(self.mentions_money_or_commitment),
            "classifier": self.classifier,
        }


@dataclass(frozen=True)
class TriageDecision(TriageSignals):
    """Signals plus the tier that policy derived from them."""

    tier: Tier = "needs_admin"

    def signals(self) -> TriageSignals:
        return TriageSignals(
            category=self.category,
            language=self.language,
            confidence=self.confidence,
            asks_for_human=self.asks_for_human,
            mentions_money_or_commitment=self.mentions_money_or_commitment,
            classifier=self.classifier,
        )

    def with_tier(self, tier: Tier) -> TriageDecision:
        return replace(self, tier=tier)


class TriageClassifier(Protocol):
    """One call, four answers. Raises ``TriageUnavailable`` rather than guessing."""

    name: str

    def classify(self, message: IncomingMessage) -> TriageSignals: ...
