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

## Inbound channels (QnA bot)

The approved design for the Messenger and email bot is `docs/superpowers/specs/2026-07-28-qna-bot-design.md`;
read its "Amendment 2026-09-29" section before trusting D6 or the rollout table.
The email channel's own design and phase plan are `docs/superpowers/specs/2026-09-29-email-channel-design.md`
and `docs/superpowers/plans/2026-09-29-email-channel-implementation.md`.

The shared engine now exists and email is its first channel: `app/services/channels/`
(transport, guards, MIME), `app/services/triage/` (two classifiers behind one protocol,
with the tier derived in `policy.py` and never by a model), `app/services/conversation_service.py`
and `app/services/email_bot/` (per-run pipeline). Messenger still runs on the
phase-0 webhook and has not been migrated onto it.

Two properties of that code are load-bearing and easy to break:

- **The bot cannot send mail to the public, by construction.** `GmailTransport` has no
  `send_reply` and `users.messages.send` is called nowhere. Do not add one outside the
  phase that introduces `SendSink`; `tests/test_gmail_transport.py` fails on any attribute
  access named `send`. The only outbound path is `EmailForwardNotifier`, whose recipient is
  fixed at construction.
- **Nothing derived from inbound mail may be logged or stored.** Log lines and exception
  messages carry Gmail ids, categories, tiers and actions only, because logs persist to the
  database and Sentry runs with `send_default_pii=True`. `tests/test_email_bot_privacy.py`
  sweeps the whole fixture corpus and fails on an `@` or a body phrase anywhere.

## Capturing logs in tests

`app/utils/logging_config.py` sets `propagate = False`, and pytest's `caplog` installs its
handler on the root logger, so a bare `caplog` assertion against an app logger captures
nothing and **passes vacuously**. Use `tests/fixtures/logs.py::attached_caplog`, which hangs
`caplog`'s handler on the named loggers.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
