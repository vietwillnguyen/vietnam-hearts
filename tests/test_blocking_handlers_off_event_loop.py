"""
Cron handlers that block on Sheets, SMTP or the database must not run on the
event loop.

An `async def` handler with no awaits runs its blocking calls on the loop
thread itself, so a rotation or a reminder send stalls every other request on
the instance for as long as it runs. As plain `def` handlers FastAPI runs them
in its threadpool instead.
"""

import asyncio
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.main import app


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture
def admin_client(client):
    from app.dependencies.auth import get_current_admin_user

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "id": "service-account-auto-scheduler",
        "email": "auto-scheduler@example.iam.gserviceaccount.com",
        "is_service_account": True,
    }
    yield client
    app.dependency_overrides.pop(get_current_admin_user, None)


@contextmanager
def _no_session():
    yield None


def _recorder(seen, result):
    def record(*_args, **_kwargs):
        seen.append(_on_event_loop())
        return result

    return record


def test_rotate_schedule_runs_off_the_event_loop(admin_client):
    seen = []
    with (
        patch("app.routers.admin.schedules.get_db_session", _no_session),
        patch(
            "app.routers.admin.schedules.sheets_service.rotate_schedule_sheets",
            side_effect=_recorder(seen, {"sheets_failed": []}),
        ),
    ):
        response = admin_client.post("/admin/rotate-schedule")

    assert response.status_code == 200
    assert seen == [False]


def test_send_weekly_reminders_runs_off_the_event_loop(admin_client):
    seen = []
    with (
        patch("app.routers.admin.emails.get_db_session", _no_session),
        patch(
            "app.routers.admin.emails.ConfigHelper.get_weekly_reminders_enabled",
            side_effect=_recorder(seen, False),
        ),
    ):
        response = admin_client.post("/admin/send-weekly-reminders")

    assert response.json()["status"] == "skipped"
    assert seen == [False]


def test_send_confirmation_emails_runs_off_the_event_loop(admin_client):
    seen = []
    with patch(
        "app.routers.admin.emails.email_service.send_confirmation_emails",
        side_effect=_recorder(seen, None),
    ):
        response = admin_client.post("/admin/send-confirmation-emails")

    assert response.json()["status"] == "success"
    assert seen == [False]


def test_sync_volunteers_runs_off_the_event_loop(admin_client):
    seen = []
    with patch(
        "app.routers.admin.signups.get_signup_form_submissions",
        side_effect=_recorder(seen, {"status": "success"}),
    ):
        response = admin_client.post("/admin/sync-volunteers")

    assert response.json()["status"] == "success"
    assert seen == [False]
