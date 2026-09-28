"""Tests for data migrations that repair already-deployed settings rows.

initialize_default_settings() only inserts keys that are missing, so a changed
default in code never reaches a database that already has the row. Anything
that has to correct existing data therefore lives in an Alembic migration, and
those migrations get exercised here rather than only on a fresh database (where
they touch no rows at all and so prove nothing).
"""

import importlib.util
import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.config import PROJECT_ROOT
from app.models import Setting

MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "9c2d51f0ab74_hourly_rotate_schedule_default.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location(
        "hourly_rotate_schedule_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db(test_db):
    return test_db


def run(db, migration, direction: str) -> None:
    """Run one direction of the migration against the test session's connection."""
    context = MigrationContext.configure(db.connection())
    with Operations.context(context):
        getattr(migration, direction)()
    # The migration writes raw SQL, so the session's identity map is stale.
    db.expire_all()


def seed(db, value: str, description: str = "whatever") -> None:
    db.add(Setting(key="CRON_ROTATE_SCHEDULE", value=value, description=description))
    db.commit()


def stored(db) -> Setting:
    return db.query(Setting).filter(Setting.key == "CRON_ROTATE_SCHEDULE").first()


class TestHourlyRotateScheduleDefault:
    def test_untouched_weekly_default_is_retuned_to_hourly(self, db, migration):
        seed(db, migration.WEEKLY_VALUE, migration.WEEKLY_DESCRIPTION)

        run(db, migration, "upgrade")

        setting = stored(db)
        assert setting.value == migration.HOURLY_VALUE
        assert setting.description == migration.HOURLY_DESCRIPTION

    def test_admin_customized_value_is_left_alone(self, db, migration):
        # The point of the conditional: pushing a deliberate '0 */6 * * *' back
        # to hourly would silently override an operator's decision.
        seed(db, "0 */6 * * *", "hand-tuned")

        run(db, migration, "upgrade")

        setting = stored(db)
        assert setting.value == "0 */6 * * *"
        assert setting.description == "hand-tuned"

    def test_upgrade_is_idempotent(self, db, migration):
        seed(db, migration.WEEKLY_VALUE, migration.WEEKLY_DESCRIPTION)

        run(db, migration, "upgrade")
        run(db, migration, "upgrade")

        assert stored(db).value == migration.HOURLY_VALUE

    def test_missing_row_is_not_created(self, db, migration):
        # A fresh database gets the hourly value from
        # initialize_default_settings; the migration only repairs existing rows.
        run(db, migration, "upgrade")

        assert stored(db) is None

    def test_downgrade_reverses_the_upgrade(self, db, migration):
        seed(db, migration.WEEKLY_VALUE, migration.WEEKLY_DESCRIPTION)

        run(db, migration, "upgrade")
        run(db, migration, "downgrade")

        setting = stored(db)
        assert setting.value == migration.WEEKLY_VALUE
        assert setting.description == migration.WEEKLY_DESCRIPTION

    def test_downgrade_leaves_a_customized_value_alone(self, db, migration):
        seed(db, "0 */6 * * *", "hand-tuned")

        run(db, migration, "downgrade")

        assert stored(db).value == "0 */6 * * *"


EMAIL_CHANNEL_MIGRATION = (
    PROJECT_ROOT / "alembic" / "versions" / "b4c17e2a9d31_email_channel_tables.py"
)


@pytest.fixture(scope="module")
def email_channel_migration():
    spec = importlib.util.spec_from_file_location(
        "email_channel_tables_migration", EMAIL_CHANNEL_MIGRATION
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EMAIL_CHANNEL_TABLES = ("messages", "conversations", "email_bot_runs")


@pytest.fixture
def blank_slate(test_db, email_channel_migration):
    """A session whose email-channel tables do not exist yet.

    The shared ``test_db`` fixture builds every table from the models with
    ``create_all``, which is right for the rest of the suite and wrong here:
    this migration's job is to create those tables from nothing, so they have
    to be absent before it runs. The settings rows go too, so the seeding
    assertions are not looking at leftovers.

    CI separately runs ``alembic upgrade head`` followed by ``alembic check``
    against a genuinely empty database, which is the authoritative proof that
    the migration and the models agree. What these tests add is the
    constraint-level and round-trip detail that ``check`` does not inspect.
    """
    from sqlalchemy import text

    # messages first: it carries the foreign key to conversations.
    for table in EMAIL_CHANNEL_TABLES:
        test_db.connection().execute(text(f"DROP TABLE IF EXISTS {table}"))
    for key, _value, _description in email_channel_migration.NEW_SETTINGS:
        test_db.connection().execute(
            text("DELETE FROM settings WHERE key = :key"), {"key": key}
        )
    test_db.expire_all()
    return test_db


class TestEmailChannelTables:
    """The three tables, their constraints, and a clean round trip.

    The two constraints asserted here are safety controls rather than tidiness:
    the unique index on the Gmail message id is the pipeline's idempotence key,
    and the unique constraint on (channel, thread_key) is what makes "one
    conversation per thread" true at the database rather than only inside one
    function.
    """

    def _inspector(self, db):
        from sqlalchemy import inspect

        return inspect(db.connection())

    def test_upgrade_creates_the_three_tables(
        self, blank_slate, email_channel_migration
    ):
        db = blank_slate
        assert not set(self._inspector(db).get_table_names()) & set(
            EMAIL_CHANNEL_TABLES
        )
        run(db, email_channel_migration, "upgrade")
        names = set(self._inspector(db).get_table_names())
        assert {"conversations", "messages", "email_bot_runs"} <= names

    def test_the_gmail_message_id_index_is_unique(
        self, blank_slate, email_channel_migration
    ):
        db = blank_slate
        run(db, email_channel_migration, "upgrade")
        indexes = self._inspector(db).get_indexes("messages")
        idempotence = [
            index
            for index in indexes
            if index["name"] == "uq_messages_provider_message_id"
        ]
        assert idempotence, "the idempotence index is missing"
        # SQLAlchemy's SQLite reflection reports unique as 1 rather than True.
        assert bool(idempotence[0]["unique"]) is True
        assert idempotence[0]["column_names"] == ["provider_message_id"]

    def test_the_conversation_thread_constraint_is_unique(
        self, blank_slate, email_channel_migration
    ):
        db = blank_slate
        run(db, email_channel_migration, "upgrade")
        constraints = self._inspector(db).get_unique_constraints("conversations")
        by_name = {c["name"]: c for c in constraints}
        assert "uq_conversations_channel_thread" in by_name
        assert by_name["uq_conversations_channel_thread"]["column_names"] == [
            "channel",
            "thread_key",
        ]

    def test_the_settings_are_seeded(self, blank_slate, email_channel_migration):
        db = blank_slate
        # initialize_default_settings only inserts on startup, so a deployment
        # that upgrades before it restarts would otherwise have the tables
        # without the settings that drive them.
        run(db, email_channel_migration, "upgrade")
        db.expire_all()
        stored = {setting.key: setting.value for setting in db.query(Setting).all()}
        assert stored["EMAIL_BOT_MODE"] == "off"
        assert stored["EMAIL_BOT_AUTO_LANGUAGES"] == "en"
        assert stored["CRON_POLL_INBOX"] == "0 8,18 * * *"

    def test_seeding_never_clobbers_a_value_an_admin_chose(
        self, blank_slate, email_channel_migration
    ):
        db = blank_slate
        db.add(Setting(key="EMAIL_BOT_MODE", value="draft", description="chosen"))
        db.commit()

        run(db, email_channel_migration, "upgrade")
        db.expire_all()

        setting = db.query(Setting).filter(Setting.key == "EMAIL_BOT_MODE").first()
        assert setting.value == "draft"

    def test_downgrade_removes_the_tables_and_the_settings(
        self, blank_slate, email_channel_migration
    ):
        db = blank_slate
        run(db, email_channel_migration, "upgrade")
        run(db, email_channel_migration, "downgrade")
        db.expire_all()

        names = set(self._inspector(db).get_table_names())
        assert not {"conversations", "messages", "email_bot_runs"} & names
        # Leaving the settings behind would show an operator a dashboard full of
        # knobs for a bot that cannot run.
        assert db.query(Setting).filter(Setting.key == "EMAIL_BOT_MODE").first() is None

    def test_the_round_trip_is_repeatable(self, blank_slate, email_channel_migration):
        db = blank_slate
        for _ in range(2):
            run(db, email_channel_migration, "upgrade")
            run(db, email_channel_migration, "downgrade")

        names = set(self._inspector(db).get_table_names())
        assert "conversations" not in names

    def test_it_revises_the_previous_head(self, email_channel_migration):
        assert email_channel_migration.down_revision == "9c2d51f0ab74"
