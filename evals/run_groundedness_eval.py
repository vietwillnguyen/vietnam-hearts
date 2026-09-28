#!/usr/bin/env python3
"""Answer every answerable golden case for real, and judge the answers.

    uv run python evals/run_groundedness_eval.py

Runs ``BotService.chat()`` against the live knowledge base with the email
prompt, then judges each reply against the case's ``must_mention`` facts and
``must_not_answer`` prohibitions. The design's gate is at least 90 percent
correctness on ``auto_answer`` cases.

Refusals count against the score rather than being excluded. A bot that refused
everything but one easy question would otherwise report 100 percent, which is
the opposite of what this measures.

Consumes real API calls, against the real knowledge base. Not in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.bot_service import (  # noqa: E402
    GenerationUnavailable,
    NoRelevantContext,
)
from app.services.knowledge_service import EmbeddingsUnavailable  # noqa: E402
from evals.judge import judge_answer  # noqa: E402
from evals.loader import answerable_cases, load_golden_set  # noqa: E402
from evals.metrics import GroundednessMetrics, format_table, percentage  # noqa: E402


def build_bot_service():
    from app.dependencies.services import get_bot_service

    return get_bot_service()


async def answer_case(bot_service, case) -> tuple[str, str, float]:
    """The reply, the context it was grounded in, and the retrieval similarity.

    The context is fetched a second time rather than plumbed out of ``chat()``:
    the judge needs it, and widening ``chat()``'s return value just for an eval
    would change the production signature for the sake of a script.
    """
    result = await bot_service.chat(case.text, channel="email", language=case.language)

    chunks = await bot_service.knowledge_service.similarity_search(case.text, limit=3)
    context = bot_service._build_context(chunks)
    return result["response"], context, float(result.get("confidence") or 0.0)


async def evaluate(bot_service, gemini_client, cases) -> GroundednessMetrics:
    metrics = GroundednessMetrics()

    for case in cases:
        try:
            answer, context, confidence = await answer_case(bot_service, case)
        except NoRelevantContext as exc:
            # Safe, but a miss: the sender got a holding message for a question
            # the knowledge base was supposed to cover.
            metrics.record_refusal(case.id, f"refused: {exc}")
            continue
        except (EmbeddingsUnavailable, GenerationUnavailable) as exc:
            metrics.record_refusal(case.id, f"unavailable: {type(exc).__name__}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--only", default=None, help="Comma-separated case ids.")
    args = parser.parse_args(argv)

    cases = answerable_cases(load_golden_set())
    if args.only:
        wanted = {case_id.strip() for case_id in args.only.split(",")}
        cases = tuple(case for case in cases if case.id in wanted)

    bot_service = build_bot_service()
    gemini_client = bot_service.knowledge_service.gemini_client
    if gemini_client is None:
        raise SystemExit("No Gemini client; set GEMINI_API_KEY")

    metrics = asyncio.run(evaluate(bot_service, gemini_client, cases))
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
                    "passes_gate": metrics.passes_gate(args.threshold),
                },
                indent=2,
            )
        )

    return 0 if metrics.passes_gate(args.threshold) else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
