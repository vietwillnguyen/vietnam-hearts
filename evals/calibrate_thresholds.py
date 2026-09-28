#!/usr/bin/env python3
"""Sweep the two thresholds and print the trade-off, so they are chosen not guessed.

    uv run python evals/calibrate_thresholds.py --classifier jev

``TRIAGE_CONFIDENCE_THRESHOLD`` and ``ANSWER_THRESHOLD`` both trade reach
against safety, in opposite currencies. Raising the triage threshold escalates
more mail, which costs the captain's time; lowering it answers more, which
risks answering the wrong thing. Neither has a defensible default, which is why
the design ships 0.6 and 0.5 as starting points and makes both settings.

Classifies each case once and re-derives tiers at every threshold, so a sweep
of twenty values costs one pass of API calls rather than twenty.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.triage.policy import decide  # noqa: E402
from app.services.triage.protocol import TriageSignals, TriageUnavailable  # noqa: E402
from evals.loader import GoldenCase, as_incoming, load_golden_set  # noqa: E402
from evals.metrics import TriageMetrics, format_table, percentage  # noqa: E402

DEFAULT_SWEEP = (0.0, 0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9, 0.95)


def classify_once(
    classifier, cases: tuple[GoldenCase, ...]
) -> tuple[list[tuple[GoldenCase, TriageSignals]], list[str]]:
    """One pass of API calls, reused at every threshold."""
    answered: list[tuple[GoldenCase, TriageSignals]] = []
    errors: list[str] = []

    for case in cases:
        try:
            answered.append((case, classifier.classify(as_incoming(case))))
        except TriageUnavailable as exc:
            errors.append(f"{case.id}: {exc}")
    return answered, errors


def sweep_triage_threshold(
    answered: list[tuple[GoldenCase, TriageSignals]], thresholds=DEFAULT_SWEEP
) -> list[dict[str, float]]:
    """What each confidence threshold would have done to the same answers."""
    rows: list[dict[str, float]] = []

    for threshold in thresholds:
        metrics = TriageMetrics()
        answered_count = 0

        for case, signals in answered:
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
            if decision.tier == "auto_answer":
                answered_count += 1

        total = len(answered) or 1
        rows.append(
            {
                "threshold": threshold,
                "executive_recall": metrics.executive_recall,
                "category_accuracy": metrics.category_accuracy,
                # The cost side of the trade: how much mail the bot answers at
                # all. A threshold with perfect recall that answers nothing has
                # not solved the problem it was built for.
                "answered_share": answered_count / total,
                "missed_executive": len(metrics.missed_executive),
            }
        )
    return rows


def sweep_answer_threshold(
    similarities: list[tuple[str, float, bool]], thresholds=DEFAULT_SWEEP
) -> list[dict[str, float]]:
    """What each retrieval-similarity gate would have let through.

    ``similarities`` is ``(case_id, top_similarity, was_the_answer_correct)``,
    which the groundedness run produces. Correctness has to come from the judge
    rather than from the similarity itself, or the sweep would just be
    measuring the number against itself.
    """
    rows: list[dict[str, float]] = []

    for threshold in thresholds:
        answered = [item for item in similarities if item[1] >= threshold]
        correct = [item for item in answered if item[2]]
        rows.append(
            {
                "threshold": threshold,
                "answered": len(answered),
                "answered_share": len(answered) / (len(similarities) or 1),
                "precision": len(correct) / (len(answered) or 1),
                "wrong_answers": len(answered) - len(correct),
            }
        )
    return rows


def report_triage(rows: list[dict[str, float]]) -> str:
    table = format_table(
        [
            (
                f"{row['threshold']:.2f}",
                percentage(row["executive_recall"]),
                percentage(row["category_accuracy"]),
                percentage(row["answered_share"]),
                str(int(row["missed_executive"])),
            )
            for row in rows
        ],
        ("threshold", "exec recall", "accuracy", "answered", "missed exec"),
    )
    return (
        "TRIAGE_CONFIDENCE_THRESHOLD sweep\n"
        "Pick the highest 'answered' share among the rows with 100% exec recall\n"
        "and zero missed escalations.\n\n" + table
    )


def report_answer(rows: list[dict[str, float]]) -> str:
    table = format_table(
        [
            (
                f"{row['threshold']:.2f}",
                str(int(row["answered"])),
                percentage(row["answered_share"]),
                percentage(row["precision"]),
                str(int(row["wrong_answers"])),
            )
            for row in rows
        ],
        ("threshold", "answered", "share", "precision", "wrong"),
    )
    return (
        "ANSWER_THRESHOLD sweep\n"
        "Every 'wrong' is a wrong answer sent to a member of the public, so read\n"
        "this column first and the 'answered' share second.\n\n" + table
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classifier", default="jev", choices=["jev", "litellm"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from evals.run_triage_eval import build_classifier

    cases = load_golden_set()
    classifier = build_classifier(args.classifier, args.model)
    answered, errors = classify_once(classifier, cases)

    rows = sweep_triage_threshold(answered)
    print(report_triage(rows))

    if errors:
        print(f"\n{len(errors)} case(s) could not be classified:")
        for error in errors:
            print(f"  - {error}")

    print(
        "\nANSWER_THRESHOLD needs the judged similarities from "
        "run_groundedness_eval.py; run that and pass its JSON to "
        "sweep_answer_threshold()."
    )

    if args.json:
        print(json.dumps({"triage_sweep": rows, "errors": errors}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
