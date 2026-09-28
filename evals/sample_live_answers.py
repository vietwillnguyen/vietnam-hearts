#!/usr/bin/env python3
"""Judge last week's actually-sent FAQ answers, and store nothing.

    uv run python evals/sample_live_answers.py --days 7

The golden set says what the bot does on cases somebody thought of. This says
what it did to real questions, which is the only measurement that can notice a
question nobody anticipated being answered badly.

Two properties make this safe to run against production, and both are asserted
by ``tests/test_evals_runners.py``:

*It stores nothing.* The inbound text is fetched from Gmail by id at run time,
judged in memory, and never written anywhere. The database deliberately does
not hold inbound bodies, and this script is not the thing that starts.

*It only looks at what was sent.* Drafts are measured by the draft-acceptance
rate instead, because a draft the captain corrected before sending is not a
customer-visible answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.judge import judge_answer  # noqa: E402
from evals.metrics import GroundednessMetrics, format_table, percentage  # noqa: E402

# What a sampled FAQ answer is checked against. There is no per-case
# must_mention for real mail, so the judge grades groundedness and the
# prohibitions only - which is the half that matters for harm.
LIVE_PROHIBITIONS = (
    "a date, time, deadline or commitment not present in the context",
    "acceptance or rejection of an application",
    "an amount of money, a fee or a donation figure",
    "anyone's name, email address or phone number",
    "a promise about when someone will reply",
)


def sent_faq_rows(db, since: datetime, until: datetime):
    """Outbound FAQ answers the bot actually sent in the window.

    Deliberately narrow. ``action == "sent"`` excludes drafts, and the ``faq``
    kind excludes the sign-up template and the holding message, neither of
    which is generated and so neither of which can be ungrounded.
    """
    from app.models import Message

    return (
        db.query(Message)
        .filter(
            Message.direction == "outbound",
            Message.action == "sent",
            Message.category == "faq",
            Message.created_at >= since,
            Message.created_at < until,
        )
        .order_by(Message.created_at)
        .all()
    )


def inbound_for(db, row):
    """The inbound message the reply answered, from the same conversation."""
    from app.models import Message

    return (
        db.query(Message)
        .filter(
            Message.conversation_id == row.conversation_id,
            Message.direction == "inbound",
            Message.created_at <= row.created_at,
        )
        .order_by(Message.id.desc())
        .first()
    )


def fetch_question(adapter, provider_message_id: str) -> str:
    """The sender's text, fetched from Gmail at run time and never stored.

    Returns an empty string when the mail is gone, which happens: a thread can
    be deleted between the send and the sampling. A missing question is
    reported rather than guessed at.
    """
    from app.services.channels.mail_builder import extract_text

    # GmailAdapter.get_message is added alongside this script: the transport
    # could always fetch one message, but nothing above it had a reason to
    # until now.
    raw = adapter.get_message(provider_message_id)
    return extract_text(raw.payload) if raw is not None else ""


async def retrieve_context(bot_service, question: str) -> str:
    """The context the answer would have been grounded in.

    Re-retrieved rather than stored. The knowledge base may have changed since,
    which makes this an approximation - and an honest one, because storing the
    context at send time would mean keeping a copy of the question's
    neighbourhood for every reply.
    """
    chunks = await bot_service.knowledge_service.similarity_search(question, limit=3)
    return bot_service._build_context(chunks)


async def evaluate(
    db, adapter, bot_service, gemini_client, rows
) -> GroundednessMetrics:
    metrics = GroundednessMetrics()

    for row in rows:
        inbound = inbound_for(db, row)
        if inbound is None or not inbound.provider_message_id:
            metrics.record_refusal(str(row.id), "no inbound row to judge against")
            continue

        try:
            question = fetch_question(adapter, inbound.provider_message_id)
        except Exception as exc:
            metrics.record_refusal(
                inbound.provider_message_id, f"fetch failed: {type(exc).__name__}"
            )
            continue
        if not question:
            metrics.record_refusal(
                inbound.provider_message_id, "the original mail is no longer in Gmail"
            )
            continue

        try:
            context = await retrieve_context(bot_service, question)
        except Exception as exc:
            metrics.record_refusal(
                inbound.provider_message_id, f"retrieval failed: {type(exc).__name__}"
            )
            continue

        try:
            verdict = judge_answer(
                gemini_client,
                question=question,
                answer=row.text or "",
                context=context,
                must_mention=(),
                must_not_answer=LIVE_PROHIBITIONS,
            )
        except Exception as exc:
            metrics.record_refusal(
                inbound.provider_message_id, f"judge failed: {type(exc).__name__}"
            )
            continue

        metrics.record(inbound.provider_message_id, verdict.passes, verdict.why_not())

    return metrics


def report(metrics: GroundednessMetrics, days: int) -> str:
    lines = [
        f"Sent FAQ answers judged over the last {days} day(s)",
        "",
        format_table(
            [
                ("sent and judged", str(metrics.judged)),
                ("grounded and clean", str(metrics.grounded)),
                ("problems found", str(len(metrics.ungrounded))),
                ("could not be judged", str(metrics.refused)),
            ],
            ("", "count"),
        ),
        "",
        f"Groundedness: {percentage(metrics.correctness)}",
    ]
    if metrics.ungrounded:
        lines += ["", "Answers worth reading:"]
        lines += [f"  - {item}" for item in metrics.ungrounded]
    if metrics.failures:
        lines += ["", "Not judged:"]
        lines += [f"  - {item}" for item in metrics.failures]
    lines += [
        "",
        "Nothing above was written to the database. The inbound text was "
        "fetched from Gmail for this run and discarded.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    until = datetime.now(UTC)
    since = until - timedelta(days=args.days)

    from app.database import SessionLocal
    from app.dependencies.services import get_bot_service
    from app.services.channels.gmail import GmailAdapter
    from app.services.email_bot.factory import Credentials

    credentials = Credentials.from_env()
    credentials.require_gmail()

    from app.services.channels.gmail_transport import GmailTransport

    adapter = GmailAdapter(
        GmailTransport.from_credentials(
            credentials.gmail_client_id,
            credentials.gmail_client_secret,
            credentials.gmail_refresh_token,
        )
    )
    bot_service = get_bot_service()
    gemini_client = bot_service.knowledge_service.gemini_client
    if gemini_client is None:
        raise SystemExit("No Gemini client; set GEMINI_API_KEY")

    db = SessionLocal()
    try:
        rows = sent_faq_rows(db, since, until)
        metrics = asyncio.run(evaluate(db, adapter, bot_service, gemini_client, rows))
    finally:
        db.close()

    print(report(metrics, args.days))

    if args.json:
        print(
            json.dumps(
                {
                    "days": args.days,
                    "judged": metrics.judged,
                    "grounded": metrics.grounded,
                    "refused": metrics.refused,
                    "correctness": metrics.correctness,
                    "problems": metrics.ungrounded,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
