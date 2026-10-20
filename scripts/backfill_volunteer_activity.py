#!/usr/bin/env python
"""
Backfill volunteers.last_active_at from the schedule spreadsheet's history.

The weekly reminder now only goes to volunteers active within
REMINDER_ACTIVE_WEEKS, and last_active_at starts out NULL for everyone. Until
this has run, recency falls back to each volunteer's sign-up date, so a
long-serving volunteer who joined two years ago reads as lapsed. Run this once
after deploying, before raising REMINDER_ACTIVE_WEEKS off 0.

Run read-only first (the default) and read what it says it would write:

    python scripts/backfill_volunteer_activity.py --include-hidden
    python scripts/backfill_volunteer_activity.py --include-hidden --apply

--include-hidden is what reaches the archive: rotation hides past weeks, and
those are the weeks that carry the history. Without it only the few visible
weeks are scanned, which is all the two-hourly cron needs.

It is safe to re-run and the tab order does not matter: last_active_at only
ever moves forward, so each volunteer keeps the latest week they appear in.

Pay attention to the unmatched names it lists. Each one is a cell the matcher
could not tie to any volunteer record - usually a nickname with no record
("Yer", "Laure") or a volunteer whose record holds a different name - and each
one is someone who will look lapsed and stop receiving the reminder. Fix the
volunteer's name in the admin dashboard, or ask them to use their full name on
the sheet, and re-run.

Reads the spreadsheet and the database through the app's own configuration, so
it needs the same environment the app runs with: DATABASE_URL, the schedule
sheet's SCHEDULE_SIGNUP_LINK setting, and read scope on the spreadsheet via
GOOGLE_APPLICATION_CREDENTIALS. Only the volunteers table is written; the
spreadsheet is never modified.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db_session  # noqa: E402
from app.models import Volunteer as VolunteerModel  # noqa: E402
from app.services.volunteer_activity import (  # noqa: E402
    record_activity_from_schedule_sheets,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write last_active_at; without it nothing is written",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="also scan the hidden past weeks, which is where the history is",
    )
    args = parser.parse_args(argv)

    with get_db_session() as db:
        results = record_activity_from_schedule_sheets(
            db,
            include_hidden=args.include_hidden,
            dry_run=not args.apply,
        )

        if not results:
            print(
                "No dated schedule tabs found. Check SCHEDULE_SIGNUP_LINK, and "
                "pass --include-hidden if the weeks you expected are archived."
            )
            return 1

        verb = "credited" if args.apply else "would credit"
        unmatched: set[str] = set()
        for title, result in results:
            print(f"{title}: {verb} {result.matched} volunteer(s)")
            unmatched.update(result.unmatched)

        if unmatched:
            print(
                f"\n{len(unmatched)} signup name(s) matched no volunteer record. "
                "Each of these will look lapsed:"
            )
            for name in sorted(unmatched):
                print(f"  - {name}")

        if args.apply:
            placed = (
                db.query(VolunteerModel)
                .filter(VolunteerModel.last_active_at.isnot(None))
                .count()
            )
            total = db.query(VolunteerModel).count()
            print(f"\n{placed} of {total} volunteer(s) now have last_active_at set.")
            print(
                "The rest fall back to their sign-up date, so check that list "
                "before raising REMINDER_ACTIVE_WEEKS."
            )
        else:
            print("\nDry run: nothing written. Re-run with --apply.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
