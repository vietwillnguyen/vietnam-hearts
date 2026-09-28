"""The delivery mode gate.

One pure function decides whether anything is delivered at all, which is what
makes "nothing sends while the mode is off or draft" a property of a table rather
than a claim about every call site. In this phase there is no send sink at all,
so ``auto`` resolving to the draft sink is the correct behaviour rather than a
stub.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.channels.base import OutboundReply
from app.services.email_bot.delivery import (
    DeliveryMode,
    DeliveryResult,
    DraftSink,
    choose_sink,
    parse_mode,
)
from tests.fixtures.email_bot import FakeSendSink, FakeTransport, load_gmail
from tests.fixtures.logs import attached_caplog

EN_ONLY = frozenset({"en"})
BOTH = frozenset({"en", "vi"})


def reply(language: str = "en") -> OutboundReply:
    return OutboundReply(
        thread_key="thread-1",
        to_address="sender@example.com",
        subject="Volunteering",
        in_reply_to="<abc@x>",
        references=(),
        text="Thanks for writing.",
        language=language,
        kind="signup",
    )


@pytest.fixture
def adapter():
    from app.services.channels.gmail import GmailAdapter

    return GmailAdapter(FakeTransport(mails=[load_gmail("signup_en.json")]))


class TestParseMode:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("off", DeliveryMode.OFF),
            ("draft", DeliveryMode.DRAFT),
            ("auto", DeliveryMode.AUTO),
            ("  AUTO  ", DeliveryMode.AUTO),
            ("Draft", DeliveryMode.DRAFT),
        ],
    )
    def test_recognised_values(self, value, expected):
        assert parse_mode(value) is expected

    @pytest.mark.parametrize(
        "value", ["aut", "on", "true", "send", "", "   ", None, "OFF ON"]
    )
    def test_everything_unrecognised_reads_as_off(self, value):
        # Fail-closed parsing: a typo in the dashboard field must stop the bot,
        # never fall through to a mode that sends.
        assert parse_mode(value) is DeliveryMode.OFF

    def test_an_unknown_value_is_logged(self, caplog):
        with attached_caplog(caplog, "email_bot_delivery", level="WARNING"):
            parse_mode("aut")
        assert "aut" in caplog.text


class TestChooseSinkWithoutASendSink:
    def test_off_yields_no_sink(self, adapter):
        # The kill switch. The pipeline still labels and still escalates,
        # because knowing about a safeguarding mail is not something a delivery
        # switch should turn off. The sender simply gets nothing.
        assert (
            choose_sink(DeliveryMode.OFF, "en", EN_ONLY, {"draft": DraftSink(adapter)})
            is None
        )

    def test_draft_yields_the_draft_sink(self, adapter):
        draft = DraftSink(adapter)
        assert choose_sink(DeliveryMode.DRAFT, "en", EN_ONLY, {"draft": draft}) is draft

    def test_auto_falls_back_to_drafting(self, adapter):
        # A mapping assembled without a send sink must degrade to holding the
        # reply for review, not to delivering nothing.
        draft = DraftSink(adapter)
        assert choose_sink(DeliveryMode.AUTO, "en", EN_ONLY, {"draft": draft}) is draft

    @pytest.mark.parametrize("language", ["en", "vi", "other"])
    def test_no_language_reaches_a_send_sink_that_is_not_there(self, adapter, language):
        draft = DraftSink(adapter)
        for mode in (DeliveryMode.DRAFT, DeliveryMode.AUTO):
            assert choose_sink(mode, language, BOTH, {"draft": draft}) is draft


class TestChooseSinkWhenASendSinkExists:
    """Pins the contract E3 fills in, so its ``SendSink`` needs no new wiring."""

    def test_off_never_reaches_the_send_sink(self, adapter):
        send = FakeSendSink()
        assert (
            choose_sink(
                DeliveryMode.OFF,
                "en",
                EN_ONLY,
                {"draft": DraftSink(adapter), "send": send},
            )
            is None
        )

    def test_draft_never_reaches_the_send_sink(self, adapter):
        draft = DraftSink(adapter)
        send = FakeSendSink()
        assert (
            choose_sink(
                DeliveryMode.DRAFT, "en", EN_ONLY, {"draft": draft, "send": send}
            )
            is draft
        )

    def test_auto_reaches_the_send_sink_for_a_cleared_language(self, adapter):
        send = FakeSendSink()
        assert (
            choose_sink(
                DeliveryMode.AUTO,
                "en",
                EN_ONLY,
                {"draft": DraftSink(adapter), "send": send},
            )
            is send
        )

    def test_auto_drafts_a_language_that_is_not_cleared(self, adapter, caplog):
        # Review Focus item 4, pinned properly in E3. Vietnamese joins
        # EMAIL_BOT_AUTO_LANGUAGES only after native-speaker sign-off, so the
        # Vietnamese templates can ship as drafts first.
        draft = DraftSink(adapter)
        with attached_caplog(caplog, "email_bot_delivery", level="INFO"):
            chosen = choose_sink(
                DeliveryMode.AUTO,
                "vi",
                EN_ONLY,
                {"draft": draft, "send": FakeSendSink()},
            )
        assert chosen is draft
        assert "vi" in caplog.text

    def test_auto_sends_vietnamese_once_it_is_cleared(self, adapter):
        send = FakeSendSink()
        assert (
            choose_sink(
                DeliveryMode.AUTO,
                "vi",
                BOTH,
                {"draft": DraftSink(adapter), "send": send},
            )
            is send
        )

    def test_an_unknown_language_is_never_sent(self, adapter):
        draft = DraftSink(adapter)
        assert (
            choose_sink(
                DeliveryMode.AUTO,
                "other",
                BOTH,
                {"draft": draft, "send": FakeSendSink()},
            )
            is draft
        )


class TestDraftSink:
    def test_it_creates_a_draft_and_reports_the_id(self, adapter):
        result = DraftSink(adapter).deliver(reply())
        assert result == DeliveryResult(action="drafted", gmail_draft_id="draft-1")

    def test_it_reports_no_sent_message_id(self, adapter):
        # Nothing was sent, so nothing may look as though it was.
        assert DraftSink(adapter).deliver(reply()).gmail_message_id_out is None

    def test_it_has_no_send_path(self):
        assert not any("send" in name.lower() for name in dir(DraftSink))


class TestTheCapsGate:
    """A reached cap drafts. It never drops the reply and never raises.

    That is the whole point of a cap here: it bounds how much goes out, not
    whether the captain gets to see it. Dropping the reply would turn a
    capacity limit into lost mail.
    """

    def _send_sink(self, adapter, **caps):
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import SendSink

        return SendSink(adapter, CapsState(**caps))

    def test_within_the_caps_the_send_sink_is_chosen(self, adapter):
        send = self._send_sink(adapter, daily_cap=30, per_sender_cap=2)
        assert (
            choose_sink(
                DeliveryMode.AUTO,
                "en",
                EN_ONLY,
                {"draft": DraftSink(adapter), "send": send},
                "hash-a",
            )
            is send
        )

    def test_a_reached_daily_cap_drafts(self, adapter, caplog):
        draft = DraftSink(adapter)
        send = self._send_sink(adapter, daily_cap=1, per_sender_cap=5, sent_today=1)

        with attached_caplog(caplog, "email_bot_delivery", level="INFO"):
            chosen = choose_sink(
                DeliveryMode.AUTO,
                "en",
                EN_ONLY,
                {"draft": draft, "send": send},
                "hash-a",
            )

        assert chosen is draft
        assert "daily" in caplog.text

    def test_a_reached_per_sender_cap_drafts(self, adapter, caplog):
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import SendSink

        draft = DraftSink(adapter)
        send = SendSink(
            adapter,
            CapsState(
                daily_cap=30,
                per_sender_cap=1,
                sent_today=1,
                sent_today_by_sender={"hash-a": 1},
            ),
        )

        with attached_caplog(caplog, "email_bot_delivery", level="INFO"):
            chosen = choose_sink(
                DeliveryMode.AUTO,
                "en",
                EN_ONLY,
                {"draft": draft, "send": send},
                "hash-a",
            )

        assert chosen is draft
        assert "per_sender" in caplog.text

    def test_another_sender_still_sends_when_one_is_capped(self, adapter):
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import SendSink

        # The loop bound is per sender: one misbehaving responder must not stop
        # everyone else being answered.
        send = SendSink(
            adapter,
            CapsState(
                daily_cap=30,
                per_sender_cap=1,
                sent_today=1,
                sent_today_by_sender={"hash-a": 1},
            ),
        )
        sinks = {"draft": DraftSink(adapter), "send": send}

        assert (
            choose_sink(DeliveryMode.AUTO, "en", EN_ONLY, sinks, "hash-a") is not send
        )
        assert choose_sink(DeliveryMode.AUTO, "en", EN_ONLY, sinks, "hash-b") is send

    def test_the_language_gate_is_checked_before_the_caps(self, adapter):
        # An uncleared language drafts regardless of capacity, so a sign-off
        # that has not happened is never overridden by there being room.
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import SendSink

        draft = DraftSink(adapter)
        send = SendSink(adapter, CapsState(daily_cap=30, per_sender_cap=2))

        assert (
            choose_sink(
                DeliveryMode.AUTO,
                "vi",
                EN_ONLY,
                {"draft": draft, "send": send},
                "hash-a",
            )
            is draft
        )

    def test_off_never_reaches_the_caps_at_all(self, adapter):
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import SendSink

        send = SendSink(adapter, CapsState(daily_cap=30, per_sender_cap=2))
        assert (
            choose_sink(
                DeliveryMode.OFF,
                "en",
                EN_ONLY,
                {"draft": DraftSink(adapter), "send": send},
            )
            is None
        )


class TestSendSink:
    def test_it_sends_and_reports_the_message_id(self, adapter):
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import SendSink
        from tests.fixtures.email_bot import SendingFakeTransport

        transport = SendingFakeTransport(mails=[load_gmail("signup_en.json")])
        sending_adapter = type(adapter)(transport)
        sink = SendSink(sending_adapter, CapsState(daily_cap=30, per_sender_cap=2))

        result = sink.deliver(reply())

        assert result.action == "sent"
        assert result.gmail_message_id_out == "sent-1"
        assert result.gmail_draft_id is None
        assert len(transport.sent) == 1

    def test_it_records_the_send_against_the_caps(self, adapter):
        # Otherwise the caps would only bound what previous runs sent, and one
        # poll at the per-run cap could send twenty replies to one person.
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import SendSink
        from tests.fixtures.email_bot import SendingFakeTransport

        transport = SendingFakeTransport(mails=[load_gmail("signup_en.json")])
        caps = CapsState(daily_cap=30, per_sender_cap=2)
        sink = SendSink(type(adapter)(transport), caps)

        sink.deliver(reply())

        assert caps.sent_today == 1

    def test_it_derives_the_sender_key_rather_than_carrying_one(self, adapter):
        # OutboundReply holds the address because a reply has to be addressed
        # to somebody; the caps need the non-identifying form, and deriving it
        # keeps the hash out of the reply object and out of anything logging one.
        from app.services.channels.mail_guards import sender_key
        from app.services.email_bot.caps import CapsState
        from app.services.email_bot.delivery import SendSink
        from tests.fixtures.email_bot import SendingFakeTransport

        transport = SendingFakeTransport(mails=[load_gmail("signup_en.json")])
        caps = CapsState(daily_cap=30, per_sender_cap=2)
        SendSink(type(adapter)(transport), caps).deliver(reply())

        assert sender_key("sender@example.com") in caps.sent_today_by_sender
