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

## Which tab the schedule is read from

A range with no tab name resolves to the *first sheet in the spreadsheet*, and
rotation pins "Schedule Template" there (display weeks start at index 1). An
unqualified read of the class grid therefore returned the template's
placeholder rows - that is what made every weekly reminder describe unfilled
classes - so always go through
`GoogleSheetsService.get_leading_schedule_sheet`, the first *visible dated*
tab, and qualify ranges with `sheet_utils.a1_range`. Dated matters as much as
visible: every helper here matches tabs by the "Schedule " prefix, which also
catches the template, and the template is visible whenever a volunteer is
editing it.

## Template changes reach only future tabs

Rotation builds a weekly tab by duplicating "Schedule Template", so a class
the captain adds to the template appears only in tabs created after the edit;
the current week and already-created future weeks keep the old shape and hold
signups that cannot be recreated. `scripts/apply_class_block_to_weekly_sheets.py`
backfills one template block into those tabs (dry-run by default, idempotent,
inserts full-width rows above the block at the same position the template puts
it). It placed Grade 1A into the weeks of 12/10, 19/10 and 26/10/2026 on
2026-10-15, each backed up first to a hidden `Backup Schedule <date> 2026-10-15`
tab - those are safe to delete once the captain is happy.

Nothing in the code enumerates classes, so a fourth block needed no code
change. Two shapes to keep in mind when touching the parser: a class title may
carry one time line per day (Grade 1A meets Tuesday morning and Thursday
afternoon), and `discover_schedule_blocks` treats every column after the label
as a day - read B:G, or the "Classroom Info" cells in I:J become two phantom
days with no teacher.

## Logging

Every app logger comes from `app/utils/logging_config.py::get_logger`, and each one fans a
record out to three sinks: stdout (JSON via `CloudRunJSONFormatter` whenever `K_SERVICE` is
set, so Cloud Logging parses severity), a rotating file under `logs/`, and - unless
`PERSIST_LOGS_TO_DB=false` - the `system_logs` table through the shared `DatabaseLogHandler`.
Anything logged therefore lands in Cloud Logging (~30-day default retention) *and* in the
database the admin dashboard reads, so treat a log line as published to both.

Startup runs `alembic upgrade head`, and `alembic/env.py` must keep `disable_existing_loggers=False` on its `fileConfig` call.
Without it every app logger created before the migration is disabled, and production logs nothing after the startup banner.

`CredentialRedactingFilter` rewrites a record so a URL password, a credential-shaped query
parameter, a Bearer token, or an Authorization value is redacted to `***` even if a call
site forgets. `install_credential_redaction()` runs on import of `logging_config` and puts
it on every sink in the process: `logging.lastResort`, the root logger, the handlers already
on any logger (uvicorn's access and error handlers exist before the app is imported), and
any handler added later via a one-time `Logger.addHandler` wrap. It redacts msg and each str
arg in place so formatters that unpack `record.args` keep working. That matters because three
modules bypass the factory with a bare `logging.getLogger(__name__)` (`app/config.py`,
`app/services/messenger/message_sender.py`, `app/utils/retry_utils.py`); with no root
handlers in production their records reach Cloud Logging through `lastResort` on stderr.
They are redacted but still miss the file and `system_logs` sinks, so use `get_logger` for
anything new.

## Privacy policy page

`/privacy` is public on purpose: its URL sits in the Google OAuth consent screen, so Google's
reviewer fetches it with no account. Never put it behind auth, and never reword the Limited Use
sentence - Google specifies it verbatim. The text, contact address, disclosure and "Last updated"
date are constants at the top of `app/routers/public.py`; `tests/test_privacy_policy.py` pins both
properties, and the README's "Privacy policy URL" section covers the console step.

## Sending a batch of emails

The weekly reminder send goes through `EmailService.send_html_batch`, which holds one
authenticated SMTP session open for the whole batch and yields a per-message outcome.
A connection per message cost about 1.85s (TCP + STARTTLS + AUTH against Gmail), so the
Sunday run grew with the volunteer list and died at Cloud Run's 300s request timeout once
it passed roughly 160 recipients - four Sundays in a row, 2026-09-27 to 2026-10-18. Use
that method for anything bulk; `send_custom_email` opens its own connection and, unlike
the batch sender, reports a refused send as `False` rather than raising, so a caller that
ignores the return value records a failure as "sent".

Rows are committed PENDING before the batch is sent, and each outcome is committed as it is yielded, so a run cut off at the deadline leaves only its unsent remainder PENDING.
The next run skips any volunteer with a `sent` row for the same week, and `fail_abandoned_pending_reminders` resolves PENDING rows older than six hours to `failed`.
The run key is the subject's trailing `(dd/mm-dd/mm)`, read by `helpers.py::reminder_week_key_of` and built by `email_service.reminder_week_key` - not the whole subject, which now leads with an open-slot count that moves the moment anyone signs up.
Matching the whole subject would make a resumed run whose count had changed re-email everyone the first attempt reached, and would split one run's rows into two in the health report.
A subject with no trailing `(...)` keys on itself, so rows written before the subject changed still group as they did.
Outcomes are paired to rows by position, never by address - `volunteers.email` has no unique constraint.
A successful send made while DRY_RUN is on is recorded `dry_run`: the sends `send_custom_email` skips, and the bulk run's real send to the dry-run recipient, since no volunteer received it.
A per-volunteer send to the dry-run recipient under DRY_RUN really goes out and is recorded `sent`.
Every bulk row written under DRY_RUN also carries the dry-run template stamp below whatever its outcome, so a failed or abandoned one reads `failed` yet still counts as a dry run.
`helpers.py::is_dry_run_email` (status `dry_run` or the stamp) is the one definition of a dry-run row; reminder-stats and the email log use it, and resume and run health never count such a row as delivered, whatever DRY_RUN is set to later.

`GET /admin/weekly-reminder-health` reports the last bulk run, and `/admin/health` degrades on anything but `ok`, because Cloud Scheduler only keeps the last attempt.
Bulk rows are the ones with `template_name` `weekly-reminder-email.html`; a manual per-volunteer send leaves it empty, so it can never stand in for a failed Sunday.
A bulk run under DRY_RUN stamps `dry-run:weekly-reminder-email.html` on every row it writes, so its sent, failed or abandoned rows never read as a real run after DRY_RUN is switched off.
The verdict is per recipient: a recipient counts as delivered if any of their rows that week is `sent`, so a resumed run clears it.
With DRY_RUN on the verdict is `dry_run` and the payload carries `"dry_run": true`, since no volunteer is being reminded.

No test may open a real SMTP connection; `tests/conftest.py::forbid_real_smtp` raises a
`BaseException` subclass if one tries, deliberately not an `Exception`, which the batch
sender would catch and report as a run of failed sends.

## Admin auth for the cron jobs

Cloud Scheduler authenticates with the `apikey` header holding `SUPABASE_SECRET_KEY`.
A wrong key is a 401; a **403 means the key was right** and the admin gate rejected the
caller, so a 403 on a cron endpoint is never a credential to rotate. That gate used to
put the synthetic service-account email through the `admin_users` lookup, which fails
closed, so an absent row or a slow Supabase denied every scheduled run -
`get_current_admin_user` now short-circuits on `is_service_account`.

Cloud Scheduler's default 180s attempt deadline is shorter than Cloud Run's 300s request
timeout, so it recorded failures for runs the service was still serving.
`SCHEDULER_ATTEMPT_DEADLINE` in `scripts/deploy.config` pins the two together and is sent
on both the create and update paths of `scripts/create-or-update-scheduler-jobs.sh`.

## Who the weekly reminder goes to

The sign-up form has no opt-in question, so every accepted volunteer is auto-subscribed; `volunteer_activity.select_weekly_reminder_recipients` adds a recency window (`REMINDER_ACTIVE_WEEKS`; default `0`, which disables it, until the backfill below has run and the captain raises it) on top of the subscription flags.
Recency comes from `volunteers.last_active_at`, the Monday of the most recent schedule week the volunteer is signed up on, and NULL there means the name matcher has never placed them - so recency falls back to their sign-up date rather than reading as inactive.
Fill it for existing volunteers with `scripts/backfill_volunteer_activity.py --include-hidden` (dry-run by default); the `review-and-sync` cron keeps it current from the visible weeks and reports the signup names it could not place.

Attribution is name matching and nothing else, because the sheet records a name a volunteer typed and never an email.
It is lossy: one live week held 21 distinct signup names of which 6 were nickname-only and 4 of those had no parenthetical alias, so roughly a fifth of *active* volunteers can be unplaceable.
Two rules therefore hold throughout `volunteer_activity.py`, and both exist so an attribution error never unsubscribes someone: an ambiguous name credits every candidate rather than guessing one, and a name that cannot be placed is reported rather than dropped.
For the same reason the captain ruled on 2026-10-22 that lapsed volunteers are only ever flagged for review - `GET /admin/volunteers/lapsed` and the dashboard's "Lapsed" badge - and never deactivated automatically.

The reminder is list mail, so each message carries RFC 8058 `List-Unsubscribe` and `List-Unsubscribe-Post` headers pointing at `POST /unsubscribe/one-click`, which Gmail and Yahoo's bulk-sender rules expect and which renders their own unsubscribe control.
That endpoint is POST-only on purpose (clients and scanners prefetch links), is idempotent because providers retry, and stops the weekly reminder *only* - the volunteer keeps announcements and their account.
The link travels as the fourth element of each `send_html_batch` row rather than being looked up from the address, for the same reason outcomes are paired by position: `volunteers.email` has no unique constraint.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
