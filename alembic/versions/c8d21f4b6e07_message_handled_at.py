"""add messages.handled_at so dedupe means handled rather than merely seen

The inbound audit row is committed before any side effect, so that a run which
dies mid-message cannot draft a second reply on the next poll. That made the
row's existence the dedupe key, which conflates two different facts: "we have
seen this mail" and "this mail has been answered or somebody has been told
about it".

The gap between them is a silent drop. Any failure after the row commits -
a Gmail error on the draft, a paused thread, an aborted run - left the mail
looking done, and the next run applied the marker label and moved on. For a
safeguarding mail that means nobody is ever told.

``handled_at`` closes it. It is set only when a message reaches a terminal
state, and dedupe now reads it, so an unfinished message is retried. Retrying
is safe because the reply cap lives on the conversation row, which is committed
with the outbound reply: a retried message cannot produce a second reply, only
the escalation it never got.

Existing rows are backfilled from ``created_at``. Every row that predates this
migration was written by the old code, which only ever wrote a row it went on
to act upon in the same breath, so treating them as handled preserves exactly
the behaviour they have now and avoids re-answering week-old mail on the first
poll after the upgrade.

Revision ID: c8d21f4b6e07
Revises: b4c17e2a9d31
Create Date: 2026-09-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d21f4b6e07"
down_revision: str | Sequence[str] | None = "b4c17e2a9d31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("handled_at", sa.DateTime(), nullable=True))
    op.create_index(
        op.f("ix_messages_handled_at"), "messages", ["handled_at"], unique=False
    )

    # Backfill: see the module docstring. Inbound rows only - an outbound row
    # is not something the dedupe check ever looks at.
    op.get_bind().execute(
        sa.text(
            "UPDATE messages SET handled_at = created_at "
            "WHERE direction = 'inbound' AND handled_at IS NULL"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_messages_handled_at"), table_name="messages")
    op.drop_column("messages", "handled_at")
