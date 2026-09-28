#!/usr/bin/env python3
"""Run a classifier over the golden set and report whether it may decide.

    uv run python evals/run_triage_eval.py --classifier jev
    uv run python evals/run_triage_eval.py --classifier litellm --model gemini/gemini-3.5-flash-lite

Exits non-zero when executive recall is below 100 percent, so this can gate a
decision rather than merely inform one. That single number is what the design
makes the deciding classifier's licence: a missed safeguarding mail is not
something a good average elsewhere compensates for.

Consumes real API calls. Not in CI, on purpose - see evals/README.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.triage.policy import decide  # noqa: E402
from app.services.triage.protocol import (  # noqa: E402
    CATEGORIES,
    TriageUnavailable,
)
from evals.loader import (  # noqa: E402
    GOLDEN_THRESHOLD,
    GoldenCase,
    as_incoming,
    load_golden_set,
)
from evals.metrics import (  # noqa: E402
    TriageMetrics,
    format_table,
    percentage,
)


def build_classifier(name: str, model: str | None):
    """The real classifier, constructed from the environment.

    Deliberately the same classes the pipeline uses rather than a thin
    re-implementation: an eval that measured a different code path would
    certify something that is not what production runs.
    """
    import os

    if name == "jev":
        from app.services.triage.jev import DEFAULT_MODEL, JevClassifier

        key = os.getenv("TYPESAFE_API_KEY")
        if not key:
            raise SystemExit("TYPESAFE_API_KEY is not set")
        return JevClassifier.from_api_key(key, model or DEFAULT_MODEL)

    if name == "litellm":
        from app.services.triage.litellm import DEFAULT_MODEL, LiteLLMClassifier

        return LiteLLMClassifier(model=model or DEFAULT_MODEL)

    raise SystemExit(f"unknown classifier {name!r}; expected jev or litellm")


def evaluate(
    classifier, cases: tuple[GoldenCase, ...], threshold: float
) -> TriageMetrics:
    metrics = TriageMetrics()

    for case in cases:
        message = as_incoming(case)
        try:
            signals = classifier.classify(message)
        except TriageUnavailable as exc:
            metrics.record_error(case.id, str(exc))
            if case.is_executive:
                metrics.missed_executive.append(
                    f"{case.id}: classifier unavailable, so recall is unproven"
                )
            continue

        decision = decide(signals, threshold)
        metrics.record(
            case_id=case.id,
            expected_category=case.expected_category,
            actual_category=decision.category,
            expected_tier=case.expected_tier,
            actual_tier=decision.tier,
            expected_language=case.language,
            actual_language=decision.language,
        )

    return metrics


def report(metrics: TriageMetrics, classifier_name: str, threshold: float) -> str:
    lines = [
        f"Classifier: {classifier_name}",
        f"Confidence threshold: {threshold}",
        "",
        "Per-category precision and recall",
    ]

    rows = []
    for category in CATEGORIES:
        counts = metrics.per_category.get(category)
        if counts is None:
            continue
        rows.append(
            (
                category,
                str(counts.support),
                percentage(counts.precision),
                percentage(counts.recall),
            )
        )
    lines.append(format_table(rows, ("category", "cases", "precision", "recall")))

    lines += ["", "Per-tier recall"]
    tier_rows = [
        (tier, str(counts.support), percentage(counts.recall))
        for tier, counts in sorted(metrics.per_tier.items())
    ]
    lines.append(format_table(tier_rows, ("tier", "cases", "recall")))

    lines += [
        "",
        f"Category accuracy:  {percentage(metrics.category_accuracy)}",
        f"Language accuracy:  {percentage(metrics.language_accuracy)}",
        f"Executive recall:   {percentage(metrics.executive_recall)}  "
        "(the gate: must be 100.0%)",
    ]

    if metrics.confusion:
        lines += ["", "Confusion (expected -> actual, mistakes only)"]
        confusion_rows = [
            (expected, actual, str(count))
            for (expected, actual), count in sorted(
                metrics.confusion.items(), key=lambda item: -item[1]
            )
            if expected != actual
        ]
        if confusion_rows:
            lines.append(format_table(confusion_rows, ("expected", "actual", "count")))
        else:
            lines.append("  none")

    if metrics.missed_executive:
        lines += ["", "MISSED ESCALATIONS - this classifier may not decide:"]
        lines += [f"  - {miss}" for miss in metrics.missed_executive]

    if metrics.errors:
        lines += ["", f"Unclassifiable cases ({len(metrics.errors)}):"]
        lines += [f"  - {error}" for error in metrics.errors]

    verdict = "PASS" if metrics.passes_executive_gate() else "FAIL"
    lines += ["", f"Executive recall gate: {verdict}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classifier", default="jev", choices=["jev", "litellm"])
    parser.add_argument("--model", default=None, help="Model override.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=GOLDEN_THRESHOLD,
        help="Confidence threshold to derive tiers at.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON as well.")
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated case ids, for re-running a disagreement.",
    )
    args = parser.parse_args(argv)

    cases = load_golden_set()
    if args.only:
        wanted = {case_id.strip() for case_id in args.only.split(",")}
        cases = tuple(case for case in cases if case.id in wanted)
        if not cases:
            raise SystemExit("no matching cases")

    classifier = build_classifier(args.classifier, args.model)
    metrics = evaluate(classifier, cases, args.threshold)

    print(report(metrics, getattr(classifier, "name", args.classifier), args.threshold))

    if args.json:
        print(
            json.dumps(
                {
                    "classifier": getattr(classifier, "name", args.classifier),
                    "cases": len(cases),
                    "category_accuracy": metrics.category_accuracy,
                    "language_accuracy": metrics.language_accuracy,
                    "executive_recall": metrics.executive_recall,
                    "missed_executive": metrics.missed_executive,
                    "errors": metrics.errors,
                    "passes_gate": metrics.passes_executive_gate(),
                },
                indent=2,
            )
        )

    return 0 if metrics.passes_executive_gate() else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
