"""add email_bot_runs.capped, the replies auto mode held back

A reply drafted while the mode says send is not the same event as a reply
drafted because the mode says draft, and the dashboard has to be able to tell
them apart. Either a cap was reached or the language is not signed off for
automatic sending; both mean somebody should look, and both would otherwise
read as an ordinary draft-mode run.

Existing rows get 0, which is accurate: they ran before anything could send.

Revision ID: f1b8e042a7c5
Revises: e7a4c93d2f18
Create Date: 2026-09-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1b8e042a7c5"
down_revision: str | Sequence[str] | None = "e7a4c93d2f18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default so the NOT NULL holds for rows written by an instance that
    # has not restarted onto the new model yet, which is the window between a
    # migration and a rollout finishing.
    op.add_column(
        "email_bot_runs",
        sa.Column("capped", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("email_bot_runs", "capped")
