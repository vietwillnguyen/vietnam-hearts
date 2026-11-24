#!/usr/bin/env python3
"""Answer every answerable golden case for real, and judge the answers.

    uv run python evals/run_groundedness_eval.py

Answers each case with the bot's own FAQ handler - inbox-core's
``GroundedAnswer`` over the live knowledge base and Gemini under the email
prompt - then judges each reply against the case's ``must_mention`` facts and
``must_not_answer`` prohibitions. The design's gate is at least 90 percent
correctness on ``auto_answer`` cases.

The handler runs with no similarity floor, so every case gets an answer to
judge and ``calibrate_thresholds.py --answers`` can say where
``ANSWER_THRESHOLD`` belongs. In production the floor is that threshold.

Refusals count against the score rather than being excluded. A bot that refused
everything but one easy question would otherwise report 100 percent, which is
the opposite of what this measures.

Consumes real API calls, against the real knowledge base. Not in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inbox_core import GroundedAnswer, HandlerRequest  # noqa: E402

from app.services.email_bot.answers import (  # noqa: E402
    ANSWER_PASSAGES,
    AnswerOutages,
    KnowledgeBaseGenerator,
    KnowledgeBaseRetriever,
)
from app.services.email_bot.client import client_config  # noqa: E402
from evals.judge import judge_answer  # noqa: E402
from evals.loader import (  # noqa: E402
    GOLDEN_THRESHOLD,
    answerable_cases,
    as_incoming,
    decide_at,
    load_golden_set,
)
from evals.metrics import GroundednessMetrics, format_table, percentage  # noqa: E402


def build_bot_service():
    from app.dependencies.services import get_bot_service

    return get_bot_service()


class AnswerRefused(Exception):
    """The handler handed the case to a person instead of answering it."""

    def __init__(self, reason: str, outage: str | None) -> None:
        super().__init__(reason)
        self.outage = outage


class _RecordingGenerator(KnowledgeBaseGenerator):
    """The production generator, keeping the passages it was given for the judge."""

    passages: tuple = ()

    def generate(self, request):
        self.passages = request.passages
        return super().generate(request)


def answer_case(bot_service, case) -> tuple[str, str, float]:
    """The reply, the context it was grounded in, and the best similarity."""
    outages = AnswerOutages()
    generator = _RecordingGenerator(bot_service, outages=outages)
    handler = GroundedAnswer(
        KnowledgeBaseRetriever(bot_service.knowledge_service, outages=outages),
        generator,
        min_score=0.0,
        limit=ANSWER_PASSAGES,
        name="faq",
    )
    request = HandlerRequest(
        as_incoming(case),
        decide_at(case.expected_signals, GOLDEN_THRESHOLD),
        client_config(),
    )
    result = handler.handle(request)
    if result.outcome == "hand_off":
        raise AnswerRefused(result.reason, outages.last)

    context = bot_service._build_context(
        [{"content": passage.entry.text} for passage in generator.passages]
    )
    similarity = max((source.score for source in result.sources), default=0.0)
    return result.text, context, similarity


def evaluate(bot_service, gemini_client, cases) -> GroundednessMetrics:
    metrics = GroundednessMetrics()

    for case in cases:
        try:
            answer, context, confidence = answer_case(bot_service, case)
        except AnswerRefused as exc:
            # Safe, but a miss: the sender got a holding message for a question
            # the knowledge base was supposed to cover.
            if exc.outage:
                metrics.record_refusal(case.id, f"unavailable: {exc.outage}")
            else:
                metrics.record_refusal(case.id, f"refused: {exc}")
            continue

        try:
            verdict = judge_answer(
                gemini_client,
                question=case.text,
                answer=answer,
                context=context,
                must_mention=case.must_mention,
                must_not_answer=case.must_not_answer,
            )
        except Exception as exc:
            metrics.record_refusal(case.id, f"judge failed: {type(exc).__name__}")
            continue

        metrics.record(
            case.id,
            verdict.passes,
            f"{verdict.why_not()} (similarity {confidence:.2f})",
            similarity=confidence,
        )

    return metrics


def report(metrics: GroundednessMetrics, threshold: float) -> str:
    lines = [
        format_table(
            [
                ("answerable cases", str(metrics.total)),
                ("answered and correct", str(metrics.grounded)),
                ("answered but wrong", str(len(metrics.ungrounded))),
                ("refused or unavailable", str(metrics.refused)),
            ],
            ("", "count"),
        ),
        "",
        f"Correctness: {percentage(metrics.correctness)}  "
        f"(gate: {percentage(threshold)})",
    ]

    if metrics.ungrounded:
        lines += ["", "Answers that did not pass:"]
        lines += [f"  - {item}" for item in metrics.ungrounded]

    if metrics.failures:
        lines += ["", "Cases with no answer to judge:"]
        lines += [f"  - {item}" for item in metrics.failures]

    lines += [
        "",
        f"Groundedness gate: {'PASS' if metrics.passes_gate(threshold) else 'FAIL'}",
    ]
    return "\n".join(lines)


def answers_payload(metrics: GroundednessMetrics) -> dict[str, list[dict]]:
    """Per-answer similarity and verdict, in the shape calibrate_thresholds reads."""
    return {
        "answers": [
            {"id": case_id, "similarity": similarity, "passed": passed}
            for case_id, similarity, passed in metrics.answers
        ]
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--only", default=None, help="Comma-separated case ids.")
    parser.add_argument(
        "--answers-out",
        type=Path,
        default=None,
        help="Write each judged answer's similarity and verdict here, for "
        "calibrate_thresholds.py --answers.",
    )
    args = parser.parse_args(argv)

    cases = answerable_cases(load_golden_set())
    if args.only:
        wanted = {case_id.strip() for case_id in args.only.split(",")}
        cases = tuple(case for case in cases if case.id in wanted)

    bot_service = build_bot_service()
    gemini_client = bot_service.knowledge_service.gemini_client
    if gemini_client is None:
        raise SystemExit("No Gemini client; set GEMINI_API_KEY")

    metrics = evaluate(bot_service, gemini_client, cases)
    print(report(metrics, args.threshold))

    if args.json:
        print(
            json.dumps(
                {
                    "cases": metrics.total,
                    "grounded": metrics.grounded,
                    "refused": metrics.refused,
                    "correctness": metrics.correctness,
                    "ungrounded": metrics.ungrounded,
                    "failures": metrics.failures,
                    "answers": answers_payload(metrics)["answers"],
                    "passes_gate": metrics.passes_gate(args.threshold),
                },
                indent=2,
            )
        )

    if args.answers_out:
        args.answers_out.write_text(json.dumps(answers_payload(metrics), indent=2))

    return 0 if metrics.passes_gate(args.threshold) else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
