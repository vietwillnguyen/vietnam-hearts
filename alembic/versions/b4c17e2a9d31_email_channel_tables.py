"""add the email channel tables: conversations, messages, email_bot_runs

The three tables the inbound answer engine needs. Channel-neutral on purpose:
``conversations.channel`` and ``messages`` carry no email-only columns that
Messenger could not leave null, so the Messenger migration onto the same engine
needs no second schema.

Two constraints in here are safety controls rather than tidiness. The unique
index on ``messages.provider_message_id`` is the pipeline's idempotence key: the
inbound row is committed before any side effect, so a run that dies after
drafting cannot draft again. Both SQLite and Postgres treat NULLs as distinct in
a unique index, which is what lets the outbound rows share the column. The
unique constraint on ``(channel, thread_key)`` is what makes "one conversation
per thread" true at the database rather than only inside one function.

Revision ID: b4c17e2a9d31
Revises: 9c2d51f0ab74
Create Date: 2026-09-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c17e2a9d31"
down_revision: str | Sequence[str] | None = "9c2d51f0ab74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Seeded here as well as in initialize_default_settings() because that function
# only inserts keys that are missing at startup, and a deployment that upgrades
# before it restarts would otherwise have the tables without the settings that
# drive them. Insert-if-absent, so nothing an admin has already chosen is
# touched.
NEW_SETTINGS: tuple[tuple[str, str, str], ...] = (
    (
        "EMAIL_BOT_MODE",
        "off",
        "Inbox bot mode: off, draft, or auto. Any unrecognised value reads as off.",
    ),
    (
        "EMAIL_BOT_AUTO_LANGUAGES",
        "en",
        "Comma-separated languages the bot may send automatically in auto mode.",
    ),
    (
        "EMAIL_BOT_LAST_ERROR",
        "",
        "Set by the inbox bot when a run aborts; shown as a dashboard banner.",
    ),
    (
        "EMAIL_BOT_PER_RUN_CAP",
        "20",
        "Most messages the inbox bot will process in one run, in any mode.",
    ),
    (
        "CRON_POLL_INBOX",
        "0 8,18 * * *",
        "Cron schedule for polling the volunteer inbox (Vietnam time).",
    ),
    (
        "ESCALATION_OWNER_EMAIL",
        "",
        "The one address escalated mail is forwarded to.",
    ),
    (
        "KNOWLEDGE_BASE_DOC_ID",
        "",
        "Google Doc id of the curated knowledge base the bot answers from.",
    ),
    (
        "TRIAGE_CLASSIFIER",
        "jev",
        "Which classifier decides the category: jev or litellm.",
    ),
    (
        "TRIAGE_FALLBACK_MODEL",
        "gemini/gemini-3.5-flash-lite",
        "LiteLLM model string for the shadow classifier.",
    ),
    (
        "TRIAGE_CONFIDENCE_THRESHOLD",
        "0.6",
        "Below this classification confidence the mail goes to a person.",
    ),
    (
        "ANSWER_THRESHOLD",
        "0.5",
        "Minimum retrieval similarity before a generated FAQ answer is offered.",
    ),
    (
        "VOLUNTEER_SIGNUP_FORM_LINK",
        "",
        "The volunteer signup Google Form the sign-up reply points to.",
    ),
    ("CLASS_START_TIME", "09:30", "When a class starts, 24-hour HH:MM."),
    ("CLASS_END_TIME", "10:30", "When a class ends, 24-hour HH:MM."),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("thread_key", sa.String(), nullable=False),
        sa.Column("sender_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("pause_reason", sa.String(), nullable=True),
        sa.Column("bot_reply_count", sa.Integer(), nullable=False),
        sa.Column("last_category", sa.String(), nullable=True),
        sa.Column("last_tier", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel", "thread_key", name="uq_conversations_channel_thread"
        ),
    )
    op.create_index(op.f("ix_conversations_id"), "conversations", ["id"], unique=False)
    op.create_index(
        op.f("ix_conversations_channel"), "conversations", ["channel"], unique=False
    )
    op.create_index(
        op.f("ix_conversations_thread_key"),
        "conversations",
        ["thread_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversations_sender_key"),
        "conversations",
        ["sender_key"],
        unique=False,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("provider_message_id", sa.String(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("rfc_message_id", sa.String(), nullable=True),
        sa.Column("gmail_thread_id", sa.String(), nullable=True),
        sa.Column("gmail_message_id_out", sa.String(), nullable=True),
        sa.Column("gmail_draft_id", sa.String(), nullable=True),
        sa.Column("draft_outcome", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("tier", sa.String(), nullable=True),
        sa.Column("triage_confidence", sa.Float(), nullable=True),
        sa.Column("classifier", sa.String(), nullable=True),
        sa.Column("triage_shadow", sa.JSON(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_id"), "messages", ["id"], unique=False)
    op.create_index(
        op.f("ix_messages_conversation_id"),
        "messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_messages_created_at"), "messages", ["created_at"], unique=False
    )
    op.create_index(
        "uq_messages_provider_message_id",
        "messages",
        ["provider_message_id"],
        unique=True,
    )

    op.create_table(
        "email_bot_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("listed", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("drafted", sa.Integer(), nullable=False),
        sa.Column("sent", sa.Integer(), nullable=False),
        sa.Column("forwarded", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("aborted_reason", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_email_bot_runs_id"), "email_bot_runs", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_email_bot_runs_started_at"),
        "email_bot_runs",
        ["started_at"],
        unique=False,
    )

    _seed_settings()


def _seed_settings() -> None:
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

    The settings rows are removed as well as the tables. They only exist to
    configure a feature whose tables are going away, and leaving them behind
    would show an operator a dashboard full of knobs for a bot that cannot run.
    """
    bind = op.get_bind()
    for key, _value, _description in NEW_SETTINGS:
        bind.execute(sa.text("DELETE FROM settings WHERE key = :key"), {"key": key})

    op.drop_index(op.f("ix_email_bot_runs_started_at"), table_name="email_bot_runs")
    op.drop_index(op.f("ix_email_bot_runs_id"), table_name="email_bot_runs")
    op.drop_table("email_bot_runs")

    op.drop_index("uq_messages_provider_message_id", table_name="messages")
    op.drop_index(op.f("ix_messages_created_at"), table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_id"), table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_conversations_sender_key"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_thread_key"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_channel"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_id"), table_name="conversations")
    op.drop_table("conversations")
