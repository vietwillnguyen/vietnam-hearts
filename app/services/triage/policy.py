"""The category-to-tier table and the tier rules. Pure, and the model never sees it.

Four rules, applied in this order, and the order is the whole design:

1. An executive category, or any mention of money or a commitment, is
   ``needs_executive``. First, so nothing below can talk it down.
2. Otherwise ``automated`` is ``skip`` - whatever the confidence, and even if
   the writer "asked for a human", because a vacation responder saying "I am
   away, contact my colleague" would otherwise earn a holding message and start
   a loop.
3. Otherwise an unknown language, an explicit ask for a person, or a confidence
   below the threshold is ``needs_admin``. This is the fail-closed rule.
4. Otherwise the category's own tier.

Rule 1 before rule 3 is the "category gate before confidence gate" the design
calls for: a low-confidence safeguarding guess still escalates to the captain.
Rule 2 before rule 3 is what keeps the second line of defence behind the header
guards from replying.
"""

from __future__ import annotations

from app.services.triage.protocol import Category, Tier, TriageDecision, TriageSignals

# Money, commitments, partnerships, media, brand, safeguarding, and anything
# that reads as accepting or rejecting an applicant. The captain decides all of
# these; the bot's only job is to notice and hand over.
EXECUTIVE_CATEGORIES: frozenset[Category] = frozenset(
    {
        "sponsorship",
        "donation",
        "partnership",
        "press",
        "safeguarding_legal",
        "acceptance",
    }
)

CATEGORY_TIERS: dict[Category, Tier] = {
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

ESCALATING_TIERS: frozenset[Tier] = frozenset({"needs_admin", "needs_executive"})


def derive_tier(signals: TriageSignals, confidence_threshold: float) -> Tier:
    """Which tier handles this mail. Never chosen by a model."""
    if signals.category in EXECUTIVE_CATEGORIES or signals.mentions_money_or_commitment:
        return "needs_executive"

    if signals.category == "automated":
        return "skip"

    if (
        signals.language not in ("en", "vi")
        or signals.asks_for_human
        or signals.confidence < confidence_threshold
    ):
        return "needs_admin"

    return CATEGORY_TIERS.get(signals.category, "needs_admin")


def decide(signals: TriageSignals, confidence_threshold: float) -> TriageDecision:
    """``signals`` plus its derived tier, as one immutable record."""
    return TriageDecision(
        category=signals.category,
        language=signals.language,
        confidence=signals.confidence,
        asks_for_human=signals.asks_for_human,
        mentions_money_or_commitment=signals.mentions_money_or_commitment,
        classifier=signals.classifier,
        tier=derive_tier(signals, confidence_threshold),
    )


def escalation_reason(signals: TriageSignals, confidence_threshold: float) -> str:
    """One short phrase saying why this escalated, for the forward and Discord.

    Categories, tiers and numbers only. This string is written to the audit
    row, posted to Discord and put in the forward, so it must not be able to
    carry any of the mail itself.
    """
    if signals.category in EXECUTIVE_CATEGORIES:
        return f"executive category {signals.category}"
    if signals.mentions_money_or_commitment:
        return "mentions money or a commitment"
    if signals.asks_for_human:
        return "the sender asked for a person"
    if signals.language not in ("en", "vi"):
        return f"unsupported language {signals.language}"
    if signals.confidence < confidence_threshold:
        return (
            f"confidence {signals.confidence:.2f} below threshold "
            f"{confidence_threshold:.2f}"
        )
    return f"category {signals.category} is handled by a person"
