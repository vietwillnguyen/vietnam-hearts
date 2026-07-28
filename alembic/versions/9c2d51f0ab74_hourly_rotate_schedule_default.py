"""retune the stale weekly CRON_ROTATE_SCHEDULE default to hourly

initialize_default_settings() only inserts keys that are missing, so changing a
default in code never reaches a database that already has the row. Every
already-deployed environment therefore kept CRON_ROTATE_SCHEDULE at the weekly
'0 17 * * 5' - and since POST /admin/sync-cron-schedules pushes that stored
value into Cloud Scheduler, applying the settings would have dragged the job
back to weekly instead of forward to the new hourly reconciliation cadence.

The update is conditional on the row still holding the old default, so a value
an admin deliberately chose is never clobbered.

Revision ID: 9c2d51f0ab74
Revises: 57d3bfee129a
Create Date: 2026-07-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c2d51f0ab74"
down_revision: str | Sequence[str] | None = "57d3bfee129a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTING_KEY = "CRON_ROTATE_SCHEDULE"

WEEKLY_VALUE = "0 17 * * 5"
WEEKLY_DESCRIPTION = (
    "Cron schedule for rotating schedule sheets (default: every Friday at 5:00 PM)"
)

HOURLY_VALUE = "0 * * * *"
HOURLY_DESCRIPTION = (
    "Cron schedule for reconciling schedule sheets to the current week "
    "(default: hourly). The operation is idempotent, so running it often "
    "just means a missed run, a manual edit, or a week boundary is "
    "corrected within the hour instead of days later."
)


def _retune(from_value: str, to_value: str, to_description: str) -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE settings "
            "SET value = :to_value, description = :to_description "
            "WHERE key = :key AND value = :from_value"
        ),
        {
            "key": SETTING_KEY,
            "from_value": from_value,
            "to_value": to_value,
            "to_description": to_description,
        },
    )


def upgrade() -> None:
    """Move the untouched weekly default to hourly."""
    _retune(WEEKLY_VALUE, HOURLY_VALUE, HOURLY_DESCRIPTION)


def downgrade() -> None:
    """Move an untouched hourly value back to the weekly default."""
    _retune(HOURLY_VALUE, WEEKLY_VALUE, WEEKLY_DESCRIPTION)
