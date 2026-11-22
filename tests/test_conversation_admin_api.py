"""The admin endpoints that read stored inbox conversations.

Two of the four safeguards are enforced here: only admins read conversations,
and every time one is opened that is recorded. The list endpoint is the one
the dashboard polls, so it must never carry a subject, a body or an address;
content is returned only by the single-conversation endpoint, which writes the
view record before it returns anything.

The cron identity (the ``apikey`` service-role key Cloud Scheduler uses) is an
admin for every scheduled job, but no job reads conversations, so it is refused
here: a leaked scheduler key must not be a way to read the inbox.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Conversation, ConversationView, Message
from app.services.auth_service import AuthService
from app.utils.field_crypto import KEYS_SETTING
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.conversations import fixture_phrases, run_corpus
from tests.fixtures.logs import EMAIL_BOT_LOGGERS, attached_caplog

LIST = "/admin/email-bot/conversations"
ADMIN = {"id": "admin-1", "email": "lan@vietnamhearts.org"}
SECRET = "sb_secret_" + "t" * 32


def one(conversation_id: int) -> str:
    return f"{LIST}/{conversation_id}"


@pytest.fixture
def auth(monkeypatch):
    """The real admin gate, with only the Supabase calls behind it faked.

    ``get_current_admin_user`` runs as production runs it, so the 401, the 403
    and the service-account short-circuit are the real ones.
    """
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


@pytest.fixture
def corpus(test_db):
    return run_corpus(test_db)


@pytest.fixture
def phrases(corpus):
    found = fixture_phrases(corpus["parsed"])
    assert found
    return found


def as_admin(client, method, url, **kwargs):
    headers = {"Authorization": "Bearer admin-token", **kwargs.pop("headers", {})}
    return client.request(method, url, headers=headers, **kwargs)


def a_conversation_with_content(db) -> Conversation:
    return (
        db.query(Conversation)
        .join(Message)
        .filter(Message.direction == "inbound", Message.body_encrypted.isnot(None))
        .first()
    )


class TestWhoMayRead:
    @pytest.mark.parametrize("path", ["list", "one"])
    def test_unauthenticated_gets_401_and_no_data(
        self, client, auth, corpus, test_db, phrases, path
    ):
        url = LIST if path == "list" else one(a_conversation_with_content(test_db).id)
        response = client.get(url)

        assert response.status_code == 401
        for phrase in phrases:
            assert phrase not in response.text
        assert test_db.query(ConversationView).count() == 0

    @pytest.mark.parametrize("path", ["list", "one"])
    def test_a_signed_in_non_admin_gets_403_and_no_data(
        self, client, auth, corpus, test_db, phrases, path
    ):
        auth._check_admin_db = AsyncMock(return_value=False)
        url = LIST if path == "list" else one(a_conversation_with_content(test_db).id)
        response = as_admin(client, "GET", url)

        assert response.status_code == 403
        for phrase in phrases:
            assert phrase not in response.text
        assert test_db.query(ConversationView).count() == 0

    @pytest.mark.parametrize("path", ["list", "one"])
    def test_the_cron_service_account_is_refused(
        self, client, auth, corpus, test_db, phrases, path
    ):
        # The same key every scheduled job uses, accepted as admin for those.
        assert client.get(
            "/admin/email-bot/runs", headers={"apikey": SECRET}
        ).is_success

        url = LIST if path == "list" else one(a_conversation_with_content(test_db).id)
        response = client.get(url, headers={"apikey": SECRET})

        assert response.status_code == 403
        for phrase in phrases:
            assert phrase not in response.text
        assert test_db.query(ConversationView).count() == 0

    def test_a_signed_in_admin_may_read(self, client, auth, corpus, test_db):
        assert as_admin(client, "GET", LIST).status_code == 200


class TestTheList:
    def test_lists_every_conversation_newest_first(self, client, auth, corpus, test_db):
        body = as_admin(client, "GET", LIST, params={"limit": 50}).json()

        assert body["total"] == test_db.query(Conversation).count()
        rows = body["conversations"]
        assert len(rows) == body["total"]
        stamps = [row["last_activity_at"] for row in rows]
        assert stamps == sorted(stamps, reverse=True)

    def test_carries_no_message_text_subject_or_address(
        self, client, auth, corpus, phrases
    ):
        response = as_admin(client, "GET", LIST, params={"limit": 50})

        assert response.status_code == 200
        for phrase in phrases:
            assert phrase not in response.text, f"the list leaked {phrase!r}"
        assert "@" not in response.text
        assert "vhenc:" not in response.text

    def test_each_row_says_what_happened(self, client, auth, corpus):
        rows = as_admin(client, "GET", LIST, params={"limit": 50}).json()[
            "conversations"
        ]
        outcomes = {row["outcome"] for row in rows}
        assert {"draft_pending", "escalated", "skipped"} <= outcomes
        for row in rows:
            assert set(row) >= {
                "id",
                "status",
                "category",
                "tier",
                "received_at",
                "last_activity_at",
                "inbound_count",
                "outbound_count",
                "outcome",
                "has_content",
                "erased",
            }
            assert row["received_at"].endswith("+00:00")

    def test_is_paginated(self, client, auth, corpus, test_db):
        total = test_db.query(Conversation).count()
        first = as_admin(client, "GET", LIST, params={"limit": 3}).json()
        second = as_admin(client, "GET", LIST, params={"limit": 3, "offset": 3}).json()

        assert first["total"] == second["total"] == total
        assert len(first["conversations"]) == 3
        assert not {row["id"] for row in first["conversations"]} & {
            row["id"] for row in second["conversations"]
        }

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1000}, {"offset": -1}])
    def test_bounds_are_enforced(self, client, auth, corpus, params):
        assert as_admin(client, "GET", LIST, params=params).status_code == 422

    def test_listing_records_no_view(self, client, auth, corpus, test_db):
        as_admin(client, "GET", LIST)
        assert test_db.query(ConversationView).count() == 0

    def test_listing_works_without_the_key(self, client, auth, corpus, monkeypatch):
        # Nothing in the list is decrypted, so a missing key must not take
        # the dashboard card down with it.
        monkeypatch.delenv(KEYS_SETTING)
        assert as_admin(client, "GET", LIST).status_code == 200


class TestOpeningOne:
    def test_returns_the_decrypted_conversation(self, client, auth, corpus, test_db):
        conversation = a_conversation_with_content(test_db)
        body = as_admin(client, "GET", one(conversation.id)).json()

        assert body["conversation"]["id"] == conversation.id
        inbound = [m for m in body["messages"] if m["direction"] == "inbound"]
        outbound = [m for m in body["messages"] if m["direction"] == "outbound"]
        assert inbound and outbound

        row = (
            test_db.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.direction == "inbound",
            )
            .first()
        )
        original = corpus["parsed"][row.provider_message_id]
        assert inbound[0]["subject"] == original.subject
        assert inbound[0]["body"] == original.text
        assert inbound[0]["sender_address"] == payloads.TEST_SENDER
        assert outbound[0]["body"]
        assert outbound[0]["sender_address"] is None

    def test_writes_a_view_record_with_who_and_when_and_nothing_else(
        self, client, auth, corpus, test_db
    ):
        conversation = a_conversation_with_content(test_db)
        as_admin(client, "GET", one(conversation.id))

        view = test_db.query(ConversationView).one()
        assert view.conversation_id == conversation.id
        assert view.viewer_id == ADMIN["id"]
        assert view.viewer_email == ADMIN["email"]
        assert view.viewed_at is not None
        assert view.viewed_at.tzinfo is None  # naive UTC, like every column

    def test_every_open_is_recorded_and_reported(self, client, auth, corpus, test_db):
        conversation = a_conversation_with_content(test_db)
        as_admin(client, "GET", one(conversation.id))
        as_admin(client, "GET", one(conversation.id))
        body = as_admin(client, "GET", one(conversation.id)).json()

        assert test_db.query(ConversationView).count() == 3
        assert body["views"]["count"] == 3
        assert body["views"]["recent"][0]["viewer_email"] == ADMIN["email"]
        assert body["viewer"]["id"] == ADMIN["id"]

    def test_the_response_is_not_cached(self, client, auth, corpus, test_db):
        response = as_admin(client, "GET", one(a_conversation_with_content(test_db).id))
        assert response.headers["cache-control"] == "no-store"

    def test_an_unknown_conversation_is_404_and_records_nothing(
        self, client, auth, corpus, test_db
    ):
        response = as_admin(client, "GET", one(99_999))
        assert response.status_code == 404
        assert test_db.query(ConversationView).count() == 0

    def test_without_the_key_it_is_503_naming_the_setting(
        self, client, auth, corpus, test_db, monkeypatch, phrases
    ):
        monkeypatch.delenv(KEYS_SETTING)
        response = as_admin(client, "GET", one(a_conversation_with_content(test_db).id))

        assert response.status_code == 503
        assert KEYS_SETTING in response.json()["detail"]
        for phrase in phrases:
            assert phrase not in response.text

    def test_a_value_that_will_not_decrypt_is_500_naming_ids_only(
        self, client, auth, corpus, test_db, phrases
    ):
        conversation = a_conversation_with_content(test_db)
        row = (
            test_db.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.body_encrypted.isnot(None),
            )
            .first()
        )
        # A token from the subject column, moved into the body column.
        row.body_encrypted = row.subject_encrypted
        test_db.commit()

        response = as_admin(client, "GET", one(conversation.id))

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert f"message id={row.id}" in detail
        for phrase in phrases:
            assert phrase not in response.text
        # The attempt is still on the record.
        assert test_db.query(ConversationView).count() == 1


class TestReadingLeavesNoTrace:
    def test_no_log_record_carries_content_when_reading(
        self, client, auth, corpus, test_db, phrases, caplog
    ):
        with attached_caplog(
            caplog, *EMAIL_BOT_LOGGERS, "logging_middleware", "auth_service"
        ):
            as_admin(client, "GET", LIST)
            for conversation in test_db.query(Conversation):
                as_admin(client, "GET", one(conversation.id))

        assert caplog.records, "nothing was captured; the assertion would be void"
        for phrase in phrases:
            assert phrase not in caplog.text
        assert payloads.TEST_SENDER not in caplog.text

    def test_a_decrypt_failure_logs_ids_only(
        self, client, auth, corpus, test_db, phrases, caplog
    ):
        conversation = a_conversation_with_content(test_db)
        row = (
            test_db.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.body_encrypted.isnot(None),
            )
            .first()
        )
        row.body_encrypted = row.subject_encrypted
        test_db.commit()

        with attached_caplog(caplog, *EMAIL_BOT_LOGGERS, "logging_middleware"):
            as_admin(client, "GET", one(conversation.id))

        assert f"message id={row.id}" in caplog.text
        for phrase in phrases:
            assert phrase not in caplog.text
        assert "vhenc:" not in caplog.text
