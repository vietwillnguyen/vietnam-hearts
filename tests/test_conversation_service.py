"""The conversation store: dedupe, the reply cap, and the pause state machine.

Three safety controls live here, and all three are statements about persisted
state, so they are tested against a real session rather than a mock. A control
that only holds while one function runs is not a control.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.channels.base import EMAIL_CHANNEL, IncomingMessage
from app.services.conversation_service import (
    ACTION_DRAFTED,
    ACTION_FORWARDED,
    ACTION_PAUSED,
    ACTION_SENT,
    ACTION_SKIPPED,
    DRAFT_PENDING,
    MAX_BOT_REPLIES_PER_THREAD,
    STATUS_BOT,
    STATUS_PAUSED_HANDOFF,
    STATUS_PAUSED_MANUAL,
    ConversationService,
)
from app.services.triage.policy import decide
from tests.fixtures.email_bot import signals


@pytest.fixture
def service(test_db):
    return ConversationService(test_db)


def message(
    message_id: str = "msg-1", thread: str = "thread-1", sender: str = "hash-a"
) -> IncomingMessage:
    return IncomingMessage(
        channel=EMAIL_CHANNEL,
        thread_key=thread,
        provider_message_id=message_id,
        sender_key=sender,
        sender_address="sender@example.com",
        subject="Volunteering",
        text="How do I sign up?",
        rfc_message_id=f"<{message_id}@x>",
    )


def decision(category: str = "signup", **kwargs):
    return decide(signals(category=category, **kwargs), 0.6)


class TestDedupe:
    def test_an_unseen_id_is_not_a_duplicate(self, service):
        assert not service.is_duplicate("msg-1")

    def test_a_recorded_id_is_a_duplicate(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_inbound(conversation, message(), decision())
        assert service.is_duplicate("msg-1")

    def test_an_empty_id_is_never_a_duplicate(self, service):
        # Outbound rows carry no provider id, so an empty value must not make
        # every one of them look like a duplicate of the others.
        assert not service.is_duplicate("")

    def test_the_unique_index_still_refuses_a_raw_second_insert(self, service, test_db):
        from sqlalchemy.exc import IntegrityError

        from app.models import Message as MessageModel

        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_inbound(conversation, message(), decision())

        # The idempotence guarantee is the database's, not the pipeline's.
        test_db.add(
            MessageModel(
                conversation_id=conversation.id,
                direction="inbound",
                provider_message_id="msg-1",
                action="labelled",
            )
        )
        with pytest.raises(IntegrityError):
            test_db.commit()
        test_db.rollback()

    def test_recording_the_same_mail_again_updates_the_row_in_place(self, service):
        # A retry of an unhandled mail has to reuse its row: inserting would
        # hit the unique index, and the retry's triage is the more recent of
        # the two answers anyway.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        first = service.record_inbound(conversation, message(), decision("faq"))
        second = service.record_inbound(conversation, message(), decision("donation"))

        from app.models import Message as MessageModel

        assert first.id == second.id
        assert second.category == "donation"
        inbound_rows = (
            service.db.query(MessageModel)
            .filter(MessageModel.direction == "inbound")
            .all()
        )
        assert len(inbound_rows) == 1

    def test_several_outbound_rows_coexist(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation, text="a", action=ACTION_FORWARDED, language="en"
        )
        service.record_outbound(
            conversation, text="b", action=ACTION_FORWARDED, language="en"
        )
        assert len(conversation.messages) == 2


class TestGetOrCreate:
    def test_the_same_thread_returns_the_same_conversation(self, service):
        first = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        second = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        assert first.id == second.id

    def test_a_new_conversation_starts_open_to_the_bot(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        assert conversation.status == STATUS_BOT
        assert conversation.bot_reply_count == 0

    def test_the_same_thread_key_on_another_channel_is_a_different_conversation(
        self, service
    ):
        email = service.get_or_create(EMAIL_CHANNEL, "shared-id", "hash-a")
        messenger = service.get_or_create("messenger", "shared-id", "hash-a")
        assert email.id != messenger.id


class TestTheOneReplyCap:
    def test_the_bot_may_reply_to_a_fresh_thread(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        assert service.can_bot_reply(conversation)

    def test_the_bot_may_not_reply_twice(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation, text="reply", action=ACTION_DRAFTED, language="en"
        )
        assert conversation.bot_reply_count == MAX_BOT_REPLIES_PER_THREAD
        assert not service.can_bot_reply(conversation)

    def test_a_send_also_consumes_the_reply(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation, text="reply", action=ACTION_SENT, language="en"
        )
        assert not service.can_bot_reply(conversation)

    def test_a_forward_does_not_consume_the_threads_one_reply(self, service):
        # The forward goes to the captain. Letting it consume the reply would
        # mean an escalated sender never got the holding message.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation, text="forward", action=ACTION_FORWARDED, language="en"
        )
        assert conversation.bot_reply_count == 0
        assert service.can_bot_reply(conversation)

    def test_a_paused_thread_is_closed_to_the_bot_even_with_no_reply_yet(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.pause_manual(conversation)
        assert not service.can_bot_reply(conversation)


class TestPauseStateMachine:
    def test_an_escalation_pauses_for_handoff(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.pause(conversation, "needs_executive", "executive category donation")

        assert conversation.status == STATUS_PAUSED_HANDOFF
        assert "donation" in conversation.pause_reason

    def test_bot_to_handoff_and_back_to_bot(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.pause(conversation, "needs_admin", "a person should answer")
        service.resume(conversation)

        assert conversation.status == STATUS_BOT
        assert conversation.pause_reason is None

    def test_bot_to_manual_and_back_to_bot(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.pause_manual(conversation)
        assert conversation.status == STATUS_PAUSED_MANUAL

        service.resume(conversation)
        assert conversation.status == STATUS_BOT

    def test_only_resume_undoes_a_manual_pause(self, service):
        # An escalation arriving after a human reply must not downgrade the
        # stronger state into one a later escalation could clear.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.pause_manual(conversation)
        service.pause(conversation, "needs_admin", "something else")

        assert conversation.status == STATUS_PAUSED_MANUAL

    def test_resuming_does_not_reset_the_reply_counter(self, service):
        # Resuming means "the bot may act again", not "pretend it never
        # replied". Clearing the counter would turn the one-reply cap into one
        # reply per resume.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation, text="reply", action=ACTION_DRAFTED, language="en"
        )
        service.pause_manual(conversation)
        service.resume(conversation)

        assert conversation.bot_reply_count == 1
        assert not service.can_bot_reply(conversation)


class TestOutstandingDraft:
    def test_a_pending_draft_is_returned(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation,
            text="reply",
            action=ACTION_DRAFTED,
            language="en",
            gmail_draft_id="draft-1",
        )
        outstanding = service.outstanding_draft(conversation)
        assert outstanding is not None
        assert outstanding.gmail_draft_id == "draft-1"
        assert outstanding.draft_outcome == DRAFT_PENDING

    def test_the_newest_pending_draft_wins(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        for draft_id in ("draft-1", "draft-2"):
            service.record_outbound(
                conversation,
                text="reply",
                action=ACTION_DRAFTED,
                language="en",
                gmail_draft_id=draft_id,
            )
        assert service.outstanding_draft(conversation).gmail_draft_id == "draft-2"

    def test_a_resolved_draft_is_not_outstanding(self, service, test_db):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_outbound(
            conversation,
            text="reply",
            action=ACTION_DRAFTED,
            language="en",
            gmail_draft_id="draft-1",
        )
        row.draft_outcome = "sent_unchanged"
        test_db.commit()

        assert service.outstanding_draft(conversation) is None

    def test_a_sent_reply_is_not_an_outstanding_draft(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation,
            text="reply",
            action=ACTION_SENT,
            language="en",
            gmail_message_id_out="sent-1",
        )
        assert service.outstanding_draft(conversation) is None

    def test_no_drafts_means_none(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        assert service.outstanding_draft(conversation) is None


class TestAuditRows:
    def test_an_inbound_row_never_stores_the_body(self, service):
        # The inbound body stays in Gmail. The row carries ids and triage
        # metadata, which is everything an investigation needs without the
        # database becoming a second copy of the inbox.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_inbound(conversation, message(), decision())

        assert row.text is None
        assert row.provider_message_id == "msg-1"
        assert row.category == "signup"
        assert row.tier == "auto_answer"

    def test_the_shadow_decision_is_recorded_as_json(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        shadow = signals(category="faq", confidence=0.42, classifier="litellm:gemini")
        row = service.record_inbound(conversation, message(), decision(), shadow)

        assert row.triage_shadow["category"] == "faq"
        assert row.triage_shadow["classifier"] == "litellm:gemini"
        assert "@" not in str(row.triage_shadow)

    def test_no_shadow_records_null(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_inbound(conversation, message(), decision(), None)
        assert row.triage_shadow is None

    def test_the_conversation_remembers_its_last_outcome(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_inbound(conversation, message(), decision("donation"))

        assert conversation.last_category == "donation"
        assert conversation.last_tier == "needs_executive"

    def test_record_action_writes_the_idempotence_key_without_a_decision(self, service):
        # Guard skips have no decision to record, but the key must still be
        # written or the next run would process the mail again.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_action(conversation, message(), ACTION_SKIPPED)

        assert row.action == ACTION_SKIPPED
        assert row.category is None
        assert service.is_duplicate("msg-1")

    def test_outbound_text_is_stored(self, service):
        # Unlike inbound: it is the organisation's own words, the captain needs
        # to see what was offered on his behalf, and E2 compares it against
        # what was eventually sent.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_outbound(
            conversation,
            text="Thanks for writing.",
            action=ACTION_DRAFTED,
            language="vi",
            gmail_draft_id="draft-1",
            confidence=0.8,
            sources=["kb"],
            kind="signup",
        )

        assert row.text == "Thanks for writing."
        assert row.language == "vi"
        assert row.sources == ["kb"]
        assert row.category == "signup"


class TestBotSentMessageIds:
    def test_only_ids_the_bot_recorded_are_returned(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation,
            text="reply",
            action=ACTION_SENT,
            language="en",
            gmail_message_id_out="sent-1",
        )
        service.record_outbound(
            conversation,
            text="draft",
            action=ACTION_DRAFTED,
            language="en",
            gmail_draft_id="draft-1",
        )
        # A draft has no sent id, so it contributes nothing to the human-reply
        # comparison.
        assert service.bot_sent_message_ids(conversation) == {"sent-1"}

    def test_a_fresh_conversation_has_none(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        assert service.bot_sent_message_ids(conversation) == set()


class TestEscalationsListing:
    def test_both_pause_states_are_listed(self, service):
        handed_off = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.pause(handed_off, "needs_admin", "a person should answer")
        manual = service.get_or_create(EMAIL_CHANNEL, "thread-2", "hash-b")
        service.pause_manual(manual)

        listed = {row.thread_key for row in service.escalations()}
        assert listed == {"thread-1", "thread-2"}

    def test_a_resumed_thread_drops_off_the_list(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.pause(conversation, "needs_admin", "a person should answer")
        service.resume(conversation)

        assert service.escalations() == []

    def test_an_active_thread_is_not_an_escalation(self, service):
        service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        assert service.escalations() == []


class TestDraftAcceptance:
    def test_outcomes_are_grouped_by_the_answer_path(self, service, test_db):
        # The E2 gate is stated per kind (90 percent of sign-up drafts, 80
        # percent of FAQ drafts), so an aggregate would not answer it.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        outcomes = (
            ("signup", "sent_unchanged"),
            ("signup", "sent_unchanged"),
            ("signup", "deleted"),
            ("faq", "sent_edited"),
        )
        for index, (kind, outcome) in enumerate(outcomes):
            row = service.record_outbound(
                conversation,
                text="reply",
                action=ACTION_DRAFTED,
                language="en",
                gmail_draft_id=f"draft-{index}",
                kind=kind,
            )
            row.draft_outcome = outcome
            test_db.commit()

        summary = service.draft_acceptance()
        assert summary["signup"] == {"sent_unchanged": 2, "deleted": 1}
        assert summary["faq"] == {"sent_edited": 1}

    def test_pending_drafts_are_reported_as_pending(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation,
            text="reply",
            action=ACTION_DRAFTED,
            language="en",
            gmail_draft_id="draft-1",
            kind="faq",
        )
        assert service.draft_acceptance()["faq"] == {DRAFT_PENDING: 1}

    def test_no_drafts_is_an_empty_summary(self, service):
        assert service.draft_acceptance() == {}


class TestPendingDraftRotation:
    def test_a_checked_draft_goes_to_the_back_of_the_queue(self, service):
        # Drafts nobody touches stay pending indefinitely. Taken oldest first
        # they would fill every batch and newer drafts would never be settled.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        for index in range(3):
            service.record_outbound(
                conversation,
                text="reply",
                action=ACTION_DRAFTED,
                language="en",
                gmail_draft_id=f"draft-{index}",
            )

        first = service.pending_drafts(limit=2)
        assert [row.gmail_draft_id for row in first] == ["draft-0", "draft-1"]
        for row in first:
            service.mark_draft_checked(row)

        second = service.pending_drafts(limit=2)
        assert [row.gmail_draft_id for row in second] == ["draft-2", "draft-0"]


class TestPausedActionIsAudited:
    def test_a_manual_pause_records_an_action_row(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_action(conversation, message(), ACTION_PAUSED)
        assert row.action == ACTION_PAUSED


class TestDedupeMeansHandledNotMerelySeen:
    """The silent-drop guard: no inbound mail is written off unfinished.

    The inbound row is committed before any side effect, so that a crash
    cannot produce a second draft. That makes "a row exists" true for a mail
    that has been triaged and nothing more. If that counted as done, any
    failure afterwards - a Gmail error on the draft, an aborted run, a thread
    that turned out to be paused - would leave the mail looking handled and the
    next run would just relabel it. For a safeguarding mail that means nobody
    is ever told.
    """

    def test_a_fresh_mail_is_neither_seen_nor_handled(self, service):
        assert not service.is_duplicate("msg-1")
        assert not service.is_handled("msg-1")

    def test_a_triaged_but_unfinished_mail_is_seen_and_not_handled(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_inbound(conversation, message(), decision())

        assert service.is_duplicate("msg-1")
        # The distinction the whole fix rests on.
        assert not service.is_handled("msg-1")

    def test_marking_handled_makes_it_handled(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_inbound(conversation, message(), decision())
        service.mark_handled(row)

        assert service.is_handled("msg-1")
        assert row.handled_at is not None

    def test_a_guard_skip_is_handled_once_marked(self, service):
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_action(conversation, message(), ACTION_SKIPPED)
        assert not service.is_handled("msg-1")

        service.mark_handled(row)
        assert service.is_handled("msg-1")

    def test_an_outbound_row_never_answers_the_dedupe_question(self, service):
        # Outbound rows carry no provider id, and an empty id must not make
        # every one of them look like the same handled mail.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        service.record_outbound(
            conversation, text="a reply", action=ACTION_DRAFTED, language="en"
        )
        assert not service.is_handled("")
        assert not service.is_duplicate("")

    def test_a_retry_cannot_produce_a_second_reply(self, service):
        # Why retrying an unhandled mail is safe: the reply cap lives on the
        # conversation row and is committed with the outbound reply, so the
        # retry can only supply the escalation the mail never got.
        conversation = service.get_or_create(EMAIL_CHANNEL, "thread-1", "hash-a")
        row = service.record_inbound(conversation, message(), decision())
        service.record_outbound(
            conversation, text="a reply", action=ACTION_DRAFTED, language="en"
        )

        assert not service.is_handled("msg-1")
        assert not service.can_bot_reply(conversation)
        assert row.handled_at is None
