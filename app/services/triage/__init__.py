"""Triage: what kind of mail is this, and who should answer it.

Two classifier implementations behind one protocol, and a pure policy module
that turns their answer into a tier. The split is the point: which model
decides is a setting, the tier is code, and neither can be changed by the
model's own output.
"""

from app.services.triage.policy import (
    CATEGORY_TIERS,
    EXECUTIVE_CATEGORIES,
    decide,
    derive_tier,
)
from app.services.triage.protocol import (
    Category,
    Language,
    Tier,
    TriageClassifier,
    TriageDecision,
    TriageSignals,
    TriageUnavailable,
)

__all__ = [
    "CATEGORY_TIERS",
    "EXECUTIVE_CATEGORIES",
    "Category",
    "Language",
    "Tier",
    "TriageClassifier",
    "TriageDecision",
    "TriageSignals",
    "TriageUnavailable",
    "decide",
    "derive_tier",
]
