"""Metric arithmetic, kept separate from the runners that make API calls.

The triage and groundedness counts, the percentages and the table are
inbox-core's (``inbox_core.evals.metrics``), re-exported here so every runner
reads one module. ``AgreementMetrics`` is this app's: it scores the deciding
classifier against the shadow on real traffic, which the core does not report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from inbox_core.evals.metrics import (
    ClassCounts,
    GroundednessMetrics,
    TriageMetrics,
    format_table,
    percentage,
)

__all__ = [
    "AgreementMetrics",
    "ClassCounts",
    "GroundednessMetrics",
    "TriageMetrics",
    "format_table",
    "percentage",
]


@dataclass
class AgreementMetrics:
    """How often the deciding and shadow classifiers said the same thing."""

    per_field: dict[str, ClassCounts] = field(default_factory=dict)
    compared: int = 0
    agreed: dict[str, int] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)

    def record(
        self, case_id: str, agreement: dict[str, bool], detail: str = ""
    ) -> None:
        if not agreement:
            return
        self.compared += 1
        for name, agrees in agreement.items():
            self.agreed[name] = self.agreed.get(name, 0) + (1 if agrees else 0)
        if not all(agreement.values()):
            differing = sorted(name for name, agrees in agreement.items() if not agrees)
            self.disagreements.append(
                f"{case_id}: {', '.join(differing)}"
                + (f" ({detail})" if detail else "")
            )

    def rate(self, field_name: str) -> float:
        if self.compared == 0:
            return 1.0
        return self.agreed.get(field_name, 0) / self.compared

    @property
    def overall(self) -> float:
        """Cases where the two agreed on every field."""
        if self.compared == 0:
            return 1.0
        return (self.compared - len(self.disagreements)) / self.compared
