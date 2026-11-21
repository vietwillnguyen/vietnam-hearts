"""merge email bot and volunteer activity heads

Revision ID: 09213ade570c
Revises: a2e6d17c4b83, c4a18b7f0d52
Create Date: 2026-11-21 12:34:26.724851

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "09213ade570c"
down_revision: str | Sequence[str] | None = ("a2e6d17c4b83", "c4a18b7f0d52")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
