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
