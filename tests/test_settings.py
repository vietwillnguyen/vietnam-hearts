import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.models import Setting
from app.services.settings_service import (
    get_all_settings,
    get_setting,
    initialize_default_settings,
    set_setting,
)

CRON_KEYS = [
    "CRON_SYNC_VOLUNTEERS",
    "CRON_SEND_WEEKLY_REMINDERS",
    "CRON_ROTATE_SCHEDULE",
]

EXPECTED_DEFAULTS = {
    "CRON_SYNC_VOLUNTEERS": "0 */2 * * *",
    "CRON_SEND_WEEKLY_REMINDERS": "0 12 * * 0",
    # Reconciliation is idempotent, so it runs hourly rather than once a week -
    # a missed run or a week boundary self-corrects within the hour.
    "CRON_ROTATE_SCHEDULE": "0 * * * *",
}


@pytest.fixture
def db(test_db):
    return test_db


@pytest.fixture
def admin_client(client):
    from app.dependencies.auth import get_current_admin_user
    from app.main import app

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "id": "test-admin",
        "email": "admin@vietnamhearts.org",
    }
    yield client
    app.dependency_overrides.pop(get_current_admin_user, None)


class TestCronDefaultSettings:
    def test_all_cron_keys_created_after_init(self, db):
        initialize_default_settings(db)
        keys = {s.key for s in get_all_settings(db)}
        for cron_key in CRON_KEYS:
            assert (
                cron_key in keys
            ), f"{cron_key} missing after initialize_default_settings"

    @pytest.mark.parametrize("key,expected", list(EXPECTED_DEFAULTS.items()))
    def test_cron_default_value(self, db, key: str, expected: str):
        initialize_default_settings(db)
        value = get_setting(db, key)
        assert value == expected, f"{key}: expected '{expected}', got '{value}'"

    def test_init_is_idempotent(self, db):
        initialize_default_settings(db)
        initialize_default_settings(db)
        settings = [s for s in get_all_settings(db) if s.key in CRON_KEYS]
        assert len(settings) == len(CRON_KEYS)


class TestCronSettingsPersistence:
    def test_update_cron_setting_persists(self, db):
        initialize_default_settings(db)
        new_expr = "30 8 * * 2"
        set_setting(db, "CRON_SYNC_VOLUNTEERS", new_expr)
        assert get_setting(db, "CRON_SYNC_VOLUNTEERS") == new_expr

    def test_update_does_not_affect_other_cron_keys(self, db):
        initialize_default_settings(db)
        set_setting(db, "CRON_SYNC_VOLUNTEERS", "30 8 * * 2")
        assert (
            get_setting(db, "CRON_SEND_WEEKLY_REMINDERS")
            == EXPECTED_DEFAULTS["CRON_SEND_WEEKLY_REMINDERS"]
        )

    def test_get_setting_returns_none_for_missing_key(self, db):
        assert get_setting(db, "CRON_NONEXISTENT") is None

    def test_get_setting_returns_default_for_missing_key(self, db):
        assert get_setting(db, "CRON_NONEXISTENT", "fallback") == "fallback"

    def test_cron_descriptions_are_set(self, db):
        initialize_default_settings(db)
        for key in CRON_KEYS:
            setting = db.query(Setting).filter(Setting.key == key).first()
            assert setting is not None
            assert setting.description, f"{key} has no description"


class TestBlankCronValuesAreRejected:
    """A CRON_* key drives a live Cloud Scheduler job, so blanking one is
    never a meaningful edit - it just deletes the cadence.

    The dashboard's "Save All Settings" PUTs every field on the form at once,
    so one field rendering empty was enough to wipe all three schedules in a
    single routine click. Refusing the write here means no client can silently
    erase a cadence, whatever the form happens to render.
    """

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    @pytest.mark.parametrize("key", CRON_KEYS)
    def test_blank_value_is_refused(self, admin_client, db, key: str, blank: str):
        initialize_default_settings(db)
        original = get_setting(db, key)

        response = admin_client.put(f"/settings/{key}", json={"value": blank})

        assert response.status_code == 400
        assert key in response.json()["detail"]
        assert get_setting(db, key) == original

    def test_a_real_expression_still_saves(self, admin_client, db):
        initialize_default_settings(db)

        response = admin_client.put(
            "/settings/CRON_ROTATE_SCHEDULE", json={"value": "15 3 * * 1"}
        )

        assert response.status_code == 200
        assert get_setting(db, "CRON_ROTATE_SCHEDULE") == "15 3 * * 1"

    def test_non_cron_settings_may_still_be_blanked(self, admin_client, db):
        """Only the cadence keys are protected; this is not a global rule."""
        initialize_default_settings(db)

        response = admin_client.put("/settings/SCHEDULE_SIGNUP_LINK", json={"value": ""})

        assert response.status_code == 200
        assert get_setting(db, "SCHEDULE_SIGNUP_LINK") == ""


class TestDefaultDescriptionRefresh:
    """Descriptions are this code's documentation of a key, not user data.

    Init only ever inserted missing keys, so rewording a description left
    every already-deployed database displaying the old text indefinitely -
    which is how the dashboard kept describing hourly reconciliation as
    "rotating schedule sheets ... every Friday at 5:00 PM".
    """

    def test_stale_description_is_refreshed(self, db):
        initialize_default_settings(db)
        setting = (
            db.query(Setting).filter(Setting.key == "CRON_ROTATE_SCHEDULE").first()
        )
        current_description = setting.description
        setting.description = "Cron schedule for rotating schedule sheets"
        db.commit()

        initialize_default_settings(db)

        refreshed = (
            db.query(Setting).filter(Setting.key == "CRON_ROTATE_SCHEDULE").first()
        )
        assert refreshed.description == current_description

    def test_refresh_never_touches_a_customized_value(self, db):
        initialize_default_settings(db)
        set_setting(db, "CRON_ROTATE_SCHEDULE", "0 */6 * * *")
        db.query(Setting).filter(
            Setting.key == "CRON_ROTATE_SCHEDULE"
        ).first().description = "stale"
        db.commit()

        initialize_default_settings(db)

        assert get_setting(db, "CRON_ROTATE_SCHEDULE") == "0 */6 * * *"
