"""Where a reply goes: nowhere, a Gmail draft, or a send in the thread.

The decision table is inbox-core's ``Delivery``. These tests drive it as the
bot wires it - the Gmail sink over a fake transport, and ``CapsState`` - so
what they pin is the observable result for this inbox: which mode, language
and cap leads to a draft, which to a send, and that a run not in ``auto`` holds
nothing that can send at all.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest
from inbox_core import DeliveryMode

from app.services.channels.gmail import GmailAdapter, SendingDisabled, email_reply
from app.services.email_bot.caps import CapsState
from app.services.email_bot.delivery import build_delivery
from tests.fixtures.email_bot import SendingFakeTransport, load_gmail
from tests.fixtures.logs import attached_caplog

EN_ONLY = frozenset({"en"})
BOTH = frozenset({"en", "vi"})


@pytest.fixture
def transport():
    return SendingFakeTransport(mails=[load_gmail("signup_en.json")])


@pytest.fixture
def adapter(transport):
    return GmailAdapter(transport)


@pytest.fixture
def message(adapter, transport):
    return adapter.parse(
        transport.list_unprocessed(newer_than_days=7, exclude_label="", limit=1)[0]
    )


def reply(message, language: str = "en"):
    return email_reply(
        message, text="Thanks for writing.", language=language, kind="signup"
    )


def deliver(adapter, message, *, mode, language="en", languages=EN_ONLY, caps=None):
    delivery = build_delivery(
        adapter, mode=mode, auto_languages=languages, caps=caps or CapsState()
    )
    return delivery.deliver(reply(message, language), sender_key=message.sender_key)


class TestTheModeTable:
    def test_off_delivers_nothing(self, adapter, message, transport):
        result = deliver(adapter, message, mode=DeliveryMode.OFF)
        assert result.action == "none"
        assert transport.drafts == {}
        assert transport.sent == []

    def test_draft_holds_the_reply_as_a_gmail_draft(self, adapter, message, transport):
        result = deliver(adapter, message, mode=DeliveryMode.DRAFT)
        assert result.action == "held"
        assert result.provider_ref in transport.drafts
        assert transport.sent == []

    def test_auto_sends_a_cleared_language(self, adapter, message, transport):
        result = deliver(adapter, message, mode=DeliveryMode.AUTO)
        assert result.action == "sent"
        assert result.provider_ref == "sent-1"
        assert transport.drafts == {}

    def test_auto_drafts_a_language_that_is_not_cleared(
        self, adapter, message, transport
    ):
        result = deliver(adapter, message, mode=DeliveryMode.AUTO, language="vi")
        assert result.action == "held"
        assert transport.sent == []

    def test_auto_sends_vietnamese_once_it_is_cleared(
        self, adapter, message, transport
    ):
        result = deliver(
            adapter, message, mode=DeliveryMode.AUTO, language="vi", languages=BOTH
        )
        assert result.action == "sent"

    def test_an_unknown_language_is_never_sent(self, adapter, message, transport):
        result = deliver(
            adapter, message, mode=DeliveryMode.AUTO, language="other", languages=BOTH
        )
        assert result.action == "held"
        assert transport.sent == []


class TestOnlyAutoCanSend:
    @pytest.mark.parametrize("mode", [DeliveryMode.OFF, DeliveryMode.DRAFT])
    def test_a_sink_built_for_another_mode_refuses_to_send(
        self, adapter, message, mode
    ):
        # Defence in depth behind the table: even a caller that reached
        # send_to_public directly could not send from a draft-mode run.
        delivery = build_delivery(adapter, mode=mode, auto_languages=EN_ONLY)
        with pytest.raises(SendingDisabled):
            delivery.sink.send_to_public(reply(message))

    def test_a_draft_mode_run_never_reaches_the_transports_send(self, message):
        transport = SendingFakeTransport(
            mails=[load_gmail("signup_en.json")], raise_on_send=True
        )
        result = deliver(GmailAdapter(transport), message, mode=DeliveryMode.DRAFT)
        assert result.action == "held"


class TestTheCapsGate:
    def test_within_the_caps_the_reply_is_sent(self, adapter, message):
        caps = CapsState(daily_cap=5, per_sender_cap=2)
        assert deliver(adapter, message, mode=DeliveryMode.AUTO, caps=caps).action == (
            "sent"
        )

    def test_a_reached_daily_cap_drafts_and_says_so(self, adapter, message, caplog):
        caps = CapsState(daily_cap=1, sent_today=1)
        with attached_caplog(caplog, "email_bot_caps"):
            result = deliver(adapter, message, mode=DeliveryMode.AUTO, caps=caps)
        assert result.action == "held"
        assert result.held_because == "daily cap reached"
        assert "daily" in caplog.text

    def test_a_reached_per_sender_cap_drafts(self, adapter, message):
        caps = CapsState(per_sender_cap=1, sent_today_by_sender={message.sender_key: 1})
        result = deliver(adapter, message, mode=DeliveryMode.AUTO, caps=caps)
        assert result.action == "held"
        assert result.held_because == "per_sender cap reached"

    def test_a_send_is_counted_against_the_caps(self, adapter, message):
        caps = CapsState(daily_cap=5)
        deliver(adapter, message, mode=DeliveryMode.AUTO, caps=caps)
        assert caps.sent_today == 1
        assert caps.sent_today_by_sender == {message.sender_key: 1}

    def test_only_a_reached_cap_is_reported_as_capped(self, adapter, message):
        refused = []
        caps = CapsState(daily_cap=1, sent_today=1, on_refused=refused.append)

        deliver(adapter, message, mode=DeliveryMode.AUTO, language="vi", caps=caps)
        assert refused == []  # the language gate, not a cap

        deliver(adapter, message, mode=DeliveryMode.AUTO, caps=caps)
        assert [decision.cap for decision in refused] == ["daily"]

    @pytest.mark.parametrize("mode", [DeliveryMode.OFF, DeliveryMode.DRAFT])
    def test_modes_that_never_send_never_ask_the_caps(self, adapter, message, mode):
        refused = []
        caps = CapsState(daily_cap=1, sent_today=1, on_refused=refused.append)
        deliver(adapter, message, mode=mode, caps=caps)
        assert refused == []
