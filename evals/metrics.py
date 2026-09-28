"""Metric arithmetic, kept separate from the runners that make API calls.

Split out for one reason: this is the part that can be tested in CI. A runner
that computed its own percentages inline would only ever be exercised by a run
that costs money, which is how a confidently wrong recall number gets believed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ClassCounts:
    """True positives, false positives and false negatives for one class."""

    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0

    @property
    def support(self) -> int:
        """How many cases genuinely belong to this class."""
        return self.true_positive + self.false_negative

    @property
    def precision(self) -> float:
        """Of what we called this class, how much was. 1.0 when we called none.

        Returning 1.0 rather than 0.0 for an empty denominator is the
        convention that keeps a class nobody predicted from dragging a macro
        average down as though it had been predicted wrongly.
        """
        predicted = self.true_positive + self.false_positive
        return 1.0 if predicted == 0 else self.true_positive / predicted

    @property
    def recall(self) -> float:
        """Of what genuinely was this class, how much we caught."""
        return 1.0 if self.support == 0 else self.true_positive / self.support


@dataclass
class TriageMetrics:
    """Per-category precision and recall, plus the two gate numbers."""

    per_category: dict[str, ClassCounts] = field(default_factory=dict)
    per_tier: dict[str, ClassCounts] = field(default_factory=dict)
    language_correct: int = 0
    language_total: int = 0
    confusion: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    missed_executive: list[str] = field(default_factory=list)

    def record(
        self,
        case_id: str,
        expected_category: str,
        actual_category: str,
        expected_tier: str,
        actual_tier: str,
        expected_language: str,
        actual_language: str,
    ) -> None:
        self._record_class(self.per_category, expected_category, actual_category)
        self._record_class(self.per_tier, expected_tier, actual_tier)

        self.language_total += 1
        if expected_language == actual_language:
            self.language_correct += 1

        self.confusion[(expected_category, actual_category)] += 1

        # The gate. An executive case that came out as anything else is a
        # missed escalation, which is the one failure the design refuses to
        # trade away for any amount of accuracy elsewhere.
        if expected_tier == "needs_executive" and actual_tier != "needs_executive":
            self.missed_executive.append(
                f"{case_id}: expected needs_executive, got {actual_tier} "
                f"(category {expected_category} -> {actual_category})"
            )

    def record_error(self, case_id: str, reason: str) -> None:
        """A case the classifier could not answer at all.

        Counted as a missed escalation when the case was an executive one: an
        unavailable classifier is not an excuse, because in production that
        mail would have reached a person only through the fail-closed path,
        which is not what this gate is measuring.
        """
        self.errors.append(f"{case_id}: {reason}")

    @staticmethod
    def _record_class(
        table: dict[str, ClassCounts], expected: str, actual: str
    ) -> None:
        table.setdefault(expected, ClassCounts())
        table.setdefault(actual, ClassCounts())
        if expected == actual:
            table[expected].true_positive += 1
        else:
            table[expected].false_negative += 1
            table[actual].false_positive += 1

    @property
    def executive_recall(self) -> float:
        """The gate: of the mail that had to reach the captain, how much did."""
        counts = self.per_tier.get("needs_executive")
        return 1.0 if counts is None else counts.recall

    @property
    def language_accuracy(self) -> float:
        if self.language_total == 0:
            return 1.0
        return self.language_correct / self.language_total

    @property
    def category_accuracy(self) -> float:
        total = sum(counts.support for counts in self.per_category.values())
        if total == 0:
            return 1.0
        correct = sum(counts.true_positive for counts in self.per_category.values())
        return correct / total

    def passes_executive_gate(self) -> bool:
        """100 percent recall on needs_executive, and no case left unclassified.

        The error check is part of the gate rather than a footnote: a run that
        could not classify a third of the safeguarding cases has not
        demonstrated anything about recall.
        """
        return (
            self.executive_recall == 1.0
            and not self.missed_executive
            and not self.errors
        )


@dataclass
class GroundednessMetrics:
    """How often an answerable case was answered, and answered from the facts."""

    judged: int = 0
    grounded: int = 0
    refused: int = 0
    failures: list[str] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)

    def record(self, case_id: str, is_grounded: bool, reason: str = "") -> None:
        self.judged += 1
        if is_grounded:
            self.grounded += 1
        else:
            self.ungrounded.append(f"{case_id}: {reason}" if reason else case_id)

    def record_refusal(self, case_id: str, reason: str) -> None:
        """The bot declined to answer an answerable case.

        Counted separately from an ungrounded answer, and deliberately not as a
        pass. A refusal is safe but it is also a miss: the sender got a holding
        message for a question the knowledge base was supposed to cover.
        """
        self.refused += 1
        self.failures.append(f"{case_id}: {reason}")

    @property
    def total(self) -> int:
        return self.judged + self.refused

    @property
    def correctness(self) -> float:
        """Grounded answers over every answerable case, refusals included.

        Refusals are in the denominator on purpose. Scoring only the cases the
        bot chose to answer would let a bot that refused everything but one
        easy question report 100 percent.
        """
        return 1.0 if self.total == 0 else self.grounded / self.total

    def passes_gate(self, threshold: float = 0.9) -> bool:
        return self.correctness >= threshold


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


def percentage(value: float) -> str:
    return f"{value * 100:.1f}%"


def format_table(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> str:
    """A plain-text table. No dependency, because this prints to a terminal."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def line(cells) -> str:
        return "  ".join(
            str(cell).ljust(widths[index]) for index, cell in enumerate(cells)
        )

    separator = "  ".join("-" * width for width in widths)
    return "\n".join([line(headers), separator, *(line(row) for row in rows)])
