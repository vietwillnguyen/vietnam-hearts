"""
system_logs keeps 30 days of history and nothing older.

The DatabaseLogHandler only prunes once per process, on its first flush, and
swallows any error doing it, so how much history is kept depends on how often
Cloud Run happens to start an instance. A daily Cloud Scheduler job calls
POST /admin/prune-system-logs so retention runs on a schedule and reports
what it deleted.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.main import app
from app.models import SystemLog


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


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


def _seed(db, ages_days):
    for i, age in enumerate(ages_days):
        db.add(
            SystemLog(
                created_at=_now() - timedelta(days=age),
                level="INFO",
                logger_name="api",
                message=f"row {i} age {age}d",
            )
        )
    db.commit()


def test_prune_deletes_only_rows_older_than_30_days(admin_client, test_db):
    _seed(test_db, [0, 1, 29, 31, 45, 90])

    response = admin_client.post("/admin/prune-system-logs")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["deleted"] == 3
    assert body["retention_days"] == 30
    remaining = sorted(r.message for r in test_db.query(SystemLog).all())
    assert remaining == ["row 0 age 0d", "row 1 age 1d", "row 2 age 29d"]


def test_prune_is_idempotent(admin_client, test_db):
    _seed(test_db, [40])

    assert admin_client.post("/admin/prune-system-logs").json()["deleted"] == 1
    assert admin_client.post("/admin/prune-system-logs").json()["deleted"] == 0


def test_retention_days_follows_the_env_setting(admin_client, test_db, monkeypatch):
    monkeypatch.setenv("LOG_RETENTION_DAYS", "7")
    _seed(test_db, [3, 8])

    body = admin_client.post("/admin/prune-system-logs").json()

    assert body["deleted"] == 1
    assert body["retention_days"] == 7


def test_prune_requires_admin(client):
    assert client.post("/admin/prune-system-logs").status_code in (401, 403)


def test_scheduler_script_creates_the_daily_job():
    from app.config import PROJECT_ROOT

    script = (
        PROJECT_ROOT / "scripts" / "create-or-update-scheduler-jobs.sh"
    ).read_text()
    assert '"prune-system-logs"' in script
    assert '"/admin/prune-system-logs"' in script
