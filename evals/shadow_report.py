#!/usr/bin/env python3
"""Where the deciding and shadow classifiers disagreed, over real inbox traffic.

    uv run python evals/shadow_report.py --since 2026-09-15 --until 2026-09-29

Reads the ``messages`` rows the pipeline already wrote, so it costs nothing and
reports on the mail the bot actually saw rather than on the golden set. That is
the point of running a shadow at all: the golden set says what the classifiers
do on cases someone thought of, and this says what they do on the inbox.

Disagreements are listed by Gmail id and category only. The rows hold no
subject, body or address, so there is nothing else to print even if it were
wanted.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.metrics import AgreementMetrics, format_table, percentage  # noqa: E402

FIELDS = ("category", "language", "asks_for_human", "mentions_money_or_commitment")


def load_rows(db, since: datetime | None, until: datetime | None):
    from app.models import Message

    query = db.query(Message).filter(
        Message.direction == "inbound", Message.triage_shadow.isnot(None)
    )
    if since is not None:
        query = query.filter(Message.created_at >= since)
    if until is not None:
        query = query.filter(Message.created_at <= until)
    return query.order_by(Message.created_at).all()


def compare(rows) -> AgreementMetrics:
    metrics = AgreementMetrics()

    for row in rows:
        shadow = row.triage_shadow or {}
        if not isinstance(shadow, dict) or "category" not in shadow:
            continue

        # The decided values live in their own columns; the shadow's are in the
        # JSON blob. Only the fields both actually report are compared -
        # confidence is deliberately not one of them, because Jev's
        # probabilities are calibrated and a general model's self-report is
        # not, so comparing the two numbers would manufacture disagreements.
        agreement = {
            "category": row.category == shadow.get("category"),
            "language": row.language == shadow.get("language"),
        }
        metrics.record(
            row.provider_message_id or str(row.id),
            agreement,
            detail=f"decided {row.category}, shadow {shadow.get('category')}",
        )

    return metrics


def report(metrics: AgreementMetrics, decided_name: str, shadow_name: str) -> str:
    lines = [
        f"Decided by: {decided_name}",
        f"Shadowed by: {shadow_name}",
        f"Messages compared: {metrics.compared}",
        "",
    ]
    if metrics.compared == 0:
        lines.append("Nothing to compare in this window.")
        return "\n".join(lines)

    lines.append(
        format_table(
            [
                (name, percentage(metrics.rate(name)))
                for name in ("category", "language")
            ],
            ("field", "agreement"),
        )
    )
    lines += ["", f"Full agreement: {percentage(metrics.overall)}"]

    if metrics.disagreements:
        lines += ["", f"Disagreements ({len(metrics.disagreements)}):"]
        lines += [f"  - {item}" for item in metrics.disagreements]

    return "\n".join(lines)


def classifier_names(rows) -> tuple[str, str]:
    decided = {row.classifier for row in rows if row.classifier}
    shadow = {
        (row.triage_shadow or {}).get("classifier")
        for row in rows
        if isinstance(row.triage_shadow, dict)
    }
    shadow.discard(None)
    return (
        ", ".join(sorted(decided)) or "unknown",
        ", ".join(sorted(shadow)) or "none",
    )


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None, help="ISO date, inclusive.")
    parser.add_argument("--until", default=None, help="ISO date, inclusive.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        rows = load_rows(db, parse_date(args.since), parse_date(args.until))
        metrics = compare(rows)
        decided_name, shadow_name = classifier_names(rows)
    finally:
        db.close()

    print(report(metrics, decided_name, shadow_name))

    if args.json:
        print(
            json.dumps(
                {
                    "compared": metrics.compared,
                    "category_agreement": metrics.rate("category"),
                    "language_agreement": metrics.rate("language"),
                    "overall": metrics.overall,
                    "disagreements": metrics.disagreements,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
