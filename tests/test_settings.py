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
    "CRON_ROTATE_SCHEDULE": "0 17 * * 5",
}


@pytest.fixture
def db(test_db):
    return test_db


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
