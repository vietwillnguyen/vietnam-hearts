# Schedule Rotation DD/MM/YYYY + Persistent Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make schedule rotation robust (survive protected sheets), rename schedule sheets to `Schedule DD/MM/YYYY`, surface real failures to Cloud Scheduler, and add a persistent DB-backed Logs tab to the admin dashboard.

**Architecture:** A shared title parse/format utility becomes the single source of truth for schedule sheet naming (new `DD/MM/YYYY` format with legacy `MM/DD` parsing).
The rotation matches sheets by parsed date instead of exact string, tolerates per-sheet API failures, and reports them.
Logs persist through a buffered SQLAlchemy logging handler into a `system_logs` table, exposed via `/admin/logs` and a new dashboard tab.
On Cloud Run, stdout logs switch to structured JSON so Cloud Logging parses severity.

**Tech Stack:** FastAPI, SQLAlchemy, Google Sheets API (`googleapiclient`), Bootstrap 5 dashboard, pytest.

## Root-Cause Evidence (from production investigation, 2026-07-09)

- Cloud Logging HAS all service logs (30-day retention, default sinks intact). The user-visible gap is (a) console filtering and (b) severities not parsed because logs are plain text on stderr.
- Rotation has failed every Friday run since at least 2026-06-12: 2025-era sheets `Schedule 07/07` / `Schedule 07/14` carry whole-sheet protection with `editors: []`, so hiding them throws `HttpError 400 "protected cell or object"`, aborting the entire rotation in PASS 1 before any new sheets are created. That is why `Schedule 07/06` never existed (nothing was deleted).
- The rotate endpoint swallows exceptions and returns HTTP 200 with `{"status": "error"}`, so Cloud Scheduler saw every failure as success.
- A `Schedule Config` sheet matches the `"Schedule "` prefix and is incorrectly included in rotation logic.

## Global Constraints

- Sheet title format going forward: `Schedule DD/MM/YYYY` (e.g. `Schedule 13/07/2026`).
- Legacy titles `Schedule MM/DD` must still be recognized (matched by date, assumed current year).
- `Schedule Template` and `Schedule Config` (and any non-date suffix) must never be rotated/hidden/renamed (except the existing explicit Template hide).
- Rotation must never abort because one sheet fails; failures are collected and reported.
- Rotate endpoint returns HTTP 500 on total failure (so Cloud Scheduler alerts); HTTP 200 with `sheets_failed` warnings on partial success.
- DB log writes must never raise into application code paths and must not recurse (skip `sqlalchemy.*` loggers).
- No co-author lines in commit messages.

---

### Task 1: Schedule sheet title utilities

**Files:**
- Create: `app/utils/schedule_dates.py`
- Test: `tests/test_schedule_dates.py`

**Interfaces:**
- Produces: `format_schedule_sheet_title(date: datetime) -> str`
- Produces: `parse_schedule_sheet_title(title: str, default_year: int | None = None) -> datetime | None`

- [ ] Write failing tests covering: new format round-trip, legacy `MM/DD` parse with default year, `Schedule Template` / `Schedule Config` / random titles return `None`, non-`Schedule ` prefix returns `None`.
- [ ] Implement:

```python
"""Schedule sheet title parsing/formatting.

Single source of truth for the "Schedule <date>" tab naming scheme.
New sheets use DD/MM/YYYY; legacy MM/DD titles (no year) are still parsed
so rotation can match and migrate them.
"""
from datetime import datetime
from typing import Optional

SCHEDULE_TITLE_PREFIX = "Schedule "


def format_schedule_sheet_title(date: datetime) -> str:
    return f"{SCHEDULE_TITLE_PREFIX}{date.strftime('%d/%m/%Y')}"


def parse_schedule_sheet_title(title: str, default_year: Optional[int] = None) -> Optional[datetime]:
    if not title or not title.startswith(SCHEDULE_TITLE_PREFIX):
        return None
    date_part = title[len(SCHEDULE_TITLE_PREFIX):].strip()
    try:
        return datetime.strptime(date_part, "%d/%m/%Y")
    except ValueError:
        pass
    try:
        parsed = datetime.strptime(date_part, "%m/%d")
    except ValueError:
        return None
    return parsed.replace(year=default_year or datetime.now().year)
```

- [ ] `poetry run pytest tests/test_schedule_dates.py -v` → PASS
- [ ] Commit: `feat: add schedule sheet title utilities (DD/MM/YYYY with legacy MM/DD parsing)`

### Task 2: SystemLog model + buffered DB log handler

**Files:**
- Modify: `app/models.py` (add `SystemLog`)
- Create: `app/utils/db_log_handler.py`
- Test: `tests/test_db_log_handler.py`

**Interfaces:**
- Produces: `SystemLog` (id, created_at indexed, level indexed, logger_name, message)
- Produces: `DatabaseLogHandler(logging.Handler)` with `flush()`; buffers up to `buffer_size=20` records or `flush_interval=5.0`s.

- [ ] Model:

```python
class SystemLog(Base):
    """Persisted application log record (backs the admin Logs page)."""
    __tablename__ = "system_logs"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    level = Column(String, index=True)
    logger_name = Column(String)
    message = Column(Text)
```

- [ ] Handler: thread-safe buffer; `emit()` appends formatted record dicts and flushes when the buffer is full or stale; skips records from `sqlalchemy*`; `flush()` bulk-inserts via a fresh `SessionLocal()` (lazy import to avoid circulars), swallows all exceptions, sets a reentrancy guard; performs a once-per-process retention delete of rows older than `LOG_RETENTION_DAYS` (env, default 30).
- [ ] Tests: records land in `system_logs`; `sqlalchemy` records skipped; handler survives a broken session factory without raising; retention deletes old rows.
- [ ] `poetry run pytest tests/test_db_log_handler.py -v` → PASS
- [ ] Commit: `feat: persist application logs to system_logs table via buffered handler`

### Task 3: Wire handler + Cloud Run JSON log formatter

**Files:**
- Modify: `app/utils/logging_config.py`
- Test: `tests/test_logging.py` (extend)

**Interfaces:**
- Consumes: `DatabaseLogHandler` from Task 2.
- Produces: `setup_logger()` attaches a shared `DatabaseLogHandler` singleton when `PERSIST_LOGS_TO_DB` env (default `"true"`) is truthy; uses a JSON formatter on stdout when `K_SERVICE` env is set (Cloud Run) so Cloud Logging parses `severity`.

- [ ] JSON formatter emits `{"severity": levelname, "message": ..., "logger": name, "time": ISO8601}` one object per line.
- [ ] Tests: JSON mode produces parseable line with severity; DB handler attached exactly once per logger; `PERSIST_LOGS_TO_DB=false` disables.
- [ ] `poetry run pytest tests/test_logging.py -v` → PASS
- [ ] Commit: `feat: structured Cloud Run logging + DB persistence wiring`

### Task 4: Robust rotation with DD/MM/YYYY naming

**Files:**
- Modify: `app/services/google_sheets.py`
- Test: `tests/test_schedule_rotation.py` (new)

**Interfaces:**
- Consumes: Task 1 utilities.
- Produces: `rotate_schedule_sheets()` result gains `"sheets_failed": [{"title", "action", "error"}]` and `"sheets_renamed": [...]`; `rename_sheet(sheet_id, new_title, db)` method.

Changes:
- [ ] `create_sheet_from_template` and `update_sheet_dates` use `format_schedule_sheet_title` / accept the canonical title; in-sheet header dates use `%d/%m`; B1 becomes `Schedule for Week DD/MM/YYYY`.
- [ ] `rotate_schedule_sheets`:
  - Build `display_dates` as before; canonical titles via `format_schedule_sheet_title`.
  - PASS 1: for each existing sheet, `parse_schedule_sheet_title(title)`; skip `None` (excludes Config/Template); hide when parsed date not in display set; wrap each hide in try/except collecting into `sheets_failed` and continuing.
  - PASS 2: match existing sheet by parsed date (`.date()` equality); if matched under a legacy title, `rename_sheet` to canonical (collect into `sheets_renamed`, tolerate failure); unhide + move; else create from template. Each per-sheet operation try/except → `sheets_failed`, continue.
- [ ] `get_current_schedule_dates` uses `parse_schedule_sheet_title`.
- [ ] Tests (mock `self.sheet` / service methods with `MagicMock`): protected-sheet hide failure doesn't abort and is reported; `Schedule Config` untouched; legacy-titled sheet for a display date is renamed not duplicated; new sheets created with DD/MM/YYYY titles.
- [ ] `poetry run pytest tests/test_schedule_rotation.py -v` → PASS
- [ ] Commit: `fix: rotation survives protected sheets, matches by date, names sheets DD/MM/YYYY`

### Task 5: Honest rotate endpoint + status parsing + email subject dates

**Files:**
- Modify: `app/routers/admin/schedules.py`
- Modify: `app/services/email_service.py:84`
- Test: `tests/test_api.py` (extend)

- [ ] `/admin/rotate-schedule`: on exception raise `HTTPException(500, ...)` (Cloud Scheduler must see failure); on success include `sheets_failed` warnings in message when non-empty.
- [ ] `/admin/schedule-status`: parse titles with `parse_schedule_sheet_title`; exclude non-date sheets from week counts.
- [ ] Weekly reminder subject dates: `strftime("%d/%m")`.
- [ ] `poetry run pytest tests/test_api.py -v` → PASS
- [ ] Commit: `fix: rotate endpoint returns 500 on failure; DD/MM dates in status and email subject`

### Task 6: Admin logs API

**Files:**
- Create: `app/routers/admin/logs.py`
- Modify: `app/routers/admin/__init__.py`
- Test: `tests/test_admin_logs_api.py` (new)

**Interfaces:**
- Produces: `GET /admin/logs?level=&q=&page=1&page_size=50` → `{"status": "success", "details": {"logs": [{id, created_at, level, logger_name, message}], "total", "page", "page_size"}}`, newest first, `page_size` capped at 200.

- [ ] Implement with SQLAlchemy filters (`level` exact match uppercase, `q` case-insensitive `LIKE` on message/logger_name).
- [ ] Tests: pagination, level filter, search filter, auth required (mirror existing admin endpoint test patterns).
- [ ] `poetry run pytest tests/test_admin_logs_api.py -v` → PASS
- [ ] Commit: `feat: /admin/logs endpoint for persisted system logs`

### Task 7: Dashboard Logs tab

**Files:**
- Modify: `templates/web/admin/dashboard.html`

- [ ] Add nav link `logs-tab` + tab pane `#logs` following existing Bootstrap tab markup.
- [ ] Pane: level dropdown (All/DEBUG/INFO/WARNING/ERROR), search input, Refresh button, auto-refresh checkbox (10s), table (time, level with badge colors, logger, message), Prev/Next pagination.
- [ ] JS `loadLogs(page)` fetches `/admin/logs` with `credentials: 'include'` like existing fetches; lazy-load on tab shown; auto-refresh interval cleared when unchecked/tab hidden.
- [ ] Verify: `poetry run pytest` full suite green.
- [ ] Commit: `feat: Logs tab in admin dashboard`

### Task 8: Full verification

- [ ] `poetry run pytest` → all green.
- [ ] Boot app locally (`TESTING` off, sqlite) and curl `/admin/logs` smoke check if practical.
- [ ] Do NOT deploy; deployment and removing the 2025 sheet protections are decisions for Viet.
