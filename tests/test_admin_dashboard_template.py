"""Tests for how the admin dashboard renders the Cron Job Schedules fields.

The three cron inputs were built by looking each key up in a nested
``{% for s in settings %}`` loop and assigning ``{% set current_value = s.value %}``
inside it. Jinja2 scopes ``{% set %}`` to the block it appears in, so the
assignment was discarded at ``{% endfor %}`` and every cron input rendered
``value=""`` - hidden behind a placeholder that happened to read like the
default expression.

Nothing looked wrong until the cadence became settings-driven: pressing
"Save All Settings" to edit any unrelated field PUT those empty strings back,
wiping all three CRON_* values, after which Apply to Cloud Scheduler could no
longer restore the schedule it was supposed to push.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import re

import pytest

from app.main import app
from app.services.settings_service import get_setting, set_setting

STORED_CRON_VALUES = {
    "CRON_SYNC_VOLUNTEERS": "0 */2 * * *",
    "CRON_SEND_WEEKLY_REMINDERS": "0 12 * * 0",
    "CRON_ROTATE_SCHEDULE": "0 * * * *",
}


@pytest.fixture
def admin_client(client):
    from app.dependencies.auth import get_current_admin_user

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "id": "test-admin",
        "email": "admin@vietnamhearts.org",
    }
    yield client
    app.dependency_overrides.pop(get_current_admin_user, None)


def find_input(html: str, key: str) -> str:
    """The rendered <input> tag for a settings key."""
    match = re.search(rf'<input\b[^>]*\bid="{re.escape(key)}"[^>]*>', html)
    assert match, f"no <input> rendered for {key}"
    return match.group(0)


def attr(tag: str, name: str) -> str | None:
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', tag)
    return match.group(1) if match else None


class TestCronFieldsRenderStoredValues:
    @pytest.mark.parametrize("key,expected", list(STORED_CRON_VALUES.items()))
    def test_cron_input_carries_the_stored_value(
        self, admin_client, test_db, key: str, expected: str
    ):
        for setting_key, value in STORED_CRON_VALUES.items():
            set_setting(test_db, setting_key, value)

        response = admin_client.get("/admin/dashboard")
        assert response.status_code == 200

        assert attr(find_input(response.text, key), "value") == expected

    def test_a_saved_edit_survives_a_reload(self, admin_client, test_db):
        """The round trip the admin actually performs: edit, save, reload."""
        for setting_key, value in STORED_CRON_VALUES.items():
            set_setting(test_db, setting_key, value)

        edited = "15 3 * * 1"
        response = admin_client.put(
            "/settings/CRON_ROTATE_SCHEDULE", json={"value": edited}
        )
        assert response.status_code == 200

        reloaded = admin_client.get("/admin/dashboard")
        assert (
            attr(find_input(reloaded.text, "CRON_ROTATE_SCHEDULE"), "value") == edited
        )

    def test_save_all_settings_does_not_wipe_the_cadence(self, admin_client, test_db):
        """The reported failure, end to end.

        "Save All Settings" PUTs back every field the page rendered, so blank
        cron inputs silently replaced all three schedules on any unrelated
        edit - and Apply to Cloud Scheduler then had nothing left to push.
        """
        for setting_key, value in STORED_CRON_VALUES.items():
            set_setting(test_db, setting_key, value)

        html = admin_client.get("/admin/dashboard").text
        rendered = {
            key: attr(find_input(html, key), "value") for key in STORED_CRON_VALUES
        }

        for key, value in rendered.items():
            assert (
                admin_client.put(f"/settings/{key}", json={"value": value}).status_code
                == 200
            )

        for key, expected in STORED_CRON_VALUES.items():
            assert get_setting(test_db, key) == expected

    def test_missing_value_looks_empty_rather_than_populated(
        self, admin_client, test_db
    ):
        """A genuinely unset field must not wear the default as a placeholder.

        The placeholder was the example expression, so an empty field was
        visually indistinguishable from a configured one - which is why the
        blanking went unnoticed.
        """
        set_setting(test_db, "CRON_ROTATE_SCHEDULE", "")

        response = admin_client.get("/admin/dashboard")
        tag = find_input(response.text, "CRON_ROTATE_SCHEDULE")

        assert attr(tag, "value") == ""
        assert attr(tag, "placeholder") != "0 * * * *"
