"""add messages.draft_checked_at so every pending draft gets reconciled in turn

Each poll settles a bounded batch of pending drafts. Taken oldest first, a
draft the captain never touches stays pending forever and keeps its place at
the front, so once enough of them pile up no newer draft is ever looked at
again and the acceptance rate is silently computed from old drafts alone.

Recording when each draft was last checked lets the batch rotate: least
recently checked first, never-checked before everything else.

Existing rows stay NULL, which puts them at the front of the first rotation.

Revision ID: e5b92c7a1f38
Revises: d3f5a8b91c24
Create Date: 2026-09-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b92c7a1f38"
down_revision: str | Sequence[str] | None = "d3f5a8b91c24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "messages", sa.Column("draft_checked_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "draft_checked_at")
