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

A schedule week runs Monday to Friday and is anchored by
`app/utils/schedule_dates.py::current_week_monday()`, which is the single source of truth
for "which week are we showing". Two properties are easy to break and are covered by tests:
"now" is evaluated in the organization's timezone (Cloud Run sets no TZ, so a naive clock
reads UTC and lands a week off), and the anchor rolls forward to the coming Monday from
Saturday 00:00 local. Derive dates from that function rather than recomputing
`now.weekday()` at the call site.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
