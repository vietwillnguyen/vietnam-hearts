"""keep inbox conversations, encrypted, with a record of every view and deletion

The owner decided on 2026-11-22 that the inbox bot keeps a full record of each
conversation for traceability. Until now it kept what it did with a mail and
never the mail. This revision adds the place to keep it:

- ``messages.subject_encrypted``, ``messages.body_encrypted`` and
  ``messages.sender_address_encrypted``: application-encrypted
  (``app/utils/field_crypto.py``) subject, plain-text body and sender address.
  ``body_encrypted`` holds the bot's own reply on an outbound row as well, so
  there is one text column and it is encrypted.
- ``messages.sender_key``: the SHA-256 of the sender's address on each inbound
  row, so a deletion request by address also finds a person's mail in a thread
  somebody else started.
- ``messages.content_erased_at``: when the text and address were deleted on
  request. The rest of the row - ids, timestamps, category, tier, action -
  stays, because idempotence and the metrics read it.
- ``conversation_views`` and ``conversation_deletions``: who opened which
  conversation and when, and who deleted what and when. Neither has a column
  that could hold message content.

``messages.text`` is dropped rather than kept beside ``body_encrypted``. It only
ever held outbound bot text (the old code never wrote inbound bodies), but a
plaintext column next to an encrypted one is an invitation to write the same
text twice. Any text it does hold is encrypted into ``body_encrypted`` first,
which needs ``CONVERSATION_ENCRYPTION_KEYS``; with rows to move and no key the
upgrade stops *before* changing anything, rather than discard them. With no
rows to move - the production case, since the bot has never run - no key is
needed.

The downgrade restores ``messages.text`` for outbound rows only, decrypting if
the key is available. Inbound content is dropped, never decrypted back into
plaintext: the schema being restored promised inbound text was not stored.

Revision ID: 5f3c9a1d7e20
Revises: 09213ade570c
Create Date: 2026-11-22 00:00:00.000000

"""

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f3c9a1d7e20"
down_revision: str | Sequence[str] | None = "09213ade570c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BODY_CONTEXT = "messages.body_encrypted"

_messages = sa.table(
    "messages",
    sa.column("id", sa.Integer),
    sa.column("direction", sa.String),
    sa.column("text", sa.Text),
    sa.column("body_encrypted", sa.Text),
)


def _cipher():
    # Imported here so a database with nothing to move never touches the key.
    from app.utils.field_crypto import parse_keys

    return parse_keys(os.environ.get("CONVERSATION_ENCRYPTION_KEYS"))


def _rows(bind, query) -> list:
    if op.get_context().as_sql:
        return []
    return bind.execute(query).all()


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # Checked before any DDL, so a missing key leaves the database untouched.
    # An offline (--sql) run has no rows to read and emits the DDL only.
    to_move = _rows(
        bind,
        sa.select(_messages.c.id, _messages.c.text).where(_messages.c.text.isnot(None)),
    )
    cipher = _cipher() if to_move else None

    # Plain ALTERs rather than batch mode: both Postgres and SQLite 3.35+
    # support them, and batch mode cannot run offline (--sql) on SQLite.
    for name, kind in (
        ("subject_encrypted", sa.Text()),
        ("body_encrypted", sa.Text()),
        ("sender_address_encrypted", sa.Text()),
        ("sender_key", sa.String()),
        ("content_erased_at", sa.DateTime()),
    ):
        op.add_column("messages", sa.Column(name, kind, nullable=True))
    op.create_index(
        op.f("ix_messages_sender_key"), "messages", ["sender_key"], unique=False
    )

    for row_id, text in to_move:
        bind.execute(
            _messages.update()
            .where(_messages.c.id == row_id)
            .values(body_encrypted=cipher.encrypt(text, BODY_CONTEXT))
        )

    op.drop_column("messages", "text")

    op.create_table(
        "conversation_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("viewer_id", sa.String(), nullable=False),
        sa.Column("viewer_email", sa.String(), nullable=True),
        sa.Column("viewed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversation_views_id"), "conversation_views", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_conversation_views_conversation_id"),
        "conversation_views",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_views_viewed_at"),
        "conversation_views",
        ["viewed_at"],
        unique=False,
    )

    op.create_table(
        "conversation_deletions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("conversation_ids", sa.JSON(), nullable=False),
        sa.Column("messages_erased", sa.Integer(), nullable=False),
        sa.Column("sender_key", sa.String(), nullable=True),
        sa.Column("requested_by_id", sa.String(), nullable=False),
        sa.Column("requested_by_email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversation_deletions_id"),
        "conversation_deletions",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_deletions_created_at"),
        "conversation_deletions",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()

    op.drop_index(
        op.f("ix_conversation_deletions_created_at"),
        table_name="conversation_deletions",
    )
    op.drop_index(
        op.f("ix_conversation_deletions_id"), table_name="conversation_deletions"
    )
    op.drop_table("conversation_deletions")
    op.drop_index(
        op.f("ix_conversation_views_viewed_at"), table_name="conversation_views"
    )
    op.drop_index(
        op.f("ix_conversation_views_conversation_id"),
        table_name="conversation_views",
    )
    op.drop_index(op.f("ix_conversation_views_id"), table_name="conversation_views")
    op.drop_table("conversation_views")

    op.add_column("messages", sa.Column("text", sa.Text(), nullable=True))

    outbound = _rows(
        bind,
        sa.select(_messages.c.id, _messages.c.body_encrypted).where(
            _messages.c.direction == "outbound",
            _messages.c.body_encrypted.isnot(None),
        ),
    )
    if outbound:
        try:
            cipher = _cipher()
        except ValueError:
            # No key, no plaintext: the rows come back with text NULL, which
            # the old code reads as "no draft text to compare against".
            cipher = None
        if cipher is not None:
            for row_id, token in outbound:
                bind.execute(
                    _messages.update()
                    .where(_messages.c.id == row_id)
                    .values(
                        text=cipher.decrypt(token, BODY_CONTEXT, ids=f"id={row_id}")
                    )
                )

    op.drop_index(op.f("ix_messages_sender_key"), table_name="messages")
    for name in (
        "content_erased_at",
        "sender_key",
        "sender_address_encrypted",
        "body_encrypted",
        "subject_encrypted",
    ):
        op.drop_column("messages", name)
