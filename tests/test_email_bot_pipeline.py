"""The pipeline end to end, against fakes for every external system.

Driven through ``anyio.to_thread.run_sync`` rather than by calling ``run()``
directly, so the ``anyio.from_thread.run`` bridge to the async
``BotService.chat()`` is genuinely exercised. Calling ``run()`` inline would take
the ``asyncio.run`` fallback and leave the bridge that production actually uses
untested.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

from datetime import UTC, datetime, timedelta

import anyio
import pytest

from app.models import EmailBotRun, Message
from app.services.bot_service import GenerationUnavailable, NoRelevantContext
from app.services.channels.gmail import GmailAdapter
from app.services.channels.gmail_transport import (
    LABEL_DRAFTED,
    LABEL_ESCALATED,
    LABEL_PAUSED,
    LABEL_SEEN,
    LABEL_SENT,
    LABEL_SKIPPED,
)
from app.services.conversation_service import (
    STATUS_PAUSED_HANDOFF,
    STATUS_PAUSED_MANUAL,
    ConversationService,
)
from app.services.email_bot.delivery import DeliveryMode, DraftSink
from app.services.email_bot.pipeline import (
    ALREADY_RUNNING,
    CIRCUIT_BREAKER_THRESHOLD,
    STALE_RUN_AFTER,
    EmailBotPipeline,
)
from app.services.email_bot.settings import EmailBotSettings
from app.services.knowledge_service import EmbeddingsUnavailable
from app.services.triage.protocol import TriageUnavailable
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.email_bot import (
    FakeBotService,
    FakeClassifier,
    FakeSendSink,
    FakeTransport,
    RecordingNotifier,
    SendingFakeTransport,
    load_gmail,
    signals,
)

OWNER = "owner@example.com"
FORM = "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"


def bot_settings(**overrides) -> EmailBotSettings:
    defaults = {
        "mode": "draft",
        "auto_languages": frozenset({"en"}),
        "escalation_owner_email": OWNER,
        "signup_form_link": FORM,
        "triage_confidence_threshold": 0.6,
        "answer_threshold": 0.5,
        "per_run_cap": 20,
    }
    defaults.update(overrides)
    return EmailBotSettings(**defaults)


def build(
    db,
    mails,
    classifier=None,
    bot_service=None,
    settings=None,
    threads=None,
    sinks=None,
    notifier=None,
    transport_class=FakeTransport,
):
    transport = transport_class(mails=mails, threads=threads)
    adapter = GmailAdapter(transport)
    pipeline = EmailBotPipeline(
        db=db,
        adapter=adapter,
        classifier=classifier or FakeClassifier(default=signals()),
        notifier=notifier or RecordingNotifier(),
        settings=settings or bot_settings(),
        bot_service=bot_service or FakeBotService(),
        sinks=sinks,
    )
    return pipeline, transport


def run(pipeline):
    """Run the pipeline the way FastAPI does: in an anyio worker thread."""

    async def drive():
        return await anyio.to_thread.run_sync(pipeline.run)

    return anyio.run(drive)


def outbound(db):
    return db.query(Message).filter(Message.direction == "outbound").all()


def inbound(db):
    return db.query(Message).filter(Message.direction == "inbound").all()


class TestTheBridgeToTheAsyncAnswerPath:
    def test_the_faq_answer_is_reached_from_a_worker_thread(self, test_db):
        # If the bridge were wrong, this is where "attached to a different
        # event loop" would surface.
        bot = FakeBotService()
        pipeline, _ = build(
            test_db,
            [load_gmail("faq_en.json")],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.9)),
            bot_service=bot,
        )
        summary = run(pipeline)

        assert summary.drafted == 1
        assert bot.calls[0]["channel"] == "email"
        assert bot.calls[0]["language"] == "en"

    def test_the_senders_language_is_passed_to_the_generator(self, test_db):
        bot = FakeBotService()
        pipeline, _ = build(
            test_db,
            [load_gmail("signup_vi.json")],
            classifier=FakeClassifier(
                default=signals(category="faq", language="vi", confidence=0.9)
            ),
            bot_service=bot,
        )
        run(pipeline)
        assert bot.calls[0]["language"] == "vi"


class TestGuardsRunBeforeAnyModel:
    @pytest.mark.parametrize(
        "fixture", ["newsletter.json", "vacation_autoreply.json", "bounce.json"]
    )
    def test_an_automated_mail_is_skipped_and_labelled(self, test_db, fixture):
        classifier = FakeClassifier(default=signals())
        pipeline, transport = build(
            test_db, [load_gmail(fixture)], classifier=classifier
        )

        summary = run(pipeline)

        assert summary.skipped == 1
        assert summary.drafted == 0
        # The guard runs before any model, so the classifier is never called.
        assert classifier.seen == []
        assert transport.drafts == {}
        labels = transport.labels_for(load_gmail(fixture)["id"])
        assert LABEL_SKIPPED in labels
        assert LABEL_SEEN in labels

    def test_a_skipped_mail_still_gets_an_audit_row(self, test_db):
        # Or the next run would process it again.
        pipeline, _ = build(test_db, [load_gmail("newsletter.json")])
        run(pipeline)

        rows = inbound(test_db)
        assert len(rows) == 1
        assert rows[0].action == "skipped"

    def test_a_mail_from_the_escalation_owner_is_skipped(self, test_db):
        # Review Focus item 3: our own mail coming back must never become a
        # conversation with a reply waiting in it.
        classifier = FakeClassifier(default=signals())
        pipeline, transport = build(
            test_db,
            [payloads.message(message_id="own-1", from_address=OWNER)],
            classifier=classifier,
        )
        summary = run(pipeline)

        assert summary.skipped == 1
        assert classifier.seen == []
        assert LABEL_SKIPPED in transport.labels_for("own-1")

    def test_a_mail_from_an_admin_address_is_skipped(self, test_db):
        transport = FakeTransport(
            mails=[
                payloads.message(message_id="admin-1", from_address="coord@example.com")
            ]
        )
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(default=signals()),
            notifier=RecordingNotifier(),
            settings=bot_settings(),
            bot_service=FakeBotService(),
            admin_emails=("coord@example.com",),
        )
        assert run(pipeline).skipped == 1

    def test_the_inbox_writing_to_itself_is_skipped(self, test_db):
        pipeline, _ = build(
            test_db,
            [payloads.message(message_id="self-1", from_address=payloads.TEST_INBOX)],
        )
        assert run(pipeline).skipped == 1

    def test_an_unparseable_sender_is_skipped_rather_than_drafted_to_nowhere(
        self, test_db
    ):
        pipeline, transport = build(
            test_db,
            [payloads.message(message_id="bad-from", from_address="not an address")],
        )
        summary = run(pipeline)

        assert summary.skipped == 1
        assert transport.drafts == {}


class TestDedupe:
    def test_a_second_poll_does_nothing_but_relabel(self, test_db):
        mails = [load_gmail("signup_en.json")]
        pipeline, transport = build(test_db, mails)
        first = run(pipeline)

        second_pipeline, second_transport = build(test_db, mails)
        second = run(second_pipeline)

        assert first.drafted == 1
        assert second.drafted == 0
        assert second_transport.drafts == {}
        # The marker label is re-applied so a run that died before labelling
        # converges on the next poll.
        assert second_transport.labels_for(mails[0]["id"]) == [LABEL_SEEN]

    def test_only_one_inbound_row_exists_after_two_polls(self, test_db):
        mails = [load_gmail("signup_en.json")]
        run(build(test_db, mails)[0])
        run(build(test_db, mails)[0])
        assert len(inbound(test_db)) == 1


class TestNeverTalkOverAHuman:
    def _thread_with_human_reply(self):
        thread = load_gmail("thread_with_human_reply.json")
        return thread["id"], thread["messages"]

    def test_a_thread_a_human_answered_is_paused_and_never_drafted(self, test_db):
        thread_id, messages = self._thread_with_human_reply()
        classifier = FakeClassifier(default=signals())
        pipeline, transport = build(
            test_db,
            [load_gmail("second_inbound.json")],
            classifier=classifier,
            threads={thread_id: messages},
        )

        summary = run(pipeline)

        assert summary.drafted == 0
        assert transport.drafts == {}
        # The thread is read before the bot acts, so this decision costs no
        # classifier call at all.
        assert classifier.seen == []
        labels = transport.labels_for("18f2a1b4c5d6e8ab")
        assert LABEL_PAUSED in labels
        assert LABEL_SEEN in labels

    def test_the_conversation_moves_to_paused_manual(self, test_db):
        thread_id, messages = self._thread_with_human_reply()
        pipeline, _ = build(
            test_db,
            [load_gmail("second_inbound.json")],
            threads={thread_id: messages},
        )
        run(pipeline)

        conversation = ConversationService(test_db).get_or_create(
            "email", thread_id, "unused"
        )
        assert conversation.status == STATUS_PAUSED_MANUAL

    def test_the_bots_own_outstanding_draft_is_deleted(self, test_db):
        # A stale draft under a thread the captain has taken over is an answer
        # that is no longer true, one click from being sent.
        thread_id, messages = self._thread_with_human_reply()

        first = load_gmail("faq_en.json")
        pipeline, transport = build(
            test_db,
            [first],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.9)),
        )
        run(pipeline)
        assert transport.drafts, "expected a draft from the first poll"

        second_pipeline, second_transport = build(
            test_db,
            [load_gmail("second_inbound.json")],
            threads={thread_id: messages},
        )
        run(second_pipeline)

        assert second_transport.deleted_drafts == ["draft-1"]
        drafted = [row for row in outbound(test_db) if row.action == "drafted"]
        assert drafted[0].draft_outcome == "deleted"

    def test_the_bots_own_sent_reply_is_not_mistaken_for_a_human(self, test_db):
        # Without subtracting the bot's own recorded ids, every thread the bot
        # replied to would pause itself on the next poll.
        thread_id, messages = self._thread_with_human_reply()
        service = ConversationService(test_db)
        conversation = service.get_or_create("email", thread_id, "hash-a")
        service.record_outbound(
            conversation,
            text="the bot's own reply",
            action="sent",
            language="en",
            gmail_message_id_out=messages[1]["id"],
        )

        pipeline, transport = build(
            test_db,
            [load_gmail("second_inbound.json")],
            classifier=FakeClassifier(default=signals(category="human_other")),
            threads={thread_id: messages},
        )
        summary = run(pipeline)

        assert LABEL_PAUSED not in transport.labels_for("18f2a1b4c5d6e8ab")
        assert summary.forwarded == 1

    def test_the_bots_own_pending_draft_is_not_mistaken_for_a_human(self, test_db):
        # threads.get returns unsent drafts as thread messages From the inbox,
        # and the draft's message id is never recorded against the bot.
        first = load_gmail("faq_en.json")
        second = load_gmail("second_inbound.json")
        run(
            build(
                test_db,
                [first],
                classifier=FakeClassifier(
                    default=signals(category="faq", confidence=0.9)
                ),
            )[0]
        )
        pending_draft = payloads.message(
            message_id="r-bot-draft",
            thread_id=first["threadId"],
            from_address=payloads.TEST_INBOX,
            to_address=payloads.TEST_SENDER,
            label_ids=("DRAFT",),
        )

        notifier = RecordingNotifier()
        pipeline, transport = build(
            test_db,
            [second],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.9)),
            threads={first["threadId"]: [first, pending_draft, second]},
            notifier=notifier,
        )
        summary = run(pipeline)

        assert LABEL_PAUSED not in transport.labels_for(second["id"])
        assert transport.deleted_drafts == []
        assert summary.forwarded == 1
        assert len(notifier.events) == 1


class DraftFailingTransport(FakeTransport):
    def create_draft(self, thread_id, mime):
        raise RuntimeError("drafts.create quota exceeded")


class TestADraftFailureNeverLosesTheMail:
    """The inbound row is committed first, so the next run would dedupe it."""

    def test_an_escalation_is_forwarded_when_the_holding_draft_fails(self, test_db):
        notifier = RecordingNotifier()
        pipeline, transport = build(
            test_db,
            [load_gmail("safeguarding_vi.json")],
            classifier=FakeClassifier(
                default=signals(
                    category="safeguarding_legal", language="vi", confidence=0.9
                )
            ),
            notifier=notifier,
            transport_class=DraftFailingTransport,
        )
        summary = run(pipeline)

        assert notifier.categories == ["safeguarding_legal"]
        assert summary.forwarded == 1
        assert summary.drafted == 0
        assert summary.errors == 1
        labels = transport.labels_for("18f2a1b4c5d6e878")
        assert LABEL_ESCALATED in labels
        assert LABEL_DRAFTED not in labels
        assert labels[-1] == LABEL_SEEN
        conversation = ConversationService(test_db).get_or_create(
            "email", "18f2a1b4c5d6e878", "unused"
        )
        assert conversation.status == STATUS_PAUSED_HANDOFF

    @pytest.mark.parametrize(
        "fixture,category",
        [("faq_en.json", "faq"), ("signup_en.json", "signup")],
    )
    def test_an_answer_that_cannot_be_drafted_goes_to_a_person(
        self, test_db, fixture, category
    ):
        notifier = RecordingNotifier()
        mail = load_gmail(fixture)
        pipeline, transport = build(
            test_db,
            [mail],
            classifier=FakeClassifier(
                default=signals(category=category, confidence=0.9)
            ),
            bot_service=FakeBotService(
                {"response": "A confident answer.", "confidence": 0.9}
            ),
            notifier=notifier,
            transport_class=DraftFailingTransport,
        )
        summary = run(pipeline)

        assert len(notifier.events) == 1
        assert notifier.events[0].decision.tier == "needs_admin"
        assert summary.errors == 1
        assert outbound(test_db) == []
        labels = transport.labels_for(mail["id"])
        assert LABEL_ESCALATED in labels
        assert labels[-1] == LABEL_SEEN

    def test_an_answer_outage_that_trips_the_breaker_still_escalates(self, test_db):
        mails = [
            payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
            for index in range(CIRCUIT_BREAKER_THRESHOLD)
        ]
        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            mails,
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.9)),
            bot_service=FakeBotService(error=GenerationUnavailable("quota")),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert "consecutive" in summary.aborted_reason
        assert len(notifier.events) == CIRCUIT_BREAKER_THRESHOLD


class TestSignupAnswer:
    def test_a_signup_mail_is_drafted_from_the_template(self, test_db):
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
        )
        summary = run(pipeline)

        assert summary.drafted == 1
        body = transport.draft_body()
        assert "interest in volunteering with Vietnam Hearts" in body
        assert FORM in body

    def test_no_retrieval_happens_for_a_signup(self, test_db):
        # The sign-up answer is a fixed template. A model that could rewrite it
        # could also get it wrong.
        bot = FakeBotService()
        pipeline, _ = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            bot_service=bot,
        )
        run(pipeline)
        assert bot.calls == []

    def test_a_vietnamese_signup_is_drafted_in_vietnamese(self, test_db):
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_vi.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", language="vi", confidence=0.89)
            ),
        )
        run(pipeline)

        # Vietnamese forces base64 transfer encoding, so the decoded body is
        # the only honest thing to assert on.
        assert "tình nguyện viên" in transport.draft_body()

    def test_the_signup_labels_carry_the_category(self, test_db):
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
        )
        run(pipeline)

        labels = transport.labels_for("18f2a1b4c5d6e7f0")
        assert LABEL_DRAFTED in labels
        assert "VH-Bot/signup" in labels

    def test_an_unconfigured_form_link_escalates_instead_of_lying(self, test_db):
        notifier = RecordingNotifier()
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            settings=bot_settings(signup_form_link=""),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert "not configured" in notifier.events[0].reason
        # The holding message is still offered, so the sender is not left in
        # silence.
        assert summary.drafted == 1


class TestFaqAnswer:
    def test_a_confident_answer_is_drafted(self, test_db):
        pipeline, transport = build(
            test_db,
            [load_gmail("faq_en.json")],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
            bot_service=FakeBotService(
                {
                    "response": "No certificate is needed.",
                    "confidence": 0.82,
                    "sources": ["kb"],
                    "context_used": 2,
                }
            ),
        )
        summary = run(pipeline)

        assert summary.drafted == 1
        assert summary.forwarded == 0
        assert "No certificate is needed." in transport.draft_body()

    def test_the_retrieval_confidence_and_sources_are_audited(self, test_db):
        pipeline, _ = build(
            test_db,
            [load_gmail("faq_en.json")],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
            bot_service=FakeBotService(
                {
                    "response": "An answer.",
                    "confidence": 0.77,
                    "sources": ["kb-doc"],
                    "context_used": 3,
                }
            ),
        )
        run(pipeline)

        row = [r for r in outbound(test_db) if r.action == "drafted"][0]
        assert row.triage_confidence == pytest.approx(0.77)
        assert row.sources == ["kb-doc"]
        assert row.category == "faq"

    def test_a_similarity_below_the_threshold_escalates(self, test_db):
        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            [load_gmail("faq_en.json")],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
            bot_service=FakeBotService({"response": "A guess.", "confidence": 0.31}),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert "0.31" in notifier.events[0].reason
        # The answer the model produced is discarded, not offered.
        assert all("A guess." not in (row.text or "") for row in outbound(test_db))

    def test_the_refusal_sentinel_escalates(self, test_db):
        from app.services.triage.prompts import REFUSAL_SENTINEL

        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            [load_gmail("faq_en.json")],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
            bot_service=FakeBotService(
                {"response": REFUSAL_SENTINEL, "confidence": 0.9}
            ),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert "insufficient context" in notifier.events[0].reason

    def test_no_relevant_context_escalates(self, test_db):
        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            [load_gmail("faq_en.json")],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
            bot_service=FakeBotService(error=NoRelevantContext("nothing relevant")),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert "does not cover" in notifier.events[0].reason
        # A knowledge base gap is not an infrastructure failure.
        assert summary.errors == 0

    @pytest.mark.parametrize(
        "error", [EmbeddingsUnavailable("down"), GenerationUnavailable("quota")]
    )
    def test_an_outage_escalates_and_counts_as_an_infrastructure_failure(
        self, test_db, error
    ):
        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            [load_gmail("faq_en.json")],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
            bot_service=FakeBotService(error=error),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert summary.errors == 1

    def test_an_empty_body_escalates_rather_than_crashing(self, test_db):
        # Review Focus item 1, second half.
        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            [load_gmail("empty_body.json")],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
            bot_service=FakeBotService(error=NoRelevantContext("empty question")),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert summary.errors == 0


class TestEscalation:
    def test_an_executive_category_escalates_and_drafts_the_holding_message(
        self, test_db
    ):
        notifier = RecordingNotifier()
        pipeline, transport = build(
            test_db,
            [load_gmail("sponsorship_en.json")],
            classifier=FakeClassifier(
                default=signals(category="sponsorship", confidence=0.82, money=True)
            ),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert summary.drafted == 1
        assert notifier.categories == ["sponsorship"]
        assert "better answered by a person" in transport.draft_body()

    def test_an_executive_category_escalates_even_at_high_similarity(self, test_db):
        # The category gate runs before anything the answer path could say.
        bot = FakeBotService({"response": "A confident answer.", "confidence": 0.99})
        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            [load_gmail("safeguarding_vi.json")],
            classifier=FakeClassifier(
                default=signals(
                    category="safeguarding_legal", language="vi", confidence=0.91
                )
            ),
            bot_service=bot,
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        # Retrieval was never even attempted.
        assert bot.calls == []
        assert notifier.events[0].decision.tier == "needs_executive"

    def test_the_thread_is_paused_for_handoff(self, test_db):
        pipeline, _ = build(
            test_db,
            [load_gmail("sponsorship_en.json")],
            classifier=FakeClassifier(
                default=signals(category="sponsorship", confidence=0.8)
            ),
        )
        run(pipeline)

        conversation = ConversationService(test_db).get_or_create(
            "email", "18f2a1b4c5d6e889", "unused"
        )
        assert conversation.status == STATUS_PAUSED_HANDOFF

    def test_the_escalation_labels_are_applied(self, test_db):
        pipeline, transport = build(
            test_db,
            [load_gmail("sponsorship_en.json")],
            classifier=FakeClassifier(
                default=signals(category="sponsorship", confidence=0.8)
            ),
        )
        run(pipeline)

        labels = transport.labels_for("18f2a1b4c5d6e889")
        assert LABEL_ESCALATED in labels
        assert "VH-Bot/sponsorship" in labels
        assert LABEL_DRAFTED in labels
        assert LABEL_SEEN in labels

    def test_the_holding_message_is_in_the_senders_language(self, test_db):
        pipeline, transport = build(
            test_db,
            [load_gmail("safeguarding_vi.json")],
            classifier=FakeClassifier(
                default=signals(
                    category="safeguarding_legal", language="vi", confidence=0.9
                )
            ),
        )
        run(pipeline)

        row = [r for r in outbound(test_db) if r.action == "drafted"][0]
        assert row.language == "vi"
        assert "trợ lý tự động" in row.text

    def test_escalation_happens_even_when_the_mode_delivers_nothing(self, test_db):
        # Knowing about a safeguarding mail is not something a delivery switch
        # should be able to turn off. Mode off aborts the run entirely, so this
        # is the no-sink case: the pipeline still labels, forwards and pauses.
        notifier = RecordingNotifier()
        pipeline, transport = build(
            test_db,
            [load_gmail("safeguarding_vi.json")],
            classifier=FakeClassifier(
                default=signals(
                    category="safeguarding_legal", language="vi", confidence=0.9
                )
            ),
            notifier=notifier,
            sinks={},
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert summary.drafted == 0
        assert transport.drafts == {}
        assert LABEL_ESCALATED in transport.labels_for("18f2a1b4c5d6e878")


class TestTheOneReplyCap:
    def test_a_second_inbound_on_a_replied_thread_gets_no_second_reply(self, test_db):
        """Review Focus item 2, in full."""
        first = load_gmail("faq_en.json")
        second = load_gmail("second_inbound.json")
        notifier = RecordingNotifier()

        pipeline, transport = build(
            test_db,
            [first],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
        )
        run(pipeline)
        assert len(transport.drafts) == 1

        second_pipeline, second_transport = build(
            test_db,
            [second],
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.86)),
            notifier=notifier,
        )
        summary = run(second_pipeline)

        # No second reply...
        assert second_transport.drafts == {}
        assert summary.drafted == 0
        # ...but it is still forwarded and posted, and both inbound mails are
        # recorded.
        assert summary.forwarded == 1
        assert len(notifier.events) == 1
        assert len(inbound(test_db)) == 2

    def test_the_second_inbound_is_recorded_as_paused(self, test_db):
        first = load_gmail("faq_en.json")
        run(
            build(
                test_db,
                [first],
                classifier=FakeClassifier(
                    default=signals(category="faq", confidence=0.86)
                ),
            )[0]
        )
        run(
            build(
                test_db,
                [load_gmail("second_inbound.json")],
                classifier=FakeClassifier(
                    default=signals(category="faq", confidence=0.86)
                ),
            )[0]
        )

        rows = {row.provider_message_id: row for row in inbound(test_db)}
        assert rows["18f2a1b4c5d6e8ab"].tier == "needs_admin"


class TestSkipTier:
    def test_an_automated_classification_is_skipped_with_no_reply(self, test_db):
        # The second line of defence behind the header guards. A mail that got
        # past them but reads as machine-generated must not earn a holding
        # message.
        notifier = RecordingNotifier()
        pipeline, transport = build(
            test_db,
            [payloads.message(message_id="sneaky-1", text="Your invoice is attached.")],
            classifier=FakeClassifier(
                default=signals(category="automated", confidence=0.2)
            ),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.skipped == 1
        assert summary.drafted == 0
        assert summary.forwarded == 0
        assert notifier.events == []
        assert transport.drafts == {}
        assert LABEL_SKIPPED in transport.labels_for("sneaky-1")


class TestFailClosed:
    def test_a_classifier_outage_escalates_to_a_person(self, test_db):
        notifier = RecordingNotifier()
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(default=TriageUnavailable("jev is down")),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert summary.errors == 1
        assert notifier.events[0].decision.tier == "needs_admin"
        assert notifier.events[0].decision.classifier == "unavailable"
        # Never a guess: no sign-up template went out on an unclassified mail.
        assert FORM not in transport.draft_body()

    def test_a_low_confidence_answerable_mail_escalates(self, test_db):
        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.4)
            ),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        assert "below threshold" in notifier.events[0].reason

    def test_asking_for_a_human_escalates(self, test_db):
        notifier = RecordingNotifier()
        pipeline, _ = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.95, asks_for_human=True)
            ),
            notifier=notifier,
        )
        run(pipeline)
        assert "asked for a person" in notifier.events[0].reason

    def test_an_unknown_language_escalates_with_the_english_holding_message(
        self, test_db
    ):
        pipeline, _ = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", language="other", confidence=0.95)
            ),
        )
        summary = run(pipeline)

        assert summary.forwarded == 1
        row = [r for r in outbound(test_db) if r.action == "drafted"][0]
        assert "automated assistant" in row.text


class TestCircuitBreaker:
    def test_three_consecutive_failures_abort_the_run(self, test_db):
        mails = [
            payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
            for index in range(6)
        ]
        pipeline, transport = build(
            test_db,
            mails,
            classifier=FakeClassifier(default=TriageUnavailable("everything is down")),
        )
        summary = run(pipeline)

        assert summary.aborted_reason is not None
        assert "consecutive" in summary.aborted_reason
        # A systemic outage must not turn into six forwards.
        assert summary.processed == CIRCUIT_BREAKER_THRESHOLD
        assert summary.forwarded < len(mails)

    def test_the_remaining_mail_is_left_unlabelled_for_the_next_run(self, test_db):
        mails = [
            payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
            for index in range(6)
        ]
        pipeline, transport = build(
            test_db,
            mails,
            classifier=FakeClassifier(default=TriageUnavailable("down")),
        )
        run(pipeline)

        touched = {message_id for message_id, _ in transport.applied}
        assert "m-5" not in touched

    def test_the_abort_reason_is_recorded_on_the_run_row(self, test_db):
        mails = [
            payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
            for index in range(4)
        ]
        pipeline, _ = build(
            test_db, mails, classifier=FakeClassifier(default=TriageUnavailable("down"))
        )
        run(pipeline)

        row = test_db.query(EmailBotRun).order_by(EmailBotRun.id.desc()).first()
        assert row.aborted_reason is not None
        assert row.finished_at is not None

    def test_an_isolated_failure_does_not_stop_the_run(self, test_db):
        # A single transient failure among healthy messages is normal.
        good = signals(category="signup", confidence=0.94)
        pipeline, _ = build(
            test_db,
            [
                payloads.message(message_id="m-1", thread_id="t-1"),
                payloads.message(message_id="m-2", thread_id="t-2"),
                payloads.message(message_id="m-3", thread_id="t-3"),
            ],
            classifier=FakeClassifier(
                by_message={"m-2": TriageUnavailable("blip")}, default=good
            ),
        )
        summary = run(pipeline)

        assert summary.aborted_reason is None
        assert summary.processed == 3
        assert summary.errors == 1

    def test_the_failure_counter_resets_after_a_success(self, test_db):
        good = signals(category="signup", confidence=0.94)
        pipeline, _ = build(
            test_db,
            [
                payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
                for index in range(1, 6)
            ],
            classifier=FakeClassifier(
                by_message={
                    "m-1": TriageUnavailable("blip"),
                    "m-2": TriageUnavailable("blip"),
                    "m-4": TriageUnavailable("blip"),
                    "m-5": TriageUnavailable("blip"),
                },
                default=good,
            ),
        )
        summary = run(pipeline)

        # Two failures, a success, then two more: never three in a row.
        assert summary.aborted_reason is None
        assert summary.processed == 5


class TestPerRunCap:
    def test_the_cap_bounds_what_is_processed(self, test_db):
        mails = [
            payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
            for index in range(10)
        ]
        pipeline, transport = build(
            test_db,
            mails,
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            settings=bot_settings(per_run_cap=3),
        )
        summary = run(pipeline)

        assert summary.processed == 3
        assert summary.drafted == 3

    def test_the_cap_is_passed_to_the_listing(self, test_db):
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            settings=bot_settings(per_run_cap=5),
        )
        run(pipeline)
        assert transport.list_calls[0]["limit"] == 5

    def test_the_leftovers_are_left_unlabelled(self, test_db):
        mails = [
            payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
            for index in range(10)
        ]
        pipeline, transport = build(
            test_db,
            mails,
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            settings=bot_settings(per_run_cap=3),
        )
        run(pipeline)

        touched = {message_id for message_id, _ in transport.applied}
        assert len(touched) == 3


class TestLabelOrdering:
    def test_the_marker_label_is_applied_last(self, test_db):
        # VH-Bot/Seen goes on after the audit row is committed, so anything
        # unfinished is simply picked up by the next run.
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
        )
        run(pipeline)

        labels = transport.labels_for("18f2a1b4c5d6e7f0")
        assert labels[-1] == LABEL_SEEN

    @pytest.mark.parametrize(
        "fixture,category",
        [
            ("newsletter.json", None),
            ("sponsorship_en.json", "sponsorship"),
            ("signup_en.json", "signup"),
        ],
    )
    def test_every_outcome_ends_with_the_marker(self, test_db, fixture, category):
        classifier = FakeClassifier(
            default=signals(category=category or "automated", confidence=0.9)
        )
        pipeline, transport = build(
            test_db, [load_gmail(fixture)], classifier=classifier
        )
        run(pipeline)

        message_id = load_gmail(fixture)["id"]
        assert transport.labels_for(message_id)[-1] == LABEL_SEEN

    def test_a_labelling_failure_does_not_undo_committed_work(self, test_db):
        # Labels are the mirror, never the source of truth.
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
        )

        def explode(message_id, label_ids):
            raise RuntimeError("Gmail rejected the label")

        transport.add_labels = explode
        summary = run(pipeline)

        assert summary.drafted == 1
        assert summary.aborted_reason is None
        assert len(inbound(test_db)) == 1


class TestTheRunLock:
    def test_a_concurrent_retry_does_nothing(self, test_db):
        """Review Focus item 5."""
        test_db.add(
            EmailBotRun(
                started_at=datetime.now(UTC),
                mode="draft",
                listed=0,
                processed=0,
                drafted=0,
                sent=0,
                forwarded=0,
                skipped=0,
                errors=0,
            )
        )
        test_db.commit()

        pipeline, transport = build(test_db, [load_gmail("signup_en.json")])
        summary = run(pipeline)

        assert summary.aborted_reason == ALREADY_RUNNING
        assert transport.list_calls == []
        assert transport.drafts == {}

    def test_a_run_older_than_the_stale_window_is_treated_as_dead(self, test_db):
        # A Cloud Run instance recycled mid-poll must not block every later run
        # forever.
        stale = EmailBotRun(
            started_at=datetime.now(UTC) - STALE_RUN_AFTER - timedelta(minutes=1),
            mode="draft",
            listed=0,
            processed=0,
            drafted=0,
            sent=0,
            forwarded=0,
            skipped=0,
            errors=0,
        )
        test_db.add(stale)
        test_db.commit()

        pipeline, _ = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
        )
        summary = run(pipeline)

        assert summary.aborted_reason is None
        assert summary.drafted == 1
        test_db.refresh(stale)
        assert stale.finished_at is not None
        assert "abandoned" in stale.aborted_reason

    def test_a_finished_run_does_not_block_the_next(self, test_db):
        mails = [load_gmail("signup_en.json")]
        run(build(test_db, mails)[0])

        second = run(build(test_db, [load_gmail("faq_en.json")])[0])
        assert second.aborted_reason is None

    def test_every_run_is_recorded(self, test_db):
        pipeline, _ = build(test_db, [load_gmail("signup_en.json")])
        run(pipeline)

        row = test_db.query(EmailBotRun).one()
        assert row.mode == "draft"
        assert row.finished_at is not None
        assert row.listed == 1
        assert row.processed == 1


class TestPreflightRefusals:
    def test_mode_off_does_nothing_at_all(self, test_db):
        pipeline, transport = build(
            test_db, [load_gmail("signup_en.json")], settings=bot_settings(mode="off")
        )
        summary = run(pipeline)

        assert summary.aborted_reason == "mode is off"
        assert transport.list_calls == []
        assert test_db.query(EmailBotRun).count() == 0

    def test_an_unset_escalation_owner_makes_the_bot_inert(self, test_db):
        # An escalation with nowhere to go would be silently dropped, so the bot
        # refuses to run rather than draft replies while losing handoffs.
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            settings=bot_settings(escalation_owner_email=""),
        )
        summary = run(pipeline)

        assert "ESCALATION_OWNER_EMAIL" in summary.aborted_reason
        assert transport.list_calls == []

    def test_an_unknown_mode_reads_as_off(self, test_db):
        pipeline, transport = build(
            test_db, [load_gmail("signup_en.json")], settings=bot_settings(mode="aut")
        )
        assert run(pipeline).mode is DeliveryMode.OFF
        assert transport.list_calls == []


class TestDraftModeStillSendsNothing:
    """What E1 established, re-asserted now that a send path exists.

    These used to hold because the codebase had no way to send at all. They now
    hold because ``choose_sink`` refuses, which is a weaker guarantee and so
    worth testing harder: a send sink is present in the mapping below and must
    still never be chosen.
    """

    def test_a_full_run_over_every_fixture_never_reaches_a_send_sink(self, test_db):
        # The phase's headline acceptance criterion: everything drafts and
        # escalates, and a send sink present in the mapping is still never
        # chosen, because no code path can select it while the mode is draft.
        send = FakeSendSink()
        fixtures = [
            "signup_en.json",
            "signup_vi.json",
            "faq_en.json",
            "html_only.json",
            "empty_body.json",
            "newsletter.json",
            "vacation_autoreply.json",
            "safeguarding_vi.json",
            "sponsorship_en.json",
            "multi_topic_en.json",
        ]
        mails = [load_gmail(name) for name in fixtures]
        by_message = {
            "18f2a1b4c5d6e7f0": signals(category="signup", confidence=0.94),
            "18f2a1b4c5d6e801": signals(
                category="signup", language="vi", confidence=0.89
            ),
            "18f2a1b4c5d6e812": signals(category="faq", confidence=0.86),
            "18f2a1b4c5d6e823": signals(category="signup", confidence=0.9),
            "18f2a1b4c5d6e834": signals(category="faq", confidence=0.7),
            "18f2a1b4c5d6e878": signals(
                category="safeguarding_legal", language="vi", confidence=0.91
            ),
            "18f2a1b4c5d6e889": signals(
                category="sponsorship", confidence=0.82, money=True
            ),
            "18f2a1b4c5d6e89a": signals(
                category="donation", confidence=0.58, money=True
            ),
        }

        transport = FakeTransport(mails=mails)
        adapter = GmailAdapter(transport)
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=adapter,
            classifier=FakeClassifier(
                by_message=by_message,
                default=signals(category="automated", confidence=0.97),
            ),
            notifier=RecordingNotifier(),
            settings=bot_settings(),
            bot_service=FakeBotService(),
            sinks={"draft": DraftSink(adapter), "send": send},
        )
        summary = run(pipeline)

        assert summary.sent == 0
        assert send.delivered == []
        assert summary.drafted > 0
        assert summary.forwarded > 0
        assert summary.aborted_reason is None

    def test_auto_mode_with_no_send_sink_in_the_mapping_drafts(self, test_db):
        # A sink mapping assembled without a send sink has to degrade to
        # drafting rather than raise or silently deliver nothing.
        from app.services.channels.gmail import GmailAdapter
        from app.services.email_bot.delivery import DraftSink

        transport = FakeTransport(mails=[load_gmail("signup_en.json")])
        adapter = GmailAdapter(transport)
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=adapter,
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            notifier=RecordingNotifier(),
            settings=bot_settings(mode="auto"),
            bot_service=FakeBotService(),
            sinks={"draft": DraftSink(adapter)},
        )
        summary = run(pipeline)

        assert summary.sent == 0
        assert summary.drafted == 1

    def test_auto_mode_against_a_transport_that_cannot_send_still_tells_a_person(
        self, test_db
    ):
        # The mode says auto and the send sink exists, but the transport has no
        # send path. The inbound row is already committed by then, so the mail
        # would be deduped as done on the next run: it has to reach somebody
        # now rather than be lost to a misconfiguration.
        notifier = RecordingNotifier()
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            settings=bot_settings(mode="auto"),
            notifier=notifier,
        )
        summary = run(pipeline)

        assert summary.sent == 0
        assert summary.forwarded == 1
        assert len(notifier.events) == 1
        assert not hasattr(transport, "send_reply")

    def test_a_draft_mode_run_builds_nothing_that_can_send(self, test_db):
        # The stronger property, still true: in draft mode the pipeline holds
        # no object capable of sending, rather than holding one it declines to
        # use.
        from app.services.email_bot.delivery import SendSink

        pipeline, _ = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            settings=bot_settings(mode="draft"),
        )
        assert "send" not in pipeline.sinks
        assert not any(isinstance(s, SendSink) for s in pipeline.sinks.values())


class TestTheInboxAddressPreflight:
    """An unresolvable inbox address stops the run instead of eating the mail.

    ``human_replied`` has no safe answer without an inbox address. False lets
    the bot reply over a human; True pauses the thread, and because the inbound
    row is committed before the pause, the mail is permanently deduped as done
    with nobody told. So the decision belongs before any mail is touched, in
    the same place as the ``ESCALATION_OWNER_EMAIL`` refusal.
    """

    def test_an_empty_inbox_address_refuses_to_poll(self, test_db):
        transport = FakeTransport(
            mails=[load_gmail("safeguarding_vi.json")], inbox_address=""
        )
        notifier = RecordingNotifier()
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(
                default=signals(category="safeguarding_legal", language="vi")
            ),
            notifier=notifier,
            settings=bot_settings(),
            bot_service=FakeBotService(),
        )

        summary = run(pipeline)

        assert "inbox address" in summary.aborted_reason
        # Nothing was listed, so nothing was audited and nothing was dropped.
        assert transport.list_calls == []
        assert inbound(test_db) == []
        assert notifier.events == []

    def test_a_safeguarding_mail_is_not_silently_paused(self, test_db):
        # The regression this exists for: with the guard failing closed and no
        # pre-flight, this mail was paused, labelled and recorded as done
        # without ever being forwarded.
        transport = FakeTransport(
            mails=[load_gmail("safeguarding_vi.json")], inbox_address=""
        )
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(
                default=signals(category="safeguarding_legal", language="vi")
            ),
            notifier=RecordingNotifier(),
            settings=bot_settings(),
            bot_service=FakeBotService(),
        )

        run(pipeline)

        assert transport.applied == []
        assert LABEL_PAUSED not in transport.labels_for("18f2a1b4c5d6e878")

    def test_a_grant_that_cannot_be_read_refuses_to_poll(self, test_db):
        # A revoked or broken grant: users.getProfile raises rather than
        # returning an address.
        class BrokenGrant(FakeTransport):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                type(self).inbox_address = property(self._refuse)

            @staticmethod
            def _refuse(_self):
                raise RuntimeError("invalid_grant")

        transport = BrokenGrant(mails=[load_gmail("signup_en.json")])
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(default=signals()),
            notifier=RecordingNotifier(),
            settings=bot_settings(),
            bot_service=FakeBotService(),
        )

        summary = run(pipeline)

        assert "could not be read" in summary.aborted_reason
        assert transport.list_calls == []

    def test_the_refusal_is_recorded_for_the_dashboard_banner(self, test_db):
        from app.services.email_bot.settings import SETTING_LAST_ERROR
        from app.services.settings_service import get_setting

        transport = FakeTransport(mails=[], inbox_address="")
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(default=signals()),
            notifier=RecordingNotifier(),
            settings=bot_settings(),
            bot_service=FakeBotService(),
        )
        run(pipeline)

        # A broken grant is what the banner is for, unlike an unset setting,
        # which is already visible on the settings page.
        assert "inbox address" in (get_setting(test_db, SETTING_LAST_ERROR) or "")

    def test_no_run_row_is_created_for_a_refusal(self, test_db):
        transport = FakeTransport(mails=[], inbox_address="")
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(default=signals()),
            notifier=RecordingNotifier(),
            settings=bot_settings(),
            bot_service=FakeBotService(),
        )
        run(pipeline)

        assert test_db.query(EmailBotRun).count() == 0

    def test_a_resolvable_address_polls_normally(self, test_db):
        pipeline, transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
        )
        summary = run(pipeline)

        assert summary.aborted_reason is None
        assert summary.drafted == 1


class TestADraftIsNeverOrphaned:
    """A draft in Gmail with no audit row can never be found or deleted again.

    ``outstanding_draft`` reads the rows, so an unrecorded draft is invisible
    to ``_take_over`` for good, and it sits in the thread one click from being
    sent with no record that the bot wrote it.
    """

    def _pipeline(self, test_db, transport):
        return EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            notifier=RecordingNotifier(),
            settings=bot_settings(),
            bot_service=FakeBotService(),
        )

    def test_a_failed_audit_write_deletes_the_draft(self, test_db, monkeypatch):
        transport = FakeTransport(mails=[load_gmail("signup_en.json")])
        pipeline = self._pipeline(test_db, transport)

        def explode(*args, **kwargs):
            raise RuntimeError("the commit failed")

        monkeypatch.setattr(pipeline.conversations, "record_outbound", explode)

        run(pipeline)

        # The draft was created, then removed, so nothing is left behind.
        assert transport.deleted_drafts == ["draft-1"]
        assert transport.drafts == {}

    def test_the_mail_still_reaches_a_person(self, test_db, monkeypatch):
        transport = FakeTransport(mails=[load_gmail("signup_en.json")])
        notifier = RecordingNotifier()
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            notifier=notifier,
            settings=bot_settings(),
            bot_service=FakeBotService(),
        )

        calls = {"n": 0}
        original = pipeline.conversations.record_outbound

        def fail_first(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("the commit failed")
            return original(*args, **kwargs)

        monkeypatch.setattr(pipeline.conversations, "record_outbound", fail_first)

        summary = run(pipeline)

        # The inbound row is already committed, so the next run dedupes this
        # mail as done. A person has to hear about it now or never.
        assert summary.forwarded == 1
        assert len(notifier.events) == 1

    def test_the_session_is_usable_again_afterwards(self, test_db, monkeypatch):
        # Without the rollback, the fallback escalation's own pause() commit
        # raises too, which re-opens the lost-mail path.
        transport = FakeTransport(mails=[load_gmail("signup_en.json")])
        pipeline = self._pipeline(test_db, transport)

        from sqlalchemy import text

        def explode(*args, **kwargs):
            test_db.execute(text("SELECT * FROM does_not_exist"))

        monkeypatch.setattr(pipeline.conversations, "record_outbound", explode)
        summary = run(pipeline)

        assert summary.aborted_reason is None
        # The conversation was still paused, which needed a working session.
        conversation = ConversationService(test_db).get_or_create(
            "email", "18f2a1b4c5d6e7f0", "unused"
        )
        assert conversation.status == STATUS_PAUSED_HANDOFF

    def test_a_cleanup_failure_does_not_mask_the_original_error(
        self, test_db, monkeypatch
    ):
        transport = FakeTransport(mails=[load_gmail("signup_en.json")])
        pipeline = self._pipeline(test_db, transport)

        monkeypatch.setattr(
            pipeline.conversations,
            "record_outbound",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the commit failed")),
        )

        def refuse(draft_id):
            raise RuntimeError("Gmail refused the delete")

        transport.delete_draft = refuse

        summary = run(pipeline)

        # Still escalated, still counted, and the run did not abort.
        assert summary.forwarded == 1
        assert summary.errors == 1


class TestNoInboundMailIsEverSilentlyDropped:
    """The completed-action marker, at the level the failure actually happens.

    The inbound row commits before any side effect so a crash cannot draft
    twice. That means a row exists for a mail that has been triaged and
    nothing more, and if the next run treated that as done the mail would be
    written off having been neither answered nor escalated. These tests fail a
    message part-way and then poll again, which is the shape of every real
    version of this: a Gmail error on the draft, an aborted run, a notifier
    outage.
    """

    def _run_failing_then_recovering(self, test_db, fixture, triage, breaker):
        """Poll once with ``breaker`` sabotaging the message, then poll clean."""
        first, first_transport = build(
            test_db, [load_gmail(fixture)], classifier=FakeClassifier(default=triage)
        )
        breaker(first, first_transport)
        first_summary = run(first)

        second_notifier = RecordingNotifier()
        second, second_transport = build(
            test_db,
            [load_gmail(fixture)],
            classifier=FakeClassifier(default=triage),
            notifier=second_notifier,
        )
        second_summary = run(second)
        return (
            first_summary,
            second_summary,
            second_transport,
            second_notifier,
        )

    def test_a_safeguarding_mail_whose_forward_failed_is_retried(self, test_db):
        # The worst case, stated plainly: the notifier raised, so nobody was
        # told. The mail must come back on the next poll.
        def break_notifier(pipeline, _transport):
            def explode(event):
                raise RuntimeError("SMTP refused the forward")

            pipeline.notifier = type("Broken", (), {"notify": staticmethod(explode)})()

        _first, second, _transport, notifier = self._run_failing_then_recovering(
            test_db,
            "safeguarding_vi.json",
            signals(category="safeguarding_legal", language="vi", confidence=0.91),
            break_notifier,
        )

        assert second.forwarded == 1
        assert notifier.categories == ["safeguarding_legal"]

    def test_a_mail_whose_draft_failed_is_escalated_and_then_left_alone(self, test_db):
        # A draft failure is recovered inside the same run: the reply could not
        # be delivered, so a person is told instead. That *is* a terminal
        # state, so the mail is marked handled and the next run must not
        # forward it a second time. Retrying is for mail nobody heard about.
        def break_drafting(pipeline, transport):
            def refuse(thread_id, mime):
                raise RuntimeError("Gmail refused drafts.create")

            transport.create_draft = refuse

        first, second, _transport, notifier = self._run_failing_then_recovering(
            test_db,
            "signup_en.json",
            signals(category="signup", confidence=0.94),
            break_drafting,
        )

        assert first.forwarded == 1
        assert second.forwarded == 0
        assert notifier.events == []
        assert all(row.handled_at is not None for row in inbound(test_db))

    def test_a_handled_mail_is_not_retried(self, test_db):
        # The other half of the property: a mail that genuinely finished must
        # not be answered twice.
        triage = signals(category="signup", confidence=0.94)
        first, _ = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(default=triage),
        )
        assert run(first).drafted == 1

        second, second_transport = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(default=triage),
        )
        second_summary = run(second)

        assert second_summary.drafted == 0
        assert second_transport.drafts == {}
        assert second_transport.labels_for("18f2a1b4c5d6e7f0") == [LABEL_SEEN]

    def test_the_row_is_marked_only_once_the_mail_is_finished(self, test_db):
        pipeline, _ = build(
            test_db,
            [load_gmail("signup_en.json")],
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
        )
        run(pipeline)

        row = inbound(test_db)[0]
        assert row.handled_at is not None

    def test_an_unfinished_mail_leaves_the_marker_unset(self, test_db):
        def break_drafting(pipeline, transport):
            def refuse(thread_id, mime):
                raise RuntimeError("Gmail refused drafts.create")

            transport.create_draft = refuse

            def explode(event):
                raise RuntimeError("and the forward failed too")

            pipeline.notifier = type("Broken", (), {"notify": staticmethod(explode)})()

        pipeline, transport = build(
            test_db,
            [load_gmail("safeguarding_vi.json")],
            classifier=FakeClassifier(
                default=signals(category="safeguarding_legal", language="vi")
            ),
        )
        break_drafting(pipeline, transport)
        run(pipeline)

        # Neither answered nor escalated, so it is not finished with.
        row = inbound(test_db)[0]
        assert row.handled_at is None

    @pytest.mark.parametrize(
        "fixture,triage",
        [
            ("newsletter.json", signals(category="automated", confidence=0.97)),
            ("signup_en.json", signals(category="signup", confidence=0.94)),
            ("faq_en.json", signals(category="faq", confidence=0.86)),
            (
                "sponsorship_en.json",
                signals(category="sponsorship", confidence=0.82, money=True),
            ),
        ],
    )
    def test_every_terminal_outcome_marks_the_mail_handled(
        self, test_db, fixture, triage
    ):
        # Every branch out of _handle, so a new one cannot be added without
        # either marking the mail or failing here.
        pipeline, _ = build(
            test_db, [load_gmail(fixture)], classifier=FakeClassifier(default=triage)
        )
        run(pipeline)

        rows = inbound(test_db)
        assert rows, fixture
        assert all(row.handled_at is not None for row in rows), fixture

    def test_a_paused_thread_is_marked_handled(self, test_db):
        thread = load_gmail("thread_with_human_reply.json")
        pipeline, _ = build(
            test_db,
            [load_gmail("second_inbound.json")],
            threads={thread["id"]: thread["messages"]},
        )
        run(pipeline)

        assert all(row.handled_at is not None for row in inbound(test_db))

    def test_a_circuit_breaker_abort_leaves_the_rest_of_the_mail_untouched(
        self, test_db
    ):
        # The abort case. A classifier outage still reaches a person, through
        # the fail-closed path, so the messages that got that far are handled.
        # What matters is the mail the breaker stopped short of: it must have
        # no row and no label at all, so the next run picks it up.
        mails = [
            payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
            for index in range(6)
        ]
        pipeline, transport = build(
            test_db,
            mails,
            classifier=FakeClassifier(default=TriageUnavailable("everything is down")),
        )
        summary = run(pipeline)

        assert summary.aborted_reason is not None

        recorded = {row.provider_message_id for row in inbound(test_db)}
        touched = {message_id for message_id, _ in transport.applied}
        untouched = {f"m-{index}" for index in range(6)} - recorded - touched
        assert untouched, "the breaker fired without leaving anything for the next run"

        # And every mail that was recorded was genuinely finished with: the
        # fail-closed decision was escalated, so somebody was told.
        assert all(row.handled_at is not None for row in inbound(test_db))

    def test_a_mail_the_breaker_stopped_short_of_is_processed_next_run(self, test_db):
        mails = [
            payloads.message(message_id=f"m-{index}", thread_id=f"t-{index}")
            for index in range(6)
        ]
        first, _ = build(
            test_db,
            mails,
            classifier=FakeClassifier(default=TriageUnavailable("everything is down")),
        )
        run(first)
        recorded_after_first = {row.provider_message_id for row in inbound(test_db)}

        second, _ = build(
            test_db,
            mails,
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
        )
        second_summary = run(second)

        # The leftovers are answered, and the two already finished with are
        # not answered again. Asserted on `drafted` rather than `processed`:
        # the fake transport hands back every mail, whereas real Gmail excludes
        # anything carrying VH-Bot/Seen, so `processed` counts the relabel
        # no-ops too.
        expected = len(mails) - len(recorded_after_first)
        assert second_summary.drafted == expected
        assert all(row.handled_at is not None for row in inbound(test_db))


class TestAutoMode:
    """What actually goes out once EMAIL_BOT_MODE is auto.

    The transport gains send_reply in this phase, so for the first time the
    difference between draft and auto is observable. These tests are the
    record of what that difference is.
    """

    def _auto(
        self,
        test_db,
        fixture,
        triage,
        answer=None,
        auto_languages=frozenset({"en", "vi"}),
        caps=None,
        send_fails=False,
    ):
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import DraftSink, SendSink

        transport = SendingFakeTransport(
            mails=[load_gmail(fixture)], send_fails=send_fails
        )
        adapter = GmailAdapter(transport)
        notifier = RecordingNotifier()
        settings = bot_settings(mode="auto", auto_languages=auto_languages)
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=adapter,
            classifier=FakeClassifier(default=triage),
            notifier=notifier,
            settings=settings,
            bot_service=FakeBotService(answer) if answer else FakeBotService(),
            sinks={
                "draft": DraftSink(adapter),
                "send": SendSink(
                    adapter, caps or CapsState(daily_cap=30, per_sender_cap=2)
                ),
            },
        )
        return pipeline, transport, notifier

    def test_a_signup_reply_is_sent(self, test_db):
        pipeline, transport, _ = self._auto(
            test_db, "signup_en.json", signals(category="signup", confidence=0.94)
        )
        summary = run(pipeline)

        assert summary.sent == 1
        assert summary.drafted == 0
        assert len(transport.sent) == 1
        assert LABEL_SENT in transport.labels_for("18f2a1b4c5d6e7f0")

    def test_a_confident_faq_answer_is_sent(self, test_db):
        pipeline, transport, _ = self._auto(
            test_db,
            "faq_en.json",
            signals(category="faq", confidence=0.86),
            answer={"response": "No certificate is needed.", "confidence": 0.82},
        )
        summary = run(pipeline)

        assert summary.sent == 1
        assert "No certificate is needed." in transport.sent_bodies()[0]

    def test_an_faq_below_the_threshold_sends_the_holding_message_and_escalates(
        self, test_db
    ):
        # The sender does get mail, and it is the holding message rather than
        # the answer the model produced.
        pipeline, transport, notifier = self._auto(
            test_db,
            "faq_en.json",
            signals(category="faq", confidence=0.86),
            answer={"response": "A guess.", "confidence": 0.2},
        )
        summary = run(pipeline)

        assert summary.sent == 1
        assert summary.forwarded == 1
        body = transport.sent_bodies()[0]
        assert "better answered by a person" in body
        assert "A guess." not in body
        assert len(notifier.events) == 1

    def test_an_executive_mail_sends_the_holding_message_and_escalates(self, test_db):
        pipeline, transport, notifier = self._auto(
            test_db,
            "safeguarding_vi.json",
            signals(category="safeguarding_legal", language="vi", confidence=0.91),
        )
        summary = run(pipeline)

        assert summary.sent == 1
        assert summary.forwarded == 1
        assert "trợ lý tự động" in transport.sent_bodies()[0]
        assert notifier.events[0].decision.tier == "needs_executive"

    def test_an_automated_mail_is_skipped_with_nothing_sent(self, test_db):
        # The one outcome where auto and off are identical. A holding message
        # here is how a two-machine loop starts.
        pipeline, transport, notifier = self._auto(
            test_db,
            "vacation_autoreply.json",
            signals(category="automated", confidence=0.2),
        )
        summary = run(pipeline)

        assert summary.sent == 0
        assert transport.sent == []
        assert notifier.events == []

    def test_a_second_inbound_on_a_replied_thread_gets_nothing_but_is_forwarded(
        self, test_db
    ):
        triage = signals(category="faq", confidence=0.86)
        first, first_transport, _ = self._auto(test_db, "faq_en.json", triage)
        assert run(first).sent == 1

        second, second_transport, notifier = self._auto(
            test_db, "second_inbound.json", triage
        )
        summary = run(second)

        assert summary.sent == 0
        assert second_transport.sent == []
        assert summary.forwarded == 1
        assert len(notifier.events) == 1

    def test_a_send_failure_leaves_the_thread_unpaused_and_unlabelled_sent(
        self, test_db
    ):
        # No retry loop: the run records it and a person picks it up. Labelling
        # it Sent would claim something that did not happen.
        pipeline, transport, notifier = self._auto(
            test_db,
            "signup_en.json",
            signals(category="signup", confidence=0.94),
            send_fails=True,
        )
        summary = run(pipeline)

        assert summary.sent == 0
        assert LABEL_SENT not in transport.labels_for("18f2a1b4c5d6e7f0")
        # The mail still reaches a person rather than being lost.
        assert summary.forwarded == 1

    def test_a_language_outside_the_auto_list_is_drafted_not_sent(self, test_db):
        """Review Focus item 4."""
        pipeline, transport, _ = self._auto(
            test_db,
            "signup_vi.json",
            signals(category="signup", language="vi", confidence=0.89),
            auto_languages=frozenset({"en"}),
        )
        summary = run(pipeline)

        assert summary.sent == 0
        assert summary.drafted == 1
        assert transport.sent == []
        assert LABEL_DRAFTED in transport.labels_for("18f2a1b4c5d6e801")

    def test_a_reached_cap_drafts_rather_than_dropping_the_reply(self, test_db):
        from app.services.email_bot.caps import CapsState

        # A capacity limit must not become lost mail: the captain still has the
        # reply one click away.
        exhausted = CapsState(daily_cap=1, per_sender_cap=2, sent_today=1)
        pipeline, transport, _ = self._auto(
            test_db,
            "signup_en.json",
            signals(category="signup", confidence=0.94),
            caps=exhausted,
        )
        summary = run(pipeline)

        assert summary.sent == 0
        assert summary.drafted == 1
        assert transport.sent == []

    def test_the_sent_message_id_is_recorded(self, test_db):
        # So the next run's "never talk over a human" check can tell the bot's
        # own reply apart from one the captain typed.
        pipeline, _transport, _ = self._auto(
            test_db, "signup_en.json", signals(category="signup", confidence=0.94)
        )
        run(pipeline)

        row = [r for r in outbound(test_db) if r.action == "sent"][0]
        assert row.gmail_message_id_out


class TestOffAndDraftStillCannotSend:
    """The controls E1 established, re-asserted now that sending exists.

    A transport whose send_reply raises, so a send is not merely unasserted but
    impossible to perform silently.
    """

    def _with_exploding_send(self, test_db, mode):
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import DraftSink, SendSink

        transport = SendingFakeTransport(
            mails=[load_gmail("signup_en.json")], send_fails=True, raise_on_send=True
        )
        adapter = GmailAdapter(transport)
        return EmailBotPipeline(
            db=test_db,
            adapter=adapter,
            classifier=FakeClassifier(
                default=signals(category="signup", confidence=0.94)
            ),
            notifier=RecordingNotifier(),
            settings=bot_settings(mode=mode),
            bot_service=FakeBotService(),
            sinks={
                "draft": DraftSink(adapter),
                "send": SendSink(adapter, CapsState()),
            },
        )

    def test_draft_mode_never_reaches_the_send_sink(self, test_db):
        summary = run(self._with_exploding_send(test_db, "draft"))
        assert summary.sent == 0
        assert summary.drafted == 1

    def test_off_mode_sends_and_drafts_nothing(self, test_db):
        summary = run(self._with_exploding_send(test_db, "off"))
        assert summary.aborted_reason == "mode is off"
        assert summary.sent == 0
        assert summary.drafted == 0
