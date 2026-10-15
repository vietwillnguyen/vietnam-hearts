#!/usr/bin/env python
"""
Backfill a class block from "Schedule Template" into weekly tabs that exist.

Rotation builds a weekly tab by duplicating the template, so a class added to
the template only ever reaches tabs created *after* the edit. The current week
and any future weeks already created keep the old shape, and they hold signups
that cannot be recreated by deleting and re-rotating them.

Run read-only first (the default) and read what it says it would write:

    python scripts/apply_class_block_to_weekly_sheets.py
    python scripts/apply_class_block_to_weekly_sheets.py --apply

It is safe to re-run: a tab that already has the class is skipped. Before it
writes to a tab it duplicates that tab as a hidden, dated backup, and each
tab's three edits (open rows, copy the block, date the new day headers) go up
as one atomic batchUpdate - so a tab is never left holding the blank rows of a
copy that then failed. It never edits the template, never reorders or clears
existing signups, and never touches a protected range.

Credentials come from --credentials or GOOGLE_APPLICATION_CREDENTIALS and need
write scope on the spreadsheet; the deployed service account
(auto-scheduler@...) is already an editor of the weekly tabs' protections.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import GOOGLE_APPLICATION_CREDENTIALS  # noqa: E402
from app.services.schedule_backfill import (  # noqa: E402
    LayoutMismatch,
    build_requests,
    describe_plan,
    find_block,
    plan_backfill,
)
from app.utils.schedule_dates import (  # noqa: E402
    current_week_monday,
    parse_schedule_sheet_title,
)
from app.utils.sheet_utils import a1_range  # noqa: E402

SPREADSHEET_ID = "1WpzHm5deiJ6qMgsIodJpswo-mhOSCAgNMKO5yrhJZx8"
TEMPLATE_TITLE = "Schedule Template"
CLASS_NAME = "Grade 1A"

# Wide enough for the whole class area including the "Classroom Info" pair.
GRID_RANGE = "A1:L100"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
READONLY_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write; without it the run is read-only and prints the plan",
    )
    parser.add_argument("--class-name", default=CLASS_NAME)
    parser.add_argument("--spreadsheet", default=SPREADSHEET_ID)
    parser.add_argument("--template", default=TEMPLATE_TITLE)
    parser.add_argument("--credentials", default=GOOGLE_APPLICATION_CREDENTIALS)
    parser.add_argument(
        "--tab",
        action="append",
        dest="tabs",
        help="limit to this tab title; repeatable. Default: every visible "
        "dated tab for the current week or later",
    )
    return parser.parse_args(argv)


def sheets_client(credentials_path: str, write: bool):
    if not credentials_path:
        raise SystemExit(
            "No credentials: pass --credentials or set GOOGLE_APPLICATION_CREDENTIALS"
        )
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=SCOPES if write else READONLY_SCOPES
    )
    return build("sheets", "v4", credentials=credentials).spreadsheets()


def read_grid(api, spreadsheet_id: str, title: str) -> list[list[str]]:
    response = (
        api.values()
        .get(spreadsheetId=spreadsheet_id, range=a1_range(title, GRID_RANGE))
        .execute()
    )
    return response.get("values", [])


def target_tabs(sheets: list[dict], requested: list[str] | None) -> list[dict]:
    """The weekly tabs to backfill: the current week and the future ones.

    Past weeks are left alone - their classes have already happened - and
    "Schedule Template" is excluded by the same date parse that excludes it
    from rotation.
    """
    if requested:
        by_title = {sheet["properties"]["title"]: sheet for sheet in sheets}
        missing = [title for title in requested if title not in by_title]
        if missing:
            raise SystemExit(f"No such tab(s): {missing}")
        return [by_title[title] for title in requested]

    monday = current_week_monday()
    chosen = []
    for sheet in sheets:
        properties = sheet["properties"]
        date = parse_schedule_sheet_title(properties["title"])
        if date is None or properties.get("hidden", False):
            continue
        if date.date() >= monday.date():
            chosen.append(sheet)
    return sorted(
        chosen, key=lambda s: parse_schedule_sheet_title(s["properties"]["title"])
    )


def back_up_tab(
    api, spreadsheet_id: str, sheet: dict, today: datetime, at_index: int
) -> str:
    """Duplicate ``sheet`` as a hidden tab named for today, and return its title.

    The copy is deliberately *not* called "Schedule ...": rotation claims
    every tab with that prefix, and a backup is not a week anyone signs up on.
    """
    title = (
        f"Backup {sheet['properties']['title'].replace('/', '-')} "
        f"{today.strftime('%Y-%m-%d')}"
    )
    response = api.batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "duplicateSheet": {
                        "sourceSheetId": sheet["properties"]["sheetId"],
                        # Last, so the display tabs keep the indexes rotation
                        # expects to find them at.
                        "insertSheetIndex": at_index,
                        "newSheetName": title,
                    }
                }
            ]
        },
    ).execute()
    backup_id = response["replies"][0]["duplicateSheet"]["properties"]["sheetId"]
    api.batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": backup_id, "hidden": True},
                        "fields": "hidden",
                    }
                }
            ]
        },
    ).execute()
    return title


def main(argv=None) -> int:
    args = parse_args(argv)
    api = sheets_client(args.credentials, write=args.apply)
    metadata = api.get(spreadsheetId=args.spreadsheet).execute()
    sheets = metadata["sheets"]

    template_grid = read_grid(api, args.spreadsheet, args.template)
    if find_block(template_grid, args.class_name) is None:
        print(f"{args.template} has no {args.class_name!r} block; nothing to copy")
        return 1

    targets = target_tabs(sheets, args.tabs)
    if not targets:
        print("No current or future weekly tabs to backfill")
        return 0

    mode = "APPLY" if args.apply else "DRY RUN (nothing is written)"
    print(f"{mode}: copying {args.class_name!r} from {args.template!r}")
    print(f"spreadsheet {args.spreadsheet}")
    print(f"targets: {[s['properties']['title'] for s in targets]}")
    print(
        "Rows are inserted full-width, so every row below the insert point - "
        "signups, and the sheet-wide 'Schedule Key' note in column L - keeps "
        "its contents and moves down by the block's height.\n"
    )

    plans = []
    refusals = []
    for sheet in targets:
        title = sheet["properties"]["title"]
        grid = read_grid(api, args.spreadsheet, title)
        try:
            plan = plan_backfill(
                template_grid=template_grid,
                target_grid=grid,
                class_name=args.class_name,
                sheet_title=title,
                sheet_id=sheet["properties"]["sheetId"],
                week_monday=parse_schedule_sheet_title(title),
            )
        except LayoutMismatch as mismatch:
            refusals.append(str(mismatch))
            print(f"REFUSED {title}: {mismatch}\n")
            continue
        for line in describe_plan(plan, template_grid):
            print(line)
        print()
        plans.append((sheet, plan))

    if refusals:
        print(
            f"{len(refusals)} tab(s) refused; nothing was written. "
            "Resolve the layout difference or pass --tab for the rest."
        )
        return 2

    if not args.apply:
        print("Re-run with --apply to write this.")
        return 0

    today = datetime.now()
    template_id = template_sheet_id(sheets, args)
    backup_index = len(sheets)
    for sheet, planned in plans:
        if planned.already_present:
            continue
        title = planned.sheet_title

        # Re-plan against the tab as it is right now: a volunteer may have
        # typed into it since the plan above was printed, and the insert point
        # is a row number.
        try:
            plan = plan_backfill(
                template_grid=template_grid,
                target_grid=read_grid(api, args.spreadsheet, title),
                class_name=args.class_name,
                sheet_title=title,
                sheet_id=sheet["properties"]["sheetId"],
                week_monday=parse_schedule_sheet_title(title),
            )
        except LayoutMismatch as mismatch:
            print(f"ABORTED {title}, it changed since the plan: {mismatch}")
            return 2
        if plan.already_present:
            print(f"skipped {title}, it already has {args.class_name}")
            continue
        if plan.insert_at != planned.insert_at:
            print(
                f"ABORTED {title}: the insert point moved from row "
                f"{planned.insert_at + 1} to row {plan.insert_at + 1} since "
                f"the plan was printed. Re-run the dry run."
            )
            return 2

        backup = back_up_tab(api, args.spreadsheet, sheet, today, backup_index)
        backup_index += 1
        print(f"backed up {title} to {backup!r}")

        # One atomic batch: a tab is never left holding the blank rows of a
        # copy that then failed.
        api.batchUpdate(
            spreadsheetId=args.spreadsheet,
            body={"requests": build_requests(plan, template_id)},
        ).execute()

        written = read_grid(api, args.spreadsheet, title)
        placed = find_block(written, args.class_name)
        if placed is None or placed.start != plan.insert_at:
            print(
                f"CHECK {title}: expected {args.class_name} at row "
                f"{plan.insert_at + 1}, found {placed}"
            )
            return 3
        print(f"applied to {title}: {args.class_name} now at row {placed.start + 1}\n")
    print("Done.")
    return 0


def template_sheet_id(sheets: list[dict], args) -> int:
    for sheet in sheets:
        if sheet["properties"]["title"] == args.template:
            return sheet["properties"]["sheetId"]
    raise SystemExit(f"No {args.template!r} tab in the spreadsheet")


if __name__ == "__main__":
    raise SystemExit(main())
