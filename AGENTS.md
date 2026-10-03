# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Running the tests

`pytest` fails at collection with `ValueError: Supabase configuration missing` unless the
environment from the `Run tests` step of `.github/workflows/test.yml` is exported first
(`SUPABASE_*`, `GEMINI_API_KEY`, `DATABASE_URL`, `TESTING`, `ENVIRONMENT`, `PYTHONPATH`).
That workflow is the authoritative list; it also pins the lint gates (`ruff check .`,
`ruff format --check .`) and the `--cov-fail-under` threshold CI enforces.

## Schedule weeks

The schedule week runs Monday to Friday, but Vietnam Hearts only teaches on the days in
the `SCHEDULE_TEACHING_DAYS` setting - Tuesday and Thursday. The sheet still carries a
column for every weekday and leaves the non-teaching ones blank, so a blank cell is only
an unfilled slot on a teaching day (`schedule_dates.py::is_teaching_day`); treating every
blank as one is what used to fill the reminder email with false "Missing Teacher" rows.

The displayed week is anchored by `app/utils/schedule_dates.py::current_week_monday()`,
the single source of truth for "which week are we showing". Two properties are easy to
break and are covered by tests: "now" is evaluated in the organization's timezone (Cloud
Run sets no TZ, so a naive clock reads UTC and lands a week off), and the anchor rolls
forward to the coming Monday from Friday 00:00 local so volunteers can sign up for next
week early. Derive dates from that function rather than recomputing `now.weekday()` at
the call site.

## Schedule day headers

Day-header cells (C:G of each class header row) are date serials formatted
`SCHEDULE_DAY_HEADER_FORMAT` (`dddd" "d"/"m`), so the spreadsheet's vi_VN locale
renders "Thứ Hai 5/10" and the displayed text can never disagree with the stored
date. Writing the header as text is what used to let a locale change read 12/10
as 10 December. `update_sheet_dates` rewrites the format on every rotation, so a
new weekly tab does not inherit the Schedule Template's own `dddd" "m"/"d`
(month-first) format - the Template is volunteer-maintained, so never edit it.

A class header is a titled row with at least three day labels, each of which
must *open* with its day (`row_is_class_header`). Both halves carry weight: a
weekday matched anywhere in any one cell made a header out of every row holding
"Annie (Thu Hằng)" or "W24: Shopping & Money", and a quorum of two is not enough
because one live row holds two "Thu Hang" cells. `weekday_token` anchors for the
same reason; `weekday_tokens` stays unanchored for settings values like
"Tuesday and Thursday".

Twelve 2025 tabs (`Schedule 06/09`..`08/25`) are protected owner-only and keep
month-first titles, which is why `parse_schedule_sheet_title` still accepts
`%m/%d`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
