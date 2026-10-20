"""add volunteers.last_active_at for weekly-reminder recency

The weekly reminder went to every accepted volunteer who had never
unsubscribed, with no recency filter, because nothing in the schema recorded
whether a volunteer still turns up. This adds that column.

It is nullable with no default, and NULL is meaningful: it marks a volunteer
the schedule-sheet name matcher has never placed, whose recency is then
measured from their sign-up date instead. Backfilling it to created_at here
would destroy that distinction and make every historic volunteer look like one
the matcher had already checked.

scripts/backfill_volunteer_activity.py fills the column from the schedule
tabs, including the hidden past weeks.

Revision ID: c4a18b7f0d52
Revises: 9c2d51f0ab74
Create Date: 2026-10-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a18b7f0d52"
down_revision: str | Sequence[str] | None = "9c2d51f0ab74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "volunteers", sa.Column("last_active_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("volunteers", "last_active_at")
