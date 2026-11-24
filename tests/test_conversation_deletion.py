"""Deleting a stored conversation on request.

"Delete" removes the text and the address and keeps the skeleton: the ids
idempotence needs, the timestamps, category, tier, language and action that the
metrics and the escalations view read. The text is overwritten with NULL, not
flagged. A request can name one conversation, or a person's email address,
which is found through ``sender_key`` - the SHA-256 already on every row - so
it works without decrypting anything. Running it twice changes nothing, every
request leaves a deletion record with no content in it, and the address typed
in is never logged.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Conversation, ConversationDeletion, Message
from app.services.auth_service import AuthService
from app.services.channels.mail_guards import sender_key
from app.services.conversation_store import SqlConversationStore, Viewer
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.conversations import fixture_phrases, run_corpus
from tests.fixtures.logs import EMAIL_BOT_LOGGERS, attached_caplog

ADMIN = {"id": "admin-1", "email": "lan@vietnamhearts.org"}
REQUESTER = Viewer(id=ADMIN["id"], email=ADMIN["email"])
SECRET = "sb_secret_" + "t" * 32
BY_SENDER = "/admin/email-bot/conversations/erase-by-sender"

SKELETON = (
    "id",
    "conversation_id",
    "direction",
    "provider_message_id",
    "language",
    "action",
    "rfc_message_id",
    "gmail_thread_id",
    "gmail_message_id_out",
    "gmail_draft_id",
    "draft_outcome",
    "handled_at",
    "category",
    "tier",
    "triage_confidence",
    "classifier",
    "sources",
    "created_at",
    "sender_key",
)
CONTENT = ("subject_encrypted", "body_encrypted", "sender_address_encrypted")


def erase_one(conversation_id: int) -> str:
    return f"/admin/email-bot/conversations/{conversation_id}/erase"


@pytest.fixture
def corpus(test_db):
    return run_corpus(test_db)


@pytest.fixture
def store(test_db):
    return SqlConversationStore(test_db)


def skeleton(db) -> dict[int, tuple]:
    return {
        row.id: tuple(getattr(row, column) for column in SKELETON)
        for row in db.query(Message).order_by(Message.id)
    }


def with_content(db) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.body_encrypted.isnot(None))
        .order_by(Message.id)
        .all()
    )


def a_conversation_with_content(db) -> Conversation:
    return db.get(Conversation, with_content(db)[0].conversation_id)


class TestErasingOneConversation:
    def test_text_and_address_are_overwritten_with_null(self, test_db, corpus, store):
        conversation = a_conversation_with_content(test_db)

        result = store.erase_conversation(conversation.id, REQUESTER)

        rows = test_db.query(Message).filter(Message.conversation_id == conversation.id)
        assert result.messages_erased == rows.count()
        for row in rows:
            for column in CONTENT:
                assert getattr(row, column) is None
            assert row.content_erased_at is not None
            assert row.content_erased_at.tzinfo is None  # naive UTC

    def test_the_value_is_gone_from_the_database_not_just_hidden(
        self, test_db, corpus, store
    ):
        from sqlalchemy import text

        conversation = a_conversation_with_content(test_db)
        store.erase_conversation(conversation.id, REQUESTER)

        raw = test_db.execute(
            text(
                "SELECT subject_encrypted, body_encrypted, sender_address_encrypted "
                "FROM messages WHERE conversation_id = :id"
            ),
            {"id": conversation.id},
        ).all()
        assert raw and all(value is None for row in raw for value in row)

    def test_the_skeleton_stays(self, test_db, corpus, store):
        before = skeleton(test_db)
        store.erase_conversation(a_conversation_with_content(test_db).id, REQUESTER)
        assert skeleton(test_db) == before

    def test_other_conversations_are_untouched(self, test_db, corpus, store):
        target = a_conversation_with_content(test_db)
        others = [
            row.id for row in with_content(test_db) if row.conversation_id != target.id
        ]
        assert others

        store.erase_conversation(target.id, REQUESTER)

        still = {row.id for row in with_content(test_db)}
        assert set(others) <= still

    def test_a_second_run_changes_nothing(self, test_db, corpus, store):
        conversation = a_conversation_with_content(test_db)
        store.erase_conversation(conversation.id, REQUESTER)
        after_first = {
            row.id: (row.content_erased_at, *(getattr(row, c) for c in CONTENT))
            for row in test_db.query(Message)
        }

        second = store.erase_conversation(conversation.id, REQUESTER)

        assert second.messages_erased == 0
        assert {
            row.id: (row.content_erased_at, *(getattr(row, c) for c in CONTENT))
            for row in test_db.query(Message)
        } == after_first

    def test_a_deletion_record_is_written_without_content(self, test_db, corpus, store):
        conversation = a_conversation_with_content(test_db)
        result = store.erase_conversation(conversation.id, REQUESTER)

        record = test_db.get(ConversationDeletion, result.deletion_id)
        assert record.scope == "conversation"
        assert record.conversation_ids == [conversation.id]
        assert record.messages_erased == result.messages_erased
        assert record.requested_by_id == ADMIN["id"]
        assert record.requested_by_email == ADMIN["email"]
        assert record.sender_key is None
        assert record.created_at is not None

    def test_an_unknown_conversation_is_none_and_records_nothing(
        self, test_db, corpus, store
    ):
        assert store.erase_conversation(99_999, REQUESTER) is None
        assert test_db.query(ConversationDeletion).count() == 0


class TestErasingByAddress:
    def test_every_conversation_from_that_address_is_erased(
        self, test_db, corpus, store
    ):
        key = sender_key(payloads.TEST_SENDER)
        theirs = {
            row.id for row in test_db.query(Conversation).filter_by(sender_key=key)
        }
        assert theirs

        result = store.erase_sender(payloads.TEST_SENDER, REQUESTER)

        assert set(result.conversation_ids) == theirs
        for row in test_db.query(Message).filter(Message.conversation_id.in_(theirs)):
            for column in CONTENT:
                assert getattr(row, column) is None

    def test_the_address_is_matched_however_it_is_typed(self, test_db, corpus, store):
        typed = f"  Some Name <{payloads.TEST_SENDER.upper()}>  "
        result = store.erase_sender(typed, REQUESTER)
        assert result.messages_erased > 0

    def test_their_mail_in_someone_elses_thread_is_erased_too(
        self, test_db, corpus, store
    ):
        from app.utils.field_crypto import cipher_from_env

        other = Conversation(
            channel="email",
            thread_key="someone-elses-thread",
            sender_key="b" * 64,
            status="bot",
            bot_reply_count=0,
        )
        test_db.add(other)
        test_db.commit()
        cipher = cipher_from_env()
        starter = Message(
            conversation_id=other.id,
            direction="inbound",
            provider_message_id="starter",
            action="labelled",
            sender_key="b" * 64,
            body_encrypted=cipher.encrypt("the starter", "messages.body_encrypted"),
        )
        joined = Message(
            conversation_id=other.id,
            direction="inbound",
            provider_message_id="joined",
            action="labelled",
            sender_key=sender_key(payloads.TEST_SENDER),
            body_encrypted=cipher.encrypt("the requester", "messages.body_encrypted"),
        )
        test_db.add_all([starter, joined])
        test_db.commit()

        result = store.erase_sender(payloads.TEST_SENDER, REQUESTER)

        test_db.refresh(starter)
        test_db.refresh(joined)
        assert joined.body_encrypted is None
        assert starter.body_encrypted is not None  # not theirs to delete
        assert other.id in result.conversation_ids

    def test_works_without_the_key(self, test_db, corpus, monkeypatch):
        # Nothing is decrypted: deletion goes through the hash.
        from app.utils.field_crypto import KEYS_SETTING

        monkeypatch.delenv(KEYS_SETTING)
        result = SqlConversationStore(test_db).erase_sender(
            payloads.TEST_SENDER, REQUESTER
        )
        assert result.messages_erased > 0

    def test_the_deletion_record_holds_the_hash_never_the_address(
        self, test_db, corpus, store
    ):
        result = store.erase_sender(payloads.TEST_SENDER, REQUESTER)
        record = test_db.get(ConversationDeletion, result.deletion_id)

        assert record.scope == "sender"
        assert record.sender_key == sender_key(payloads.TEST_SENDER)
        stored = " ".join(
            str(getattr(record, column.name))
            for column in ConversationDeletion.__table__.columns
        )
        assert payloads.TEST_SENDER not in stored
        assert "sender@" not in stored

    def test_a_second_run_changes_nothing(self, test_db, corpus, store):
        store.erase_sender(payloads.TEST_SENDER, REQUESTER)
        before = skeleton(test_db)
        second = store.erase_sender(payloads.TEST_SENDER, REQUESTER)
        assert second.messages_erased == 0
        assert skeleton(test_db) == before

    def test_an_address_nobody_wrote_from_erases_nothing_but_is_recorded(
        self, test_db, corpus, store
    ):
        before = len(with_content(test_db))
        result = store.erase_sender("nobody@example.org", REQUESTER)
        assert result.messages_erased == 0
        assert result.conversation_ids == []
        assert len(with_content(test_db)) == before
        assert test_db.get(ConversationDeletion, result.deletion_id) is not None

    @pytest.mark.parametrize("typed", ["", "   ", "not an address"])
    def test_something_that_is_not_an_address_is_refused(self, test_db, store, typed):
        with pytest.raises(ValueError):
            store.erase_sender(typed, REQUESTER)
        assert test_db.query(ConversationDeletion).count() == 0


class TestEverythingElseStillWorksOnErasedRows:
    def test_metrics_and_escalations(self, test_db, corpus, store, client, admin):
        before_metrics = client.get("/admin/email-bot/metrics").json()
        before_escalations = client.get("/admin/email-bot/escalations").json()

        store.erase_sender(payloads.TEST_SENDER, REQUESTER)
        for conversation in test_db.query(Conversation):
            store.erase_conversation(conversation.id, REQUESTER)

        metrics = client.get("/admin/email-bot/metrics")
        escalations = client.get("/admin/email-bot/escalations")
        assert metrics.status_code == 200
        assert escalations.status_code == 200
        assert metrics.json()["draft_acceptance"] == before_metrics["draft_acceptance"]
        assert metrics.json()["caps"] == before_metrics["caps"]
        assert escalations.json() == before_escalations

    def test_the_list_and_the_viewer_show_the_conversation_as_deleted(
        self, test_db, corpus, store, client, admin
    ):
        conversation = a_conversation_with_content(test_db)
        store.erase_conversation(conversation.id, REQUESTER)

        listed = client.get(
            "/admin/email-bot/conversations", params={"limit": 100}
        ).json()["conversations"]
        row = next(item for item in listed if item["id"] == conversation.id)
        assert row["erased"] is True
        assert row["has_content"] is False

        opened = client.get(f"/admin/email-bot/conversations/{conversation.id}").json()
        assert opened["messages"]
        for message in opened["messages"]:
            assert message["erased"] is True
            assert message["subject"] is None
            assert message["body"] is None
            assert message["sender_address"] is None

    def test_a_retry_of_an_unhandled_mail_does_not_restore_it(
        self, test_db, corpus, store
    ):
        # A deletion can land between a mail being recorded and finished with.
        from app.services.conversation_service import ConversationService

        row = with_content(test_db)[0]
        row.handled_at = None
        test_db.commit()
        store.erase_conversation(row.conversation_id, REQUESTER)

        message = corpus["parsed"][row.provider_message_id]
        service = ConversationService(test_db, store=store)
        conversation = test_db.get(Conversation, row.conversation_id)
        from inbox_core import TriageDecision

        service.record_inbound(
            conversation,
            message,
            TriageDecision(
                category=row.category or "faq",
                language=row.language or "en",
                confidence=0.9,
                asks_for_human=False,
                mentions_money_or_commitment=False,
                classifier="fake",
                tier=row.tier or "auto_answer",
            ),
        )
        test_db.refresh(row)
        assert row.body_encrypted is None

    def test_a_pending_draft_whose_text_was_deleted_is_left_out_of_the_rate(
        self, test_db, corpus, store
    ):
        # Without its text the bot cannot tell "sent unchanged" from "edited",
        # so a guess either way would bend the acceptance gate.
        import anyio

        from app.services.channels.gmail import GmailAdapter
        from app.services.conversation_service import (
            DRAFT_PENDING,
            DRAFT_UNMEASURED,
            ConversationService,
        )
        from app.services.email_bot.pipeline import EmailBotPipeline
        from app.services.email_bot.settings import EmailBotSettings
        from tests.fixtures.email_bot import (
            FakeClassifier,
            FakeTransport,
            RecordingNotifier,
        )

        draft = (
            test_db.query(Message)
            .filter(Message.draft_outcome == DRAFT_PENDING)
            .first()
        )
        store.erase_conversation(draft.conversation_id, REQUESTER)

        # The draft is gone from Gmail (the fake has none of the corpus drafts).
        transport = FakeTransport()
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(),
            notifier=RecordingNotifier(),
            settings=EmailBotSettings(
                mode="draft", escalation_owner_email="owner@example.com"
            ),
        )
        anyio.run(lambda: anyio.to_thread.run_sync(pipeline.run))

        test_db.refresh(draft)
        assert draft.draft_outcome == DRAFT_UNMEASURED
        acceptance = ConversationService(test_db).draft_acceptance()
        kind = draft.category or "unknown"
        assert acceptance[kind].get(DRAFT_UNMEASURED) == 1

        from app.routers.admin.email_bot import _acceptance_rate

        assert _acceptance_rate({DRAFT_UNMEASURED: 3}) is None


@pytest.fixture
def admin(client):
    from app.dependencies.auth import get_current_admin_user
    from app.main import app

    app.dependency_overrides[get_current_admin_user] = lambda: dict(ADMIN)
    yield
    app.dependency_overrides.pop(get_current_admin_user, None)


@pytest.fixture
def auth(monkeypatch):
    service = AuthService.__new__(AuthService)
    service.supabase = MagicMock()
    service.admin_supabase = None
    service._admin_cache = {}
    service._cache_ttl = 300
    service._last_cache_cleanup = time.time()
    service._get_user_from_token = AsyncMock(return_value=dict(ADMIN))
    service._check_admin_db = AsyncMock(return_value=True)
    monkeypatch.setattr("app.dependencies.auth.auth_service", service)
    monkeypatch.setattr("app.services.auth_service.SUPABASE_SECRET_KEY", SECRET)
    return service


BEARER = {"Authorization": "Bearer admin-token"}


class TestTheEndpoints:
    def test_erase_one(self, test_db, corpus, client, auth):
        conversation = a_conversation_with_content(test_db)
        response = client.post(erase_one(conversation.id), headers=BEARER)

        assert response.status_code == 200
        body = response.json()
        assert body["conversation_ids"] == [conversation.id]
        assert body["messages_erased"] > 0
        assert test_db.get(ConversationDeletion, body["deletion_id"]) is not None

    def test_erase_one_unknown_is_404(self, test_db, corpus, client, auth):
        assert client.post(erase_one(99_999), headers=BEARER).status_code == 404

    def test_erase_by_sender_takes_the_address_in_the_body(
        self, test_db, corpus, client, auth
    ):
        response = client.post(
            BY_SENDER, json={"email": payloads.TEST_SENDER}, headers=BEARER
        )
        assert response.status_code == 200
        assert response.json()["messages_erased"] > 0
        # Not echoed back.
        assert payloads.TEST_SENDER not in response.text

    @pytest.mark.parametrize(
        "payload", [{}, {"email": ""}, {"email": "nope"}, {"email": 3}]
    )
    def test_erase_by_sender_rejects_a_non_address_without_echoing_it(
        self, test_db, corpus, client, auth, payload
    ):
        response = client.post(BY_SENDER, json=payload, headers=BEARER)
        assert response.status_code == 422
        assert "nope" not in response.text
        assert test_db.query(ConversationDeletion).count() == 0

    @pytest.mark.parametrize("which", ["one", "sender"])
    def test_unauthenticated_and_cron_and_non_admin_are_refused(
        self, test_db, corpus, client, auth, which
    ):
        conversation = a_conversation_with_content(test_db)

        def call(headers):
            if which == "one":
                return client.post(erase_one(conversation.id), headers=headers)
            return client.post(
                BY_SENDER, json={"email": payloads.TEST_SENDER}, headers=headers
            )

        assert call({}).status_code == 401
        assert call({"apikey": SECRET}).status_code == 403
        auth._check_admin_db = AsyncMock(return_value=False)
        auth._admin_cache.clear()
        assert call(BEARER).status_code == 403

        assert test_db.query(ConversationDeletion).count() == 0
        assert len(with_content(test_db)) > 0

    def test_the_typed_address_is_never_logged(
        self, test_db, corpus, client, auth, caplog
    ):
        phrases = fixture_phrases(corpus["parsed"])
        typed = "Requester Person <Sender@Example.com>"
        with attached_caplog(
            caplog, *EMAIL_BOT_LOGGERS, "logging_middleware", "auth_service"
        ):
            client.post(BY_SENDER, json={"email": typed}, headers=BEARER)
            client.post(
                erase_one(a_conversation_with_content(test_db).id), headers=BEARER
            )

        assert caplog.records, "nothing was captured; the assertion would be void"
        assert "sender@example.com" not in caplog.text.lower()
        assert "Requester Person" not in caplog.text
        for phrase in phrases:
            assert phrase not in caplog.text
