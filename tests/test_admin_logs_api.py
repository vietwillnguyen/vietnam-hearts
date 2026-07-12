"""Tests for the /admin/logs endpoint backing the dashboard Logs tab."""

from datetime import datetime, timedelta

import pytest

from app.main import app
from app.models import SystemLog


@pytest.fixture
def admin_client(client):
    from app.dependencies.auth import get_current_admin_user

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "id": "test-admin",
        "email": "admin@vietnamhearts.org",
    }
    yield client
    app.dependency_overrides.pop(get_current_admin_user, None)


@pytest.fixture
def seeded_logs(test_db):
    base = datetime(2026, 7, 9, 12, 0, 0)
    rows = [
        SystemLog(
            created_at=base + timedelta(minutes=i),
            level=level,
            logger_name=name,
            message=msg,
        )
        for i, (level, name, msg) in enumerate(
            [
                ("INFO", "app", "server started"),
                ("ERROR", "api", "rotation failed: protected sheet"),
                ("WARNING", "api", "could not hide sheet"),
                ("INFO", "scheduler", "weekly reminder sent"),
            ]
        )
    ]
    test_db.add_all(rows)
    test_db.commit()
    return rows


class TestAdminLogsEndpoint:
    def test_requires_auth(self, client):
        response = client.get("/admin/logs")
        assert response.status_code in (401, 403)

    def test_returns_logs_newest_first(self, admin_client, seeded_logs):
        response = admin_client.get("/admin/logs")
        assert response.status_code == 200
        details = response.json()["details"]
        assert details["total"] == 4
        messages = [log["message"] for log in details["logs"]]
        assert messages[0] == "weekly reminder sent"
        assert messages[-1] == "server started"

    def test_filters_by_level(self, admin_client, seeded_logs):
        response = admin_client.get("/admin/logs?level=error")
        details = response.json()["details"]
        assert details["total"] == 1
        assert details["logs"][0]["level"] == "ERROR"

    def test_search_filters_message(self, admin_client, seeded_logs):
        response = admin_client.get("/admin/logs?q=sheet")
        details = response.json()["details"]
        assert details["total"] == 2

    def test_pagination(self, admin_client, seeded_logs):
        response = admin_client.get("/admin/logs?page=2&page_size=3")
        details = response.json()["details"]
        assert details["total"] == 4
        assert len(details["logs"]) == 1
        assert details["page"] == 2

    def test_page_size_capped(self, admin_client, seeded_logs):
        response = admin_client.get("/admin/logs?page_size=5000")
        assert response.json()["details"]["page_size"] <= 200
