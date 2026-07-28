# API Tests

Pytest suite for the Vietnam Hearts API, plus notes on exercising the admin endpoints by hand against a running server.

## Layout

- `conftest.py` - shared fixtures: an in-memory sqlite engine, a `client` fixture wired to it, and mocked Supabase dependencies.
- `test_api.py` - endpoint-level integration tests covering route wiring, admin authentication, and status-code contracts.
- The remaining `test_*.py` files are grouped by feature (schedules, settings, email, logging, migrations, and so on).

## Running the tests

See [Running Tests Locally](../README.md#running-tests-locally) for the standard commands.
To run only the endpoint integration tests:

```bash
uv run pytest tests/test_api.py -v
```

The suite is self-contained.
It uses an in-memory sqlite database and mocks Supabase, Google Sheets, and email, so no `.env` file, network access, or gcloud login is needed.

## Exercising an endpoint by hand

Admin endpoints authenticate the same way Cloud Scheduler does: an `apikey` header carrying the value of `SUPABASE_SECRET_KEY`.
That key resolves to the service account identity, which must also be listed in `ADMIN_EMAILS` (or exist as an admin user in the database) or the request is rejected with `403` - see [Service Account Setup](../docs/SERVICE_ACCOUNT_SETUP.md).

`tools/api_tester.py` wraps this up as a CLI, reading `SUPABASE_SECRET_KEY` from `.env`:

```bash
# API_URL defaults to the PRODUCTION service, so always set it explicitly
API_URL=http://localhost:8080 uv run python tools/api_tester.py rotate-schedule
API_URL=http://localhost:8080 uv run python tools/api_tester.py all
```

Leave `--auth-type` at its `supabase` default; the `gcloud` option mints an OIDC token, which no longer matches any server-side auth path and always yields `401`.

For anything the tool does not have a mapping for, such as `sync-cron-schedules`, call the endpoint directly:

```bash
curl -X POST http://localhost:8080/admin/sync-cron-schedules \
  -H "apikey: $SUPABASE_SECRET_KEY"
```

A `401` means the key is missing, malformed, or does not match the server's `SUPABASE_SECRET_KEY`.
A `403` means the key was accepted but its identity is not an admin.

## Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/health` | GET | Health check across database, Google Sheets, and email service connectivity |
| `/admin/send-confirmation-emails` | POST | Process and send confirmation emails to new volunteers |
| `/admin/sync-volunteers` | POST | Sync volunteers from Google Sheets signup form |
| `/admin/send-weekly-reminders` | POST | Send weekly reminder emails to subscribed volunteers (skipped if no class has an open volunteer slot) |
| `/admin/rotate-schedule` | POST | Sync schedule sheets so the current week plus N-1 future weeks are visible, in order, each protected against accidental edits. Idempotent, so it is safe to run hourly. Answers `502` (with the full per-sheet detail) if any sheet was skipped, so a partial reconciliation is never recorded as a successful run |
| `/admin/sync-cron-schedules` | POST | Apply the `CRON_*` settings to their Cloud Scheduler jobs (cadence only; never touches job credentials) |

## Troubleshooting

### Tests fail on import

Run `uv sync` to install the dev dependency group (pytest, pytest-cov, ruff), then re-run from the project root.

### A manual request returns 401 or 403

1. Confirm the `apikey` value matches the running server's `SUPABASE_SECRET_KEY` exactly (a rotated key is the usual cause).
2. Confirm the service account email is in `ADMIN_EMAILS` and restart the application after changing it.

### Cannot connect to the API

1. Check the server is running: `curl http://localhost:8080/docs`.
2. Check the port matches the one `./run.sh` reported (default 8080).
