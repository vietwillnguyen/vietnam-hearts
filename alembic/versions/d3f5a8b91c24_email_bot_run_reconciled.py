"""add email_bot_runs.reconciled, the count of drafts a run settled

Each poll now decides what became of every draft still waiting on an answer,
before it lists anything new. That count is its own column rather than folded
into ``processed`` because the dashboard reads it as the health of the
draft-acceptance metric: a run that reconciles nothing for a fortnight means
the measurement the evaluation gate depends on is silently not being collected,
which looks identical to a fortnight in which nobody touched a draft.

Existing rows get 0, which is accurate: they ran before reconciliation existed.

Revision ID: d3f5a8b91c24
Revises: c8d21f4b6e07
Create Date: 2026-09-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f5a8b91c24"
down_revision: str | Sequence[str] | None = "c8d21f4b6e07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default so the NOT NULL holds for rows inserted by an instance
    # that has not restarted onto the new model yet, which is the window
    # between a migration and a rollout finishing.
    op.add_column(
        "email_bot_runs",
        sa.Column("reconciled", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("email_bot_runs", "reconciled")
