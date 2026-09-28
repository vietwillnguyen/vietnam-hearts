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
from app.utils.config_helper import ConfigHelper
from app.utils.schedule_dates import DEFAULT_TEACHING_DAYS_SETTING

CRON_KEYS = [
    "CRON_SYNC_VOLUNTEERS",
    "CRON_SEND_WEEKLY_REMINDERS",
    "CRON_ROTATE_SCHEDULE",
    "CRON_POLL_INBOX",
]

EXPECTED_DEFAULTS = {
    "CRON_SYNC_VOLUNTEERS": "0 */2 * * *",
    "CRON_SEND_WEEKLY_REMINDERS": "0 12 * * 0",
    # Reconciliation is idempotent, so it runs hourly rather than once a week -
    # a missed run or a week boundary self-corrects within the hour.
    "CRON_ROTATE_SCHEDULE": "0 * * * *",
    # Twice a day in Vietnam time. The bot only replies to mail already
    # waiting, so latency costs nothing and a rarer poll keeps both the model
    # spend and the blast radius of a bad run small.
    "CRON_POLL_INBOX": "0 8,18 * * *",
}

# Every setting the email channel design's Configuration table lists, with the
# default it gives, except the two caps and the sync cadence that arrive in
# later phases. Asserted exhaustively because a missing default means the
# pipeline silently runs on a code fallback that the dashboard cannot show or
# change.
EMAIL_BOT_DEFAULTS = {
    "EMAIL_BOT_MODE": "off",
    "EMAIL_BOT_AUTO_LANGUAGES": "en",
    "EMAIL_BOT_LAST_ERROR": "",
    "EMAIL_BOT_PER_RUN_CAP": "20",
    "ESCALATION_OWNER_EMAIL": "",
    "KNOWLEDGE_BASE_DOC_ID": "",
    "TRIAGE_CLASSIFIER": "jev",
    "TRIAGE_FALLBACK_MODEL": "gemini/gemini-3.5-flash-lite",
    "TRIAGE_CONFIDENCE_THRESHOLD": "0.6",
    "ANSWER_THRESHOLD": "0.5",
    "VOLUNTEER_SIGNUP_FORM_LINK": "",
    "CLASS_START_TIME": "09:30",
    "CLASS_END_TIME": "10:30",
    # From E3, with the send path they bound.
    "EMAIL_BOT_DAILY_SEND_CAP": "30",
    "EMAIL_BOT_PER_SENDER_DAILY_CAP": "2",
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

        response = admin_client.put(
            "/settings/SCHEDULE_SIGNUP_LINK", json={"value": ""}
        )

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


class TestScheduleTeachingDaysSetting:
    """The teaching week is configuration, not a hard-coded pair of weekdays."""

    def test_seeded_with_the_code_default(self, db):
        initialize_default_settings(db)
        assert (
            get_setting(db, "SCHEDULE_TEACHING_DAYS") == DEFAULT_TEACHING_DAYS_SETTING
        )
        assert ConfigHelper.get_schedule_teaching_days(db) == {"tue", "thu"}

    def test_a_configured_week_is_honoured(self, db):
        initialize_default_settings(db)
        set_setting(db, "SCHEDULE_TEACHING_DAYS", "Monday, Wednesday, Friday")
        assert ConfigHelper.get_schedule_teaching_days(db) == {"mon", "wed", "fri"}

    def test_no_session_falls_back_to_the_default(self):
        # Callers without a database (scripts, previews) must still get a
        # sane teaching week rather than an empty one.
        assert ConfigHelper.get_schedule_teaching_days(None) == {"tue", "thu"}

    def test_blanked_value_falls_back_to_the_default(self, db):
        initialize_default_settings(db)
        set_setting(db, "SCHEDULE_TEACHING_DAYS", "")
        assert ConfigHelper.get_schedule_teaching_days(db) == {"tue", "thu"}


class TestEmailBotDefaultSettings:
    def test_every_email_bot_key_is_created(self, db):
        initialize_default_settings(db)
        keys = {setting.key for setting in get_all_settings(db)}
        missing = sorted(set(EMAIL_BOT_DEFAULTS) - keys)
        assert missing == [], f"missing after initialize_default_settings: {missing}"

    @pytest.mark.parametrize("key,expected", sorted(EMAIL_BOT_DEFAULTS.items()))
    def test_the_default_matches_the_design(self, db, key, expected):
        initialize_default_settings(db)
        assert get_setting(db, key) == expected

    def test_the_bot_is_off_by_default(self, db):
        # The single most important default in this table: a fresh deployment
        # must not start answering a mailbox nobody has reviewed.
        initialize_default_settings(db)
        assert get_setting(db, "EMAIL_BOT_MODE") == "off"

    def test_only_english_may_send_automatically_by_default(self, db):
        # Vietnamese joins the list after native-speaker sign-off.
        initialize_default_settings(db)
        assert get_setting(db, "EMAIL_BOT_AUTO_LANGUAGES") == "en"

    def test_the_forward_recipient_starts_unset(self, db):
        # And the pipeline refuses to run while it is, rather than dropping
        # escalations silently.
        initialize_default_settings(db)
        assert get_setting(db, "ESCALATION_OWNER_EMAIL") == ""

    def test_the_send_caps_arrive_with_the_send_path(self, db):
        # They bound what auto mode can put in front of people, so they exist
        # from the same phase the send path does and not before: a knob that
        # does nothing is worse than no knob.
        initialize_default_settings(db)
        assert get_setting(db, "EMAIL_BOT_DAILY_SEND_CAP") == "30"
        assert get_setting(db, "EMAIL_BOT_PER_SENDER_DAILY_CAP") == "2"

    def test_the_e4_sync_cadence_is_not_created_yet(self, db):
        initialize_default_settings(db)
        keys = {setting.key for setting in get_all_settings(db)}
        assert "CRON_SYNC_KNOWLEDGE_BASE" not in keys

    @pytest.mark.parametrize("key", sorted(EMAIL_BOT_DEFAULTS))
    def test_every_key_has_a_description(self, db, key):
        initialize_default_settings(db)
        setting = db.query(Setting).filter(Setting.key == key).first()
        assert setting.description
        assert len(setting.description) > 20

    def test_initialisation_stays_idempotent_with_the_new_keys(self, db):
        initialize_default_settings(db)
        set_setting(db, "EMAIL_BOT_MODE", "draft")
        initialize_default_settings(db)
        # A value an admin chose is never clobbered by a later startup.
        assert get_setting(db, "EMAIL_BOT_MODE") == "draft"


class TestEmailBotSettingsLoader:
    def test_it_reads_the_seeded_defaults(self, db):
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        loaded = EmailBotSettings.load(db)

        assert loaded.mode == "off"
        assert loaded.auto_languages == frozenset({"en"})
        assert loaded.triage_classifier == "jev"
        assert loaded.triage_confidence_threshold == 0.6
        assert loaded.answer_threshold == 0.5
        assert loaded.per_run_cap == 20

    def test_it_reads_fresh_every_time(self, db):
        # The kill switch is worthless if the value is cached for the process
        # lifetime.
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        assert EmailBotSettings.load(db).mode == "off"

        set_setting(db, "EMAIL_BOT_MODE", "draft")
        assert EmailBotSettings.load(db).mode == "draft"

    @pytest.mark.parametrize(
        "stored,expected",
        [
            ("en", frozenset({"en"})),
            ("en,vi", frozenset({"en", "vi"})),
            ("EN, VI", frozenset({"en", "vi"})),
            # An empty list would silently mean "send nothing automatically",
            # which reads as a bug rather than a decision.
            ("", frozenset({"en"})),
            ("   ", frozenset({"en"})),
        ],
    )
    def test_the_auto_language_list_is_parsed_forgivingly(self, db, stored, expected):
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        set_setting(db, "EMAIL_BOT_AUTO_LANGUAGES", stored)
        assert EmailBotSettings.load(db).auto_languages == expected

    @pytest.mark.parametrize("bad", ["", "  ", "half", "1.5", "-0.2", "0.6.1"])
    def test_an_unusable_threshold_falls_back_to_the_design_default(self, db, bad):
        # These are free-text fields on an admin form, so a blank or mistyped
        # value is a realistic mistake. The safe reading is the default, not an
        # exception that aborts the poll.
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        set_setting(db, "TRIAGE_CONFIDENCE_THRESHOLD", bad)
        assert EmailBotSettings.load(db).triage_confidence_threshold == 0.6

    @pytest.mark.parametrize("bad", ["", "lots", "0", "-5", "3.7"])
    def test_an_unusable_cap_falls_back_to_the_design_default(self, db, bad):
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        set_setting(db, "EMAIL_BOT_PER_RUN_CAP", bad)
        assert EmailBotSettings.load(db).per_run_cap == 20

    def test_may_send_language_honours_the_list(self, db):
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        set_setting(db, "EMAIL_BOT_AUTO_LANGUAGES", "en")
        loaded = EmailBotSettings.load(db)

        assert loaded.may_send_language("en")
        assert not loaded.may_send_language("vi")
        assert not loaded.may_send_language("other")


class TestProductionValidationIgnoresTheNewEnvVars:
    @pytest.mark.parametrize(
        "name",
        [
            "EMAIL_BOT_ENABLED",
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "TYPESAFE_API_KEY",
            "DISCORD_WEBHOOK_URL",
            "ANTHROPIC_API_KEY",
        ],
    )
    def test_none_of_them_is_required_in_production(self, name):
        # The feature is off by default, so a deployment that never turns it on
        # must not be blocked from starting by a key it has no use for.
        from app.config import REQUIRED_ENV_VARS

        assert name not in REQUIRED_ENV_VARS

    def test_production_validation_passes_with_all_of_them_unset(self, monkeypatch):
        import app.config as config

        for name in (
            "EMAIL_BOT_ENABLED",
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "TYPESAFE_API_KEY",
            "DISCORD_WEBHOOK_URL",
            "ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(name, raising=False)
        for name in config.REQUIRED_ENV_VARS:
            monkeypatch.setenv(name, "present")
        monkeypatch.setenv("ENVIRONMENT", "production")

        config.validate_config()

    def test_the_bot_is_disabled_unless_the_flag_is_exactly_true(self, monkeypatch):
        import importlib

        import app.config as config

        for value, expected in (
            ("true", True),
            ("TRUE", True),
            ("  true  ", True),
            ("1", False),
            ("yes", False),
            ("", False),
        ):
            monkeypatch.setenv("EMAIL_BOT_ENABLED", value)
            reloaded = importlib.reload(config)
            assert reloaded.EMAIL_BOT_ENABLED is expected, value

        monkeypatch.delenv("EMAIL_BOT_ENABLED", raising=False)
        importlib.reload(config)


class TestTheSendCapsLoadIntoTheSettings:
    def test_the_defaults_are_the_designs(self, db):
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        loaded = EmailBotSettings.load(db)

        assert loaded.daily_send_cap == 30
        assert loaded.per_sender_daily_cap == 2

    def test_a_configured_cap_is_honoured(self, db):
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        set_setting(db, "EMAIL_BOT_DAILY_SEND_CAP", "10")
        assert EmailBotSettings.load(db).daily_send_cap == 10

    @pytest.mark.parametrize("bad", ["", "  ", "lots", "0", "-5", "2.5"])
    def test_an_unusable_cap_falls_back_to_the_design_default(self, db, bad):
        # A blank or mistyped cap must not read as "no limit".
        from app.services.email_bot.settings import EmailBotSettings

        initialize_default_settings(db)
        set_setting(db, "EMAIL_BOT_DAILY_SEND_CAP", bad)
        set_setting(db, "EMAIL_BOT_PER_SENDER_DAILY_CAP", bad)
        loaded = EmailBotSettings.load(db)

        assert loaded.daily_send_cap == 30
        assert loaded.per_sender_daily_cap == 2
