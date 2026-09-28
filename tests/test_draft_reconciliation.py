"""What happened to each draft the bot left behind.

This is the measurement the whole evaluation gate turns on: at least 90 percent
of sign-up drafts and 80 percent of FAQ drafts sent unchanged over two weeks is
what licences automatic sending. So the classification has to be right about
real Gmail behaviour, and the interesting cases are all about text that came
back looking different without having been edited.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.channels.gmail_transport import RawMail
from app.services.email_bot.reconciliation import (
    normalise,
    reconcile_draft,
    strip_quoted_reply,
    texts_match,
)
from tests.fixtures import gmail_payloads as payloads

DRAFTED = (
    "Thank you very much for your message and interest in volunteering with "
    "Vietnam Hearts\n\nWe teach in HCM Tuesdays and Thursdays from "
    "9:30-10:30am.\n\n- Vietnam Hearts automated assistant"
)


def sent_from_inbox(text: str, message_id: str = "sent-1") -> RawMail:
    return RawMail.from_resource(
        payloads.message(
            message_id=message_id,
            from_address=f"Vietnam Hearts <{payloads.TEST_INBOX}>",
            to_address=payloads.TEST_SENDER,
            text=text,
            label_ids=("SENT",),
        )
    )


def inbound(message_id: str = "inbound-1") -> RawMail:
    return RawMail.from_resource(
        payloads.message(message_id=message_id, text="How do I sign up?")
    )


def reconcile(**overrides):
    kwargs = {
        "draft_still_exists": False,
        "drafted_text": DRAFTED,
        "thread_messages": [],
        "inbox_address": payloads.TEST_INBOX,
        "bot_message_ids": set(),
    }
    kwargs.update(overrides)
    return reconcile_draft(**kwargs)


class TestTheFourOutcomes:
    def test_a_draft_that_is_still_there_stays_pending(self):
        result = reconcile(draft_still_exists=True)
        assert result.outcome == "pending"

    def test_gone_with_an_identical_message_is_sent_unchanged(self):
        result = reconcile(thread_messages=[inbound(), sent_from_inbox(DRAFTED)])
        assert result.outcome == "sent_unchanged"
        assert result.sent_message_id == "sent-1"

    def test_gone_with_a_different_message_is_sent_edited(self):
        rewritten = "Hi! Thanks for getting in touch. Let me explain how it works."
        result = reconcile(thread_messages=[inbound(), sent_from_inbox(rewritten)])
        assert result.outcome == "sent_edited"
        assert result.sent_message_id == "sent-1"

    def test_gone_with_no_message_at_all_is_deleted(self):
        result = reconcile(thread_messages=[inbound()])
        assert result.outcome == "deleted"
        assert result.sent_message_id is None

    def test_an_empty_thread_reads_as_deleted(self):
        assert reconcile(thread_messages=[]).outcome == "deleted"


class TestOnlyTheInboxCounts:
    def test_the_senders_own_reply_is_not_a_send(self):
        # Otherwise every sender who wrote back would score the bot's draft as
        # having been sent.
        result = reconcile(thread_messages=[inbound(), inbound("inbound-2")])
        assert result.outcome == "deleted"

    def test_a_message_the_bot_itself_sent_is_not_a_send(self):
        # In auto mode the bot's own reply is in the thread from the inbox
        # address, and counting it would score every automatic send as an
        # accepted draft.
        bot_reply = sent_from_inbox(DRAFTED, "bot-sent-1")
        result = reconcile(
            thread_messages=[inbound(), bot_reply],
            bot_message_ids={"bot-sent-1"},
        )
        assert result.outcome == "deleted"

    def test_the_newest_inbox_message_is_the_one_compared(self):
        # The captain sent the draft, then followed up with a second note.
        result = reconcile(
            thread_messages=[
                inbound(),
                sent_from_inbox(DRAFTED, "sent-1"),
                sent_from_inbox("One more thing.", "sent-2"),
            ]
        )
        assert result.outcome == "sent_edited"
        assert result.sent_message_id == "sent-2"

    def test_a_display_name_does_not_prevent_the_match(self):
        result = reconcile(thread_messages=[sent_from_inbox(DRAFTED)])
        assert result.outcome == "sent_unchanged"


class TestTextComparisonToleratesRealGmailBehaviour:
    def test_different_line_wrapping_is_not_an_edit(self):
        rewrapped = DRAFTED.replace("\n\n", "\n \n").replace(" ", "  ")
        assert texts_match(DRAFTED, rewrapped)

    def test_trailing_whitespace_is_not_an_edit(self):
        assert texts_match(DRAFTED, DRAFTED + "\n\n   \n")

    def test_a_quoted_original_appended_by_gmail_is_not_an_edit(self):
        # The case that would otherwise score every single sent draft as
        # edited.
        with_quote = (
            f"{DRAFTED}\n\n"
            "On Mon, 29 Sep 2026 at 09:12, A Person <sender@example.com> wrote:\n"
            "> Hello,\n"
            "> I would like to volunteer. How do I sign up?\n"
        )
        assert texts_match(DRAFTED, with_quote)

    def test_a_vietnamese_quote_attribution_is_also_stripped(self):
        with_quote = (
            f"{DRAFTED}\n\n"
            "Vào Th 2, 29 thg 9, 2026 lúc 09:12 A Person <sender@example.com> đã viết:\n"
            "> Xin chào\n"
        )
        assert texts_match(DRAFTED, with_quote)

    def test_the_captain_adding_a_line_still_counts_as_unchanged(self):
        # Common, and treating it as a rejection would understate acceptance
        # badly: the bot's text went out intact.
        assert texts_match(DRAFTED, f"{DRAFTED}\n\nSee you Tuesday!")

    def test_rewriting_the_opening_is_an_edit(self):
        # The prefix no longer matches, which is exactly the case that should
        # be scored as an edit.
        rewritten = "Hi there! " + DRAFTED
        assert not texts_match(DRAFTED, rewritten)

    def test_a_completely_different_reply_is_an_edit(self):
        assert not texts_match(DRAFTED, "Sorry, we are full at the moment.")

    def test_an_empty_sent_body_is_not_a_match(self):
        assert not texts_match(DRAFTED, "")

    def test_an_empty_draft_never_matches(self):
        # Otherwise a draft whose text was lost would match everything.
        assert not texts_match("", "anything at all")

    def test_case_differences_are_not_edits(self):
        assert texts_match(DRAFTED, DRAFTED.upper())


class TestStripQuotedReply:
    def test_the_english_attribution_line_bounds_the_body(self):
        text = (
            "My reply.\n\nOn Mon, 1 Jan 2026 at 10:00, X <x@example.com> wrote:\n> old"
        )
        assert strip_quoted_reply(text).strip() == "My reply."

    def test_the_original_message_separator_bounds_the_body(self):
        text = "My reply.\n\n----- Original Message -----\nold text"
        assert strip_quoted_reply(text).strip() == "My reply."

    def test_the_underscore_separator_bounds_the_body(self):
        text = "My reply.\n\n________________________________\nold text"
        assert strip_quoted_reply(text).strip() == "My reply."

    def test_the_earliest_marker_wins(self):
        # A round-tripped thread carries several, and only the first bounds
        # what this author typed.
        text = (
            "My reply.\n\n"
            "On Mon, 1 Jan 2026, X wrote:\n"
            "> older\n\n"
            "----- Original Message -----\n"
            "oldest"
        )
        assert strip_quoted_reply(text).strip() == "My reply."

    def test_loose_quoted_lines_are_dropped(self):
        text = "My reply.\n> a quoted line\nstill mine."
        stripped = strip_quoted_reply(text)
        assert "a quoted line" not in stripped
        assert "still mine." in stripped

    def test_text_with_no_quote_is_unchanged(self):
        assert strip_quoted_reply("Just a reply.").strip() == "Just a reply."

    def test_empty_input_is_empty(self):
        assert strip_quoted_reply("") == ""


class TestNormalise:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("  Hello   world  ", "hello world"),
            ("Hello\nworld", "hello world"),
            ("Hello\n\n\nworld", "hello world"),
            ("HELLO WORLD", "hello world"),
            ("", ""),
        ],
    )
    def test_normalisation(self, text, expected):
        assert normalise(text) == expected

    def test_vietnamese_diacritics_survive(self):
        assert "tình nguyện" in normalise("Tình  nguyện\nviên")


class TestThePipelineReconcilesEachRun:
    """Reconciliation runs before listing, and a failure never guesses.

    The acceptance rate this produces is what decides whether automatic
    sending is ever turned on, so an unknown outcome has to stay unknown. A
    draft whose thread cannot be read must come back next run rather than be
    recorded as deleted.
    """

    def _drafted(self, test_db, transport, draft_id="draft-1"):
        """A conversation with one pending draft, as a poll would leave it."""
        from app.services.conversation_service import ConversationService

        service = ConversationService(test_db)
        conversation = service.get_or_create("email", "18f2a1b4c5d6e7f0", "hash-a")
        row = service.record_outbound(
            conversation,
            text=DRAFTED,
            action="drafted",
            language="en",
            gmail_draft_id=draft_id,
            kind="signup",
        )
        return service, conversation, row

    def _pipeline(self, test_db, transport):
        from app.services.channels.gmail import GmailAdapter
        from app.services.email_bot.pipeline import EmailBotPipeline
        from app.services.email_bot.settings import EmailBotSettings
        from tests.fixtures.email_bot import (
            FakeBotService,
            FakeClassifier,
            RecordingNotifier,
            signals,
        )

        return EmailBotPipeline(
            db=test_db,
            adapter=GmailAdapter(transport),
            classifier=FakeClassifier(default=signals()),
            notifier=RecordingNotifier(),
            settings=EmailBotSettings(
                mode="draft",
                escalation_owner_email="owner@example.com",
                signup_form_link="https://example.com/form",
            ),
            bot_service=FakeBotService(),
        )

    def _run(self, pipeline):
        import anyio

        async def drive():
            return await anyio.to_thread.run_sync(pipeline.run)

        return anyio.run(drive)

    def test_a_sent_draft_is_resolved_as_sent_unchanged(self, test_db):
        from tests.fixtures.email_bot import FakeTransport

        transport = FakeTransport(
            mails=[], threads={"18f2a1b4c5d6e7f0": [sent_from_inbox(DRAFTED).payload]}
        )
        transport.get_draft = lambda draft_id: None  # the captain sent it
        _service, _conversation, row = self._drafted(test_db, transport)

        summary = self._run(self._pipeline(test_db, transport))

        test_db.refresh(row)
        assert row.draft_outcome == "sent_unchanged"
        assert summary.reconciled == 1

    def test_a_deleted_draft_is_resolved_as_deleted(self, test_db):
        from tests.fixtures.email_bot import FakeTransport

        transport = FakeTransport(mails=[], threads={"18f2a1b4c5d6e7f0": []})
        transport.get_draft = lambda draft_id: None
        _service, _conversation, row = self._drafted(test_db, transport)

        self._run(self._pipeline(test_db, transport))

        test_db.refresh(row)
        assert row.draft_outcome == "deleted"

    def test_a_draft_still_in_the_thread_stays_pending(self, test_db):
        from tests.fixtures.email_bot import FakeTransport

        transport = FakeTransport(mails=[], threads={"18f2a1b4c5d6e7f0": []})
        transport.get_draft = lambda draft_id: object()  # still there
        _service, _conversation, row = self._drafted(test_db, transport)

        summary = self._run(self._pipeline(test_db, transport))

        test_db.refresh(row)
        assert row.draft_outcome == "pending"
        assert summary.reconciled == 0

    def test_an_unreadable_thread_leaves_the_outcome_unknown(self, test_db):
        # The important negative: guessing would corrupt the metric the gate
        # reads, so the draft comes back next run instead.
        from tests.fixtures.email_bot import FakeTransport

        transport = FakeTransport(mails=[], threads={})
        transport.get_draft = lambda draft_id: None

        def refuse(thread_id):
            raise RuntimeError("Gmail refused threads.get")

        transport.get_thread = refuse
        _service, _conversation, row = self._drafted(test_db, transport)

        summary = self._run(self._pipeline(test_db, transport))

        test_db.refresh(row)
        assert row.draft_outcome == "pending"
        assert summary.errors == 1
        assert summary.aborted_reason is None

    def test_the_sent_message_id_is_kept(self, test_db):
        # So the next run's "never talk over a human" check can tell the
        # captain's send of a bot draft apart from a reply he typed himself.
        from tests.fixtures.email_bot import FakeTransport

        transport = FakeTransport(
            mails=[],
            threads={"18f2a1b4c5d6e7f0": [sent_from_inbox(DRAFTED, "sent-77").payload]},
        )
        transport.get_draft = lambda draft_id: None
        _service, _conversation, row = self._drafted(test_db, transport)

        self._run(self._pipeline(test_db, transport))

        test_db.refresh(row)
        assert row.gmail_message_id_out == "sent-77"

    def test_reconciliation_happens_before_listing(self, test_db):
        from tests.fixtures.email_bot import FakeTransport

        order = []

        class Ordered(FakeTransport):
            def get_draft(self, draft_id):
                order.append("reconcile")
                return None

            def list_unprocessed(self, **kwargs):
                order.append("list")
                return []

        transport = Ordered(mails=[], threads={"18f2a1b4c5d6e7f0": []})
        self._drafted(test_db, transport)

        self._run(self._pipeline(test_db, transport))

        # A busy run must not be the one that skips the measurement.
        assert order == ["reconcile", "list"]
