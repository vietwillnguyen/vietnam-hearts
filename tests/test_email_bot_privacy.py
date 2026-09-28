"""Nothing the bot writes down may contain a subject, a body, or an address.

Three destinations, all of which persist further than they look:

- **Log lines.** ``PERSIST_LOGS_TO_DB`` puts application logs in the database and
  the admin dashboard renders them, so a logged address is a durable copy of it.
- **Exception messages.** Sentry runs with ``send_default_pii=True`` for request
  debugging, so anything an exception carries leaves the building.
- **Database rows.** The design stores sender hashes and outbound bot text, and
  deliberately not inbound bodies.

The headline test drives the real ``BotService.chat()`` with only retrieval and
generation mocked, because the log line this replaces
(``Processing chat message: {message[:100]}...``) lived inside that method, and a
test that mocked the whole service would not have noticed it.

Log capture goes through ``tests.fixtures.logs.attached_caplog`` rather than
plain ``caplog``: the app's loggers set ``propagate = False``, so a bare
``caplog`` assertion here would pass against an empty string and prove nothing.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import logging
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from app.models import Conversation, Message
from app.services.bot_service import BotService
from app.services.channels.gmail import GmailAdapter
from app.services.email_bot.pipeline import EmailBotPipeline
from app.services.email_bot.settings import EmailBotSettings
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.email_bot import (
    FakeClassifier,
    FakeTransport,
    RecordingNotifier,
    load_gmail,
    signals,
)
from tests.fixtures.logs import attached_caplog

OWNER = "owner@example.com"
FORM = "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"

# Every fixture, with the classification each one should get, so the sweep
# covers the draft path, the escalation path and the skip path in one run.
SWEEP = {
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


def secrets_in(fixtures) -> list[str]:
    """Distinctive strings from the fixtures that must never be written down.

    Long phrases from each body plus every address, rather than single words:
    "Hello" appearing in a log line proves nothing, whereas a whole sentence
    from somebody's mail is unambiguous.
    """
    from app.services.channels.mail_builder import extract_text

    forbidden = [payloads.TEST_SENDER, "news@newsletter.example.com"]
    for resource in fixtures:
        text = extract_text(resource)
        forbidden.extend(
            line.strip() for line in text.splitlines() if len(line.strip()) > 25
        )
        subject = next(
            (
                entry["value"]
                for entry in resource["payload"]["headers"]
                if entry["name"] == "Subject"
            ),
            "",
        )
        if len(subject) > 12:
            forbidden.append(subject)
    return [phrase for phrase in forbidden if phrase]


def real_bot_service() -> BotService:
    """A real ``BotService`` with only retrieval and generation mocked.

    The point of using the real one: the log line this test replaces lived
    inside ``chat()``, so a fully faked service would not notice its return.
    """
    bot = BotService.__new__(BotService)
    bot.knowledge_service = MagicMock()
    bot.knowledge_service.similarity_search = AsyncMock(
        return_value=[
            {
                "content": "Volunteers do not need a teaching certificate.",
                "similarity": 0.81,
                "source_document_id": "kb-doc",
            }
        ]
    )
    gemini = MagicMock()
    gemini.models.generate_content.return_value = MagicMock(
        text="No teaching certificate is needed to help as a teaching assistant."
    )
    bot.knowledge_service.gemini_client = gemini
    bot.document_service = MagicMock()
    bot.supabase = MagicMock()
    return bot


def sweep_pipeline(db, fixtures, notifier):
    transport = FakeTransport(mails=fixtures)
    adapter = GmailAdapter(transport)
    return (
        EmailBotPipeline(
            db=db,
            adapter=adapter,
            classifier=FakeClassifier(
                by_message={
                    resource["id"]: SWEEP[name]
                    for name, resource in zip(SWEEP, fixtures, strict=True)
                },
                default=signals(category="human_other", confidence=0.5),
            ),
            notifier=notifier,
            settings=EmailBotSettings(
                mode="draft",
                escalation_owner_email=OWNER,
                signup_form_link=FORM,
                per_run_cap=50,
            ),
            bot_service=real_bot_service(),
        ),
        transport,
    )


def run(pipeline):
    async def drive():
        return await anyio.to_thread.run_sync(pipeline.run)

    return anyio.run(drive)


@pytest.fixture
def swept(test_db, caplog):
    """Run the whole fixture corpus through the pipeline, capturing everything."""
    fixtures = [load_gmail(name) for name in SWEEP]
    notifier = RecordingNotifier()
    pipeline, transport = sweep_pipeline(test_db, fixtures, notifier)

    with attached_caplog(caplog, level=logging.DEBUG):
        summary = run(pipeline)

    return {
        "summary": summary,
        "records": list(caplog.records),
        "text": caplog.text,
        "forbidden": secrets_in(fixtures),
        "notifier": notifier,
        "transport": transport,
        "fixtures": fixtures,
    }


class TestTheSweepActuallyRan:
    def test_the_capture_is_not_vacuous(self, swept):
        # Without this, every assertion below would pass against silence. The
        # app's loggers do not propagate, so a plain caplog would capture
        # nothing at all and this whole file would be a no-op.
        assert swept["records"], "no log records were captured; the assertions are void"
        assert swept["text"].strip()

    def test_the_whole_corpus_was_processed(self, swept):
        summary = swept["summary"]
        assert summary.processed == len(SWEEP)
        assert summary.drafted > 0
        assert summary.forwarded > 0
        assert summary.skipped > 0
        assert summary.aborted_reason is None


class TestNoAddressIsEverLogged:
    def test_no_log_record_contains_an_at_sign(self, swept):
        offenders = [
            record.getMessage()
            for record in swept["records"]
            if "@" in record.getMessage()
        ]
        assert offenders == [], f"log lines carrying an address: {offenders}"

    def test_no_log_record_contains_a_sender_address(self, swept):
        for record in swept["records"]:
            assert payloads.TEST_SENDER not in record.getMessage()


class TestNoBodyOrSubjectIsEverLogged:
    def test_no_fixture_phrase_appears_in_any_log_record(self, swept):
        leaked = [phrase for phrase in swept["forbidden"] if phrase in swept["text"]]
        assert leaked == [], f"log output carrying mail content: {leaked}"

    def test_the_processing_chat_message_line_is_gone(self, swept):
        # It logged the first 100 characters of every question.
        assert "Processing chat message" not in swept["text"]

    def test_the_generated_response_is_not_logged_either(self, swept):
        # It quotes the retrieved context and answers the sender's question.
        assert "No teaching certificate is needed" not in swept["text"]

    def test_the_chat_source_no_longer_logs_the_message(self):
        import inspect

        source = inspect.getsource(BotService.chat)
        assert "Processing chat message" not in source
        assert "message[:100]" not in source

    def test_the_generation_source_no_longer_logs_the_answer(self):
        import inspect

        source = inspect.getsource(BotService._generate_contextual_response)
        assert "text[:100]" not in source


class TestExceptionMessagesCarryIdsOnly:
    def test_a_classifier_failure_message_carries_no_mail_content(self, test_db):
        from app.services.channels.base import IncomingMessage
        from app.services.triage.jev import JevClassifier
        from app.services.triage.litellm import LiteLLMClassifier
        from app.services.triage.protocol import TriageUnavailable

        message = IncomingMessage(
            channel="email",
            thread_key="thread-1",
            provider_message_id="msg-1",
            sender_key="hash",
            sender_address=payloads.TEST_SENDER,
            subject="Volunteering with Vietnam Hearts",
            text="I am moving to Ho Chi Minh City and would love to help.",
            rfc_message_id="<abc@x>",
        )

        broken_jev = MagicMock()
        broken_jev.system_one.side_effect = RuntimeError(
            f"provider echoed: {message.text} from {message.sender_address}"
        )
        broken_litellm = MagicMock(side_effect=RuntimeError("upstream 500"))

        for classifier in (
            JevClassifier(broken_jev),
            LiteLLMClassifier(completion=broken_litellm),
        ):
            with pytest.raises(TriageUnavailable) as raised:
                classifier.classify(message)

            text = str(raised.value)
            assert "msg-1" in text
            assert message.text not in text
            assert message.subject not in text
            assert "@" not in text

    def test_the_pipelines_own_abort_reason_carries_no_mail_content(self, swept):
        # It is written to a settings row that the dashboard renders as a
        # banner, so it has the same reach as a log line.
        for event in swept["notifier"].events:
            assert "@" not in event.reason
            for phrase in swept["forbidden"]:
                assert phrase not in event.reason


class TestTheDatabaseStoresNoInboundContent:
    def test_no_inbound_row_carries_text(self, swept, test_db):
        rows = test_db.query(Message).filter(Message.direction == "inbound").all()
        assert rows
        assert all(row.text is None for row in rows)

    def test_no_row_anywhere_carries_a_fixture_phrase_from_an_inbound_mail(
        self, swept, test_db
    ):
        stored = " ".join(
            str(value)
            for row in test_db.query(Message).all()
            for value in (row.text, row.category, row.classifier, row.triage_shadow)
            if value is not None
        )
        for phrase in swept["forbidden"]:
            assert phrase not in stored

    def test_no_conversation_row_carries_an_address(self, swept, test_db):
        for conversation in test_db.query(Conversation).all():
            assert "@" not in conversation.sender_key
            assert len(conversation.sender_key) == 64
            assert "@" not in (conversation.pause_reason or "")

    def test_outbound_bot_text_is_stored_because_it_is_ours(self, swept, test_db):
        # The one thing that is deliberately kept: the captain needs to see what
        # was offered on his behalf, and E2 compares it against what was sent.
        outbound = test_db.query(Message).filter(Message.direction == "outbound").all()
        assert outbound
        assert any(row.text for row in outbound)


class TestTheDiscordPayloadCarriesNoPII:
    def test_no_event_payload_contains_an_address_or_a_body(self, swept):
        from app.services.notifier import build_discord_payload

        for event in swept["notifier"].events:
            content = build_discord_payload(event)["content"]
            assert "@example.com" not in content
            for phrase in swept["forbidden"]:
                assert phrase not in content
