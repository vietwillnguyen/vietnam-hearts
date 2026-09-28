"""seed the two send caps, which bound what E3's auto mode can put in front of people

Two separate limits because they bound two different failures.

``EMAIL_BOT_DAILY_SEND_CAP`` bounds the account's output. Gmail's own
500-per-day limit is shared with the weekly reminder blast, so an inbox bot that
ran away would take the reminders down with it.

``EMAIL_BOT_PER_SENDER_DAILY_CAP`` bounds the reply loop. A misbehaving
auto-responder on the other side cannot extract more than this many replies
however many times it writes, whatever the header guards miss.

Seeded here as well as in initialize_default_settings() because that function
only inserts on startup, so a deployment that upgrades before it restarts would
otherwise have the send path without the limits on it. Insert-if-absent, so a
value an admin has already chosen is never clobbered.

Revision ID: e7a4c93d2f18
Revises: e5b92c7a1f38
Create Date: 2026-09-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a4c93d2f18"
down_revision: str | Sequence[str] | None = "e5b92c7a1f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_SETTINGS: tuple[tuple[str, str, str], ...] = (
    (
        "EMAIL_BOT_DAILY_SEND_CAP",
        "30",
        "Most replies the inbox bot will send in one Vietnam-local day.",
    ),
    (
        "EMAIL_BOT_PER_SENDER_DAILY_CAP",
        "2",
        "Most threads one sender can be replied to in a day; the loop bound.",
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
    """Downgrade schema.

    The caps go with the send path they bound. Leaving them behind would show an
    operator limits on something that can no longer send.
    """
    bind = op.get_bind()
    for key, _value, _description in NEW_SETTINGS:
        bind.execute(sa.text("DELETE FROM settings WHERE key = :key"), {"key": key})
