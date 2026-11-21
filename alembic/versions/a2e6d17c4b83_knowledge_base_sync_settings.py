"""seed the daily knowledge-base sync cadence and its freshness fields

The point of the job is that an edit a coordinator makes to the curated doc is
answerable the next morning with nobody deploying or clicking anything. The
cadence is a setting so it can be retuned from the dashboard like the others.

``KNOWLEDGE_BASE_LAST_SYNC`` and ``KNOWLEDGE_BASE_CHUNKS`` exist so that
staleness is visible. A daily job that silently stopped looks exactly like a day
on which nobody edited the doc, and the bot would go on answering from an
increasingly old copy without anything saying so. Only a *successful* sync
writes them, so "last synced two days ago" stays true rather than being
overwritten with a time at which nothing was ingested.

Seeded here as well as in initialize_default_settings() because that function
only inserts on startup, so a deployment that upgrades before it restarts would
otherwise have the scheduler job without the cadence that drives it.

Revision ID: a2e6d17c4b83
Revises: f1b8e042a7c5
Create Date: 2026-09-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2e6d17c4b83"
down_revision: str | Sequence[str] | None = "f1b8e042a7c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_SETTINGS: tuple[tuple[str, str, str], ...] = (
    (
        "CRON_SYNC_KNOWLEDGE_BASE",
        "0 5 * * *",
        "Cron schedule for re-reading the curated knowledge-base doc.",
    ),
    (
        "KNOWLEDGE_BASE_LAST_SYNC",
        "",
        "When the knowledge base was last read successfully.",
    ),
    (
        "KNOWLEDGE_BASE_CHUNKS",
        "",
        "How many chunks the last successful sync produced.",
    ),
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    for key, value, description in NEW_SETTINGS:
        bind.execute(
            sa.text(
                "INSERT INTO settings (key, value, description) "
                "SELECT :key, :value, :description "
                "WHERE NOT EXISTS (SELECT 1 FROM settings WHERE key = :key)"
            ),
            {"key": key, "value": value, "description": description},
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    for key, _value, _description in NEW_SETTINGS:
        bind.execute(sa.text("DELETE FROM settings WHERE key = :key"), {"key": key})
