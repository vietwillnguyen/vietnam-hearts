"""The migration that gives the inbox bot somewhere encrypted to keep conversations.

Run through real ``alembic upgrade`` and ``downgrade`` against a fresh SQLite
file, rather than by calling ``upgrade()`` on a session, because the parts that
can go wrong are the ones only a real run exercises: the batch rebuild that
drops ``messages.text``, the revision chain, and the data step that moves any
existing bot text into the encrypted column.

CI separately runs ``alembic upgrade head`` then ``alembic check`` on an empty
database, which proves the models and the migrations agree.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command
from app.config import PROJECT_ROOT
from app.utils.field_crypto import KEYS_SETTING, EncryptionKeyError, cipher_from_env

REVISION = "5f3c9a1d7e20"
PREVIOUS = "09213ade570c"

NEW_MESSAGE_COLUMNS = {
    "subject_encrypted",
    "body_encrypted",
    "sender_address_encrypted",
    "sender_key",
    "content_erased_at",
}
NEW_TABLES = {"conversation_views", "conversation_deletions"}

BOT_TEXT = "Thank you for writing. You do not need a certificate to start."


def alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A fresh SQLite file that ``alembic`` itself is pointed at."""
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    # alembic/env.py imports DATABASE_URL from app.config each time it runs.
    monkeypatch.setattr("app.config.DATABASE_URL", url)
    engine = sa.create_engine(url)
    yield engine
    engine.dispose()


def columns(engine, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(table)}


def seed_previous_schema_rows(engine) -> None:
    """One conversation with an inbound row and an outbound row, as the old code wrote them."""
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO conversations (id, channel, thread_key, sender_key, "
                "status, bot_reply_count) VALUES (1, 'email', 'thread-1', :key, "
                "'bot', 1)"
            ),
            {"key": "a" * 64},
        )
        connection.execute(
            sa.text(
                "INSERT INTO messages (id, conversation_id, direction, "
                "provider_message_id, text, action) VALUES "
                "(1, 1, 'inbound', 'gmail-1', NULL, 'labelled'), "
                "(2, 1, 'outbound', NULL, :text, 'drafted')"
            ),
            {"text": BOT_TEXT},
        )


class TestTheRevisionChain:
    def test_there_is_exactly_one_head_and_it_is_this_revision(self):
        heads = ScriptDirectory.from_config(alembic_config()).get_heads()
        assert heads == [REVISION]

    def test_it_sits_on_the_previous_head(self):
        script = ScriptDirectory.from_config(alembic_config()).get_revision(REVISION)
        assert script.down_revision == PREVIOUS


class TestUpgradeOnAFreshDatabase:
    def test_adds_the_encrypted_columns_and_drops_the_plaintext_one(self, database):
        command.upgrade(alembic_config(), "head")

        message_columns = columns(database, "messages")
        assert NEW_MESSAGE_COLUMNS <= message_columns
        # Not left alongside the encrypted column: one copy of the text, and
        # that copy encrypted.
        assert "text" not in message_columns

    def test_creates_the_view_and_deletion_tables(self, database):
        command.upgrade(alembic_config(), "head")

        tables = set(sa.inspect(database).get_table_names())
        assert NEW_TABLES <= tables
        assert {"conversation_id", "viewer_id", "viewer_email", "viewed_at"} <= (
            columns(database, "conversation_views")
        )
        deletion_columns = columns(database, "conversation_deletions")
        assert {
            "scope",
            "conversation_ids",
            "messages_erased",
            "requested_by_id",
            "created_at",
        } <= deletion_columns
        # A deletion record says what was removed, never what it said.
        assert not {"text", "subject", "body", "email", "address"} & deletion_columns

    def test_downgrade_and_upgrade_again_round_trip(self, database):
        command.upgrade(alembic_config(), "head")
        command.downgrade(alembic_config(), PREVIOUS)

        assert "text" in columns(database, "messages")
        assert not NEW_MESSAGE_COLUMNS & columns(database, "messages")
        assert not NEW_TABLES & set(sa.inspect(database).get_table_names())

        command.upgrade(alembic_config(), "head")
        assert NEW_MESSAGE_COLUMNS <= columns(database, "messages")


class TestExistingBotText:
    def test_is_moved_into_the_encrypted_column(self, database):
        command.upgrade(alembic_config(), PREVIOUS)
        seed_previous_schema_rows(database)

        command.upgrade(alembic_config(), "head")

        with database.connect() as connection:
            rows = dict(
                connection.execute(
                    sa.text("SELECT id, body_encrypted FROM messages ORDER BY id")
                ).all()
            )
        assert rows[1] is None
        assert BOT_TEXT not in rows[2]
        assert cipher_from_env().decrypt(rows[2], "messages.body_encrypted") == BOT_TEXT

    def test_without_a_key_the_upgrade_refuses_rather_than_dropping_it(
        self, database, monkeypatch
    ):
        command.upgrade(alembic_config(), PREVIOUS)
        seed_previous_schema_rows(database)
        monkeypatch.delenv(KEYS_SETTING)

        with pytest.raises(EncryptionKeyError) as raised:
            command.upgrade(alembic_config(), "head")
        assert KEYS_SETTING in str(raised.value)

        # Nothing half-done: the plaintext column and its row are still there.
        assert "text" in columns(database, "messages")
        with database.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT text FROM messages WHERE id = 2")
                ).scalar()
                == BOT_TEXT
            )

    def test_without_any_text_no_key_is_needed(self, database, monkeypatch):
        # The production case on the deploy that ships this: the bot has never
        # stored anything, so the migration must not depend on the key.
        monkeypatch.delenv(KEYS_SETTING)
        command.upgrade(alembic_config(), "head")
        assert NEW_MESSAGE_COLUMNS <= columns(database, "messages")


class TestDowngrade:
    def test_restores_outbound_text_and_never_writes_inbound_text_back(self, database):
        command.upgrade(alembic_config(), PREVIOUS)
        seed_previous_schema_rows(database)
        command.upgrade(alembic_config(), "head")

        cipher = cipher_from_env()
        with database.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE messages SET body_encrypted = :body, "
                    "subject_encrypted = :subject WHERE id = 1"
                ),
                {
                    "body": cipher.encrypt(
                        "an inbound body", "messages.body_encrypted"
                    ),
                    "subject": cipher.encrypt(
                        "an inbound subject", "messages.subject_encrypted"
                    ),
                },
            )

        command.downgrade(alembic_config(), PREVIOUS)

        with database.connect() as connection:
            rows = dict(
                connection.execute(
                    sa.text("SELECT id, text FROM messages ORDER BY id")
                ).all()
            )
        # The old schema promised inbound text was never stored; a downgrade
        # must not break that promise by decrypting it into plaintext.
        assert rows[1] is None
        assert rows[2] == BOT_TEXT
