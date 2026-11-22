"""What the inbox bot keeps of a conversation, and that it keeps it encrypted.

Decided 2026-11-22: the bot keeps each conversation's subject, plain-text body
and sender address, and its own replies, encrypted, until someone asks for
them to be deleted. Never HTML, headers or attachments, and never past a fixed
length. These tests pin that through ``ConversationStore``, the one interface
everything else reads and writes conversation content through, and then run
the whole fixture corpus through the real pipeline and decrypt every stored
value back to what the mail actually said.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import base64
from datetime import UTC, datetime

import anyio
import pytest

from app.models import Message
from app.services.channels.base import EMAIL_CHANNEL, IncomingMessage
from app.services.channels.gmail import GmailAdapter
from app.services.channels.gmail_transport import RawMail
from app.services.channels.mail_guards import sender_key
from app.services.conversation_service import ConversationService
from app.services.conversation_store import (
    BODY_MAX_CHARS,
    SUBJECT_MAX_CHARS,
    TRUNCATION_MARKER,
    ConversationStore,
    SqlConversationStore,
)
from app.services.email_bot.pipeline import EmailBotPipeline
from app.services.email_bot.settings import EmailBotSettings
from app.services.triage.protocol import TriageDecision
from app.utils.field_crypto import KEYS_SETTING, EncryptionKeyError, cipher_from_env
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.conversations import stored
from tests.fixtures.email_bot import (
    FakeBotService,
    FakeClassifier,
    FakeTransport,
    RecordingNotifier,
    load_gmail,
    signals,
)

OWNER = "owner@example.com"
FORM = "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"

SUBJECT = "Dạy tiếng Anh vào tháng Một"
BODY = "Xin chào, tôi muốn dạy tiếng Anh hai tuần vào tháng Một. Tôi có cần chứng chỉ không?"


def incoming(**overrides) -> IncomingMessage:
    values = {
        "channel": EMAIL_CHANNEL,
        "thread_key": "thread-1",
        "provider_message_id": "msg-1",
        "sender_key": sender_key(payloads.TEST_SENDER),
        "sender_address": payloads.TEST_SENDER,
        "subject": SUBJECT,
        "text": BODY,
        "rfc_message_id": "<abc@x>",
    }
    values.update(overrides)
    return IncomingMessage(**values)


def decision() -> TriageDecision:
    return TriageDecision(
        category="faq",
        language="vi",
        confidence=0.9,
        asks_for_human=False,
        mentions_money_or_commitment=False,
        classifier="fake",
        tier="auto_answer",
    )


@pytest.fixture
def service(test_db):
    return ConversationService(test_db, store=SqlConversationStore(test_db))


def record(service, message=None):
    message = message or incoming()
    conversation = service.get_or_create(
        message.channel, message.thread_key, message.sender_key
    )
    return conversation, service.record_inbound(conversation, message, decision())


class TestTheInterface:
    def test_the_sql_store_satisfies_the_protocol(self, test_db):
        assert isinstance(SqlConversationStore(test_db), ConversationStore)

    def test_the_conversation_service_defaults_to_the_sql_store(self, test_db):
        assert isinstance(ConversationService(test_db).store, SqlConversationStore)


class TestInboundContent:
    def test_subject_body_and_address_are_stored_encrypted(self, service):
        _conversation, row = record(service)

        assert stored(row, "subject_encrypted") == SUBJECT
        assert stored(row, "body_encrypted") == BODY
        assert stored(row, "sender_address_encrypted") == payloads.TEST_SENDER

        raw = " ".join(
            [row.subject_encrypted, row.body_encrypted, row.sender_address_encrypted]
        )
        assert "tiếng Anh" not in raw
        assert "@" not in raw

    def test_the_row_carries_the_senders_hash_for_deletion_by_address(self, service):
        _conversation, row = record(service)
        assert row.sender_key == sender_key(payloads.TEST_SENDER)

    def test_a_long_body_is_cut_at_the_documented_length(self, service):
        long_body = "a" * (BODY_MAX_CHARS + 500)
        _conversation, row = record(service, incoming(text=long_body))

        kept = stored(row, "body_encrypted")
        assert kept.startswith("a" * BODY_MAX_CHARS)
        assert kept.endswith(TRUNCATION_MARKER.format(limit=BODY_MAX_CHARS))
        assert len(kept) == BODY_MAX_CHARS + len(
            TRUNCATION_MARKER.format(limit=BODY_MAX_CHARS)
        )

    def test_a_long_subject_is_cut_too(self, service):
        _conversation, row = record(service, incoming(subject="s" * 2000))
        assert stored(row, "subject_encrypted") == "s" * SUBJECT_MAX_CHARS

    def test_a_body_at_the_limit_is_kept_whole(self, service):
        exact = "b" * BODY_MAX_CHARS
        _conversation, row = record(service, incoming(text=exact))
        assert stored(row, "body_encrypted") == exact

    def test_an_empty_address_is_stored_as_nothing(self, service):
        _conversation, row = record(service, incoming(sender_address=""))
        assert row.sender_address_encrypted is None

    def test_a_retry_re_stores_the_content(self, service):
        conversation, first = record(service)
        again = service.record_inbound(conversation, incoming(), decision())
        assert again.id == first.id
        assert stored(again, "body_encrypted") == BODY

    def test_a_retry_never_brings_back_erased_content(self, service, test_db):
        # Deleted on request while the mail was still unhandled: the retry
        # must not quietly put the text back.
        conversation, row = record(service)
        row.subject_encrypted = row.body_encrypted = None
        row.sender_address_encrypted = None
        row.content_erased_at = datetime.now(UTC)
        test_db.commit()

        again = service.record_inbound(conversation, incoming(), decision())
        assert again.body_encrypted is None
        assert again.subject_encrypted is None
        assert again.sender_address_encrypted is None

    def test_a_guard_skip_keeps_no_content(self, service):
        # Automated mail, our own mail and mail with no usable sender are not a
        # conversation with a person, so nothing of them is kept.
        message = incoming()
        conversation = service.get_or_create(
            message.channel, message.thread_key, message.sender_key
        )
        row = service.record_action(conversation, message, "skipped")
        assert row.body_encrypted is None
        assert row.subject_encrypted is None
        assert row.sender_address_encrypted is None

    def test_a_human_takeover_keeps_the_mail(self, service):
        # The sender wrote to us; that a person answered by hand does not make
        # it less part of the conversation.
        message = incoming()
        conversation = service.get_or_create(
            message.channel, message.thread_key, message.sender_key
        )
        row = service.record_action(conversation, message, "paused", keep_content=True)
        assert stored(row, "body_encrypted") == BODY


class TestOutboundContent:
    def test_the_bots_reply_is_stored_encrypted_and_reads_back(self, service):
        conversation, _row = record(service)
        row = service.record_outbound(
            conversation, text="Cảm ơn bạn đã viết.", action="drafted", language="vi"
        )
        assert "Cảm ơn" not in row.body_encrypted
        assert service.outbound_text(row) == "Cảm ơn bạn đã viết."
        assert row.subject_encrypted is None
        assert row.sender_address_encrypted is None


class TestWhatIsNeverStored:
    def test_html_and_headers_are_not_kept(self, test_db, service):
        html = "<p>Hello <b>there</b>, can I <a href='https://x.example'>help</a>?</p>"
        resource = payloads.message(
            text=None,
            html=html,
            extra_headers={"X-Secret-Header": "header-value-never-kept"},
        )
        message = GmailAdapter(FakeTransport()).parse(RawMail.from_resource(resource))
        _conversation, row = record(service, message)

        kept = stored(row, "body_encrypted")
        assert "<" not in kept and "href" not in kept
        assert "Hello" in kept
        everything = " ".join(
            str(stored(row, column) or "")
            for column in (
                "subject_encrypted",
                "body_encrypted",
                "sender_address_encrypted",
            )
        )
        assert "header-value-never-kept" not in everything

    def test_attachments_are_not_kept(self, service):
        resource = payloads.message(text="Here is my passport scan.", multipart=True)
        resource["payload"]["parts"].append(
            {
                "partId": "2",
                "mimeType": "application/pdf",
                "filename": "passport.pdf",
                "headers": [],
                "body": {
                    "size": 20,
                    "data": base64.urlsafe_b64encode(
                        b"PASSPORT-NUMBER-B1234567"
                    ).decode(),
                },
            }
        )
        message = GmailAdapter(FakeTransport()).parse(RawMail.from_resource(resource))
        _conversation, row = record(service, message)

        assert stored(row, "body_encrypted") == "Here is my passport scan."
        assert "B1234567" not in stored(row, "body_encrypted")


class TestTheInboundRowIsCommittedWithItsContentBeforeAnySideEffect:
    def test_the_content_is_in_the_database_when_the_draft_is_created(self, test_db):
        seen_at_draft_time = []

        class CheckingTransport(FakeTransport):
            def create_draft(self, thread_id, mime):
                # A fresh session sees only what has been committed.
                from sqlalchemy.orm import Session

                with Session(test_db.get_bind()) as other:
                    row = (
                        other.query(Message)
                        .filter(Message.direction == "inbound")
                        .one()
                    )
                    seen_at_draft_time.append(stored(row, "body_encrypted"))
                return super().create_draft(thread_id, mime)

        resource = load_gmail("faq_en.json")
        transport = CheckingTransport(mails=[resource])
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.9)),
            notifier=RecordingNotifier(),
            settings=EmailBotSettings(
                mode="draft", escalation_owner_email=OWNER, signup_form_link=FORM
            ),
            bot_service=FakeBotService(),
        )
        anyio.run(lambda: anyio.to_thread.run_sync(pipeline.run))

        expected = GmailAdapter(transport).parse(RawMail.from_resource(resource)).text
        assert seen_at_draft_time == [expected]


class TestThePipelineRefusesWithoutAKey:
    def test_constructing_a_pipeline_needs_the_key(self, test_db, monkeypatch):
        monkeypatch.delenv(KEYS_SETTING)
        with pytest.raises(EncryptionKeyError):
            EmailBotPipeline(
                db=test_db,
                adapter=GmailAdapter(FakeTransport()),
                classifier=FakeClassifier(),
                notifier=RecordingNotifier(),
                settings=EmailBotSettings(mode="draft", escalation_owner_email=OWNER),
            )

    def test_build_pipeline_names_the_setting_before_touching_gmail(
        self, test_db, monkeypatch
    ):
        from unittest.mock import patch

        from app.services.email_bot.factory import (
            Credentials,
            EmailBotNotConfigured,
            build_pipeline,
        )

        monkeypatch.delenv(KEYS_SETTING)
        with (
            patch(
                "app.services.email_bot.factory.GmailTransport.from_credentials"
            ) as gmail,
            pytest.raises(EmailBotNotConfigured) as raised,
        ):
            build_pipeline(
                test_db,
                EmailBotSettings(mode="draft", escalation_owner_email=OWNER),
                credentials=Credentials(
                    gmail_client_id="id",
                    gmail_client_secret="secret",
                    gmail_refresh_token="token",
                ),
                bot_service=FakeBotService(),
            )
        assert KEYS_SETTING in str(raised.value)
        gmail.assert_not_called()


# The privacy sweep's corpus, reused so the two tests cover the same mail.
CORPUS = {
    "signup_en.json": signals(category="signup", confidence=0.94),
    "signup_vi.json": signals(category="signup", language="vi", confidence=0.89),
    "faq_en.json": signals(category="faq", confidence=0.86),
    "html_only.json": signals(category="faq", confidence=0.9),
    "empty_body.json": signals(category="faq", confidence=0.7),
    "newsletter.json": signals(category="automated", confidence=0.97),
    "vacation_autoreply.json": signals(category="automated", confidence=0.97),
    "bounce.json": signals(category="automated", confidence=0.97),
    "safeguarding_vi.json": signals(
        category="safeguarding_legal", language="vi", confidence=0.91
    ),
    "sponsorship_en.json": signals(category="sponsorship", confidence=0.82, money=True),
    "multi_topic_en.json": signals(category="donation", confidence=0.58, money=True),
}


@pytest.fixture
def corpus_run(test_db):
    fixtures = [load_gmail(name) for name in CORPUS]
    transport = FakeTransport(mails=fixtures)
    adapter = GmailAdapter(transport)
    pipeline = EmailBotPipeline(
        db=test_db,
        adapter=adapter,
        classifier=FakeClassifier(
            by_message={
                resource["id"]: CORPUS[name]
                for name, resource in zip(CORPUS, fixtures, strict=True)
            }
        ),
        notifier=RecordingNotifier(),
        settings=EmailBotSettings(
            mode="draft",
            escalation_owner_email=OWNER,
            signup_form_link=FORM,
            per_run_cap=50,
        ),
        bot_service=FakeBotService(),
    )
    summary = anyio.run(lambda: anyio.to_thread.run_sync(pipeline.run))
    parsed = {
        resource["id"]: adapter.parse(RawMail.from_resource(resource))
        for resource in fixtures
    }
    return {"summary": summary, "parsed": parsed, "transport": transport}


class TestTheWholeCorpusEndToEnd:
    def test_the_corpus_ran(self, corpus_run):
        summary = corpus_run["summary"]
        assert summary.processed == len(CORPUS)
        assert summary.aborted_reason is None
        assert summary.drafted > 0 and summary.forwarded > 0

    def test_every_stored_inbound_text_decrypts_to_the_original(
        self, corpus_run, test_db
    ):
        rows = test_db.query(Message).filter(Message.direction == "inbound").all()
        kept = [row for row in rows if row.body_encrypted is not None]
        # Every triaged mail is kept; only the guard-skipped ones are not.
        assert kept
        for row in kept:
            original = corpus_run["parsed"][row.provider_message_id]
            assert stored(row, "subject_encrypted") == original.subject
            assert stored(row, "body_encrypted") == original.text
            assert stored(row, "sender_address_encrypted") == (
                original.sender_address or None
            )

    def test_only_guard_skips_are_left_without_content(self, corpus_run, test_db):
        for row in test_db.query(Message).filter(Message.direction == "inbound"):
            if row.body_encrypted is None:
                assert row.action == "skipped"
                assert row.tier is None, "a triaged mail was not kept"

    def test_every_stored_outbound_text_is_exactly_what_was_drafted(
        self, corpus_run, test_db
    ):
        service = ConversationService(test_db)
        outbound = test_db.query(Message).filter(Message.direction == "outbound").all()
        assert outbound
        drafted = sorted(
            body.replace("\r\n", "\n").strip()
            for body in corpus_run["transport"].draft_bodies()
        )
        stored_texts = sorted(service.outbound_text(row).strip() for row in outbound)
        assert stored_texts == drafted

    def test_no_stored_column_is_readable_without_the_key(self, corpus_run, test_db):
        cipher = cipher_from_env()
        for row in test_db.query(Message):
            for column in (
                "subject_encrypted",
                "body_encrypted",
                "sender_address_encrypted",
            ):
                token = getattr(row, column)
                if token is not None:
                    assert token.startswith(f"vhenc:v1:{cipher.primary_key_id}:")
