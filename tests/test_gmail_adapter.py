"""Parsing every recorded payload into an ``IncomingMessage``.

Every fixture is parsed, not just a representative one: this is the layer where
real mail meets our assumptions, and the interesting cases are all the ones a
hand-written happy-path fixture would never contain - no plain part, no body at
all, no ``Message-ID``, a non-ASCII subject.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

from datetime import UTC, datetime

import pytest

from app.services.channels.base import EMAIL_CHANNEL, OutboundReply
from app.services.channels.gmail import GmailAdapter
from app.services.channels.gmail_transport import LABEL_SEEN
from app.services.channels.mail_guards import sender_key
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.email_bot import FakeTransport, load_gmail, raw

ALL_FIXTURES = [
    "signup_en.json",
    "signup_vi.json",
    "faq_en.json",
    "html_only.json",
    "empty_body.json",
    "newsletter.json",
    "vacation_autoreply.json",
    "bounce.json",
    "safeguarding_vi.json",
    "sponsorship_en.json",
    "multi_topic_en.json",
    "second_inbound.json",
]


@pytest.fixture
def transport():
    return FakeTransport(mails=[load_gmail(name) for name in ALL_FIXTURES])


@pytest.fixture
def adapter(transport):
    return GmailAdapter(transport)


class TestParseEveryFixture:
    @pytest.mark.parametrize("fixture", ALL_FIXTURES)
    def test_every_recorded_payload_parses(self, adapter, fixture):
        message = adapter.parse(raw(fixture))

        assert message.channel == EMAIL_CHANNEL
        assert message.provider_message_id
        assert message.thread_key
        # Never empty: an id with no threading anchor is the case that renders
        # a reply outside its conversation.
        assert message.rfc_message_id
        assert isinstance(message.received_at, datetime)

    @pytest.mark.parametrize("fixture", ALL_FIXTURES)
    def test_the_sender_key_never_contains_the_address(self, adapter, fixture):
        message = adapter.parse(raw(fixture))
        assert "@" not in message.sender_key
        assert message.sender_key == sender_key(message.sender_address)


class TestParsedFields:
    def test_a_plain_english_signup(self, adapter):
        message = adapter.parse(raw("signup_en.json"))

        assert message.subject == "Volunteering with Vietnam Hearts"
        assert message.sender_address == payloads.TEST_SENDER
        assert "How do I sign up?" in message.text
        assert message.rfc_message_id == "<abc123@mail.example.com>"

    def test_a_vietnamese_subject_and_body_survive(self, adapter):
        message = adapter.parse(raw("signup_vi.json"))
        assert message.subject == "Đăng ký làm tình nguyện viên"
        assert "tình nguyện viên" in message.text

    def test_html_only_mail_gets_text_from_the_html_part(self, adapter):
        # Review Focus item 1.
        message = adapter.parse(raw("html_only.json"))
        assert "teaching assistant" in message.text
        assert "<p>" not in message.text

    def test_an_empty_body_parses_to_an_empty_string(self, adapter):
        # Not an exception, and not invented text. The pipeline escalates it.
        message = adapter.parse(raw("empty_body.json"))
        assert message.text == ""

    def test_a_missing_message_id_falls_back_to_a_gmail_derived_one(self, adapter):
        message = adapter.parse(raw("empty_body.json"))
        assert message.rfc_message_id == "<gmail-18f2a1b4c5d6e834@mail.gmail.com>"

    def test_the_references_chain_is_parsed(self, adapter):
        message = adapter.parse(raw("second_inbound.json"))
        assert message.references == ("<abc123@mail.example.com>",)

    def test_a_mail_with_no_references_has_an_empty_chain(self, adapter):
        assert adapter.parse(raw("signup_en.json")).references == ()

    @pytest.mark.parametrize(
        "header_value,expected",
        [
            ("<a@x> <b@x>", ("<a@x>", "<b@x>")),
            ("<a@x>,<b@x>", ("<a@x>", "<b@x>")),
            ("<a@x>\r\n <b@x>", ("<a@x>", "<b@x>")),
            ("garbage", ()),
            ("", ()),
        ],
    )
    def test_references_parsing_tolerates_real_world_forms(
        self, adapter, header_value, expected
    ):
        resource = payloads.message(references=header_value or None)
        assert adapter.parse(raw_from(resource)).references == expected

    def test_label_ids_are_carried_through_for_the_guards(self, adapter):
        message = adapter.parse(raw("newsletter.json"))
        assert "CATEGORY_UPDATES" in message.label_ids

    def test_headers_are_lower_cased_for_the_guards(self, adapter):
        message = adapter.parse(raw("vacation_autoreply.json"))
        assert message.headers["auto-submitted"] == "auto-replied"

    def test_the_received_time_comes_from_gmails_internal_date(self, adapter):
        message = adapter.parse(raw("signup_en.json"))
        assert message.received_at == datetime.fromtimestamp(
            1_759_000_000_000 / 1000, tz=UTC
        )

    def test_an_unparseable_internal_date_falls_back_to_now(self, adapter):
        resource = payloads.message()
        resource["internalDate"] = "not a number"
        before = datetime.now(UTC)
        message = adapter.parse(raw_from(resource))
        assert message.received_at >= before

    def test_a_display_name_is_stripped_from_the_sender(self, adapter):
        resource = payloads.message(from_address='"A Person" <Person@Example.COM>')
        assert adapter.parse(raw_from(resource)).sender_address == "person@example.com"


class TestListingAndLabelling:
    def test_listing_excludes_the_marker_label(self, adapter, transport):
        adapter.list_new(limit=5, newer_than_days=7)
        assert transport.list_calls == [
            {"newer_than_days": 7, "exclude_label": LABEL_SEEN, "limit": 5}
        ]

    def test_labelling_creates_the_labels_it_needs(self, adapter, transport):
        adapter.label("msg-1", ["VH-Bot/Drafted", LABEL_SEEN])
        assert transport.labels_for("msg-1") == ["VH-Bot/Drafted", LABEL_SEEN]

    def test_labelling_with_no_names_does_nothing(self, adapter, transport):
        adapter.label("msg-1", [])
        assert transport.applied == []

    def test_the_category_label_lives_under_the_bot_prefix(self, adapter):
        assert adapter.category_label("signup") == "VH-Bot/signup"

    def test_the_thread_url_points_at_gmail(self, adapter):
        assert adapter.thread_url("abc") == (
            "https://mail.google.com/mail/u/0/#inbox/abc"
        )


class TestDrafting:
    def _reply(self) -> OutboundReply:
        return OutboundReply(
            thread_key="thread-9",
            to_address=payloads.TEST_SENDER,
            subject="Volunteering",
            in_reply_to="<abc123@mail.example.com>",
            references=("<root@x>",),
            text="Thanks for writing.\n\n- Vietnam Hearts automated assistant",
            language="en",
            kind="signup",
        )

    def test_a_draft_is_created_in_the_thread(self, adapter, transport):
        draft_id = adapter.draft(self._reply())
        assert draft_id == "draft-1"
        assert draft_id in transport.drafts

    def test_the_drafted_mime_carries_the_threading_headers(self, adapter, transport):
        draft_id = adapter.draft(self._reply())
        mime = transport.drafts[draft_id].decode("utf-8")
        assert "In-Reply-To: <abc123@mail.example.com>" in mime
        assert "References: <root@x> <abc123@mail.example.com>" in mime
        assert "Subject: Re: Volunteering" in mime
        assert "Auto-Submitted: auto-replied" in mime
        assert "X-Auto-Response-Suppress: All" in mime

    def test_the_draft_is_from_the_inbox_the_grant_belongs_to(self, adapter, transport):
        draft_id = adapter.draft(self._reply())
        assert payloads.TEST_INBOX in transport.drafts[draft_id].decode("utf-8")

    def test_deleting_a_draft_reaches_the_transport(self, adapter, transport):
        adapter.delete_draft("draft-1")
        assert transport.deleted_drafts == ["draft-1"]

    def test_the_adapter_cannot_send(self):
        assert not hasattr(GmailAdapter, "send")
        assert not any("send" in name.lower() for name in dir(GmailAdapter))


def raw_from(resource):
    from app.services.channels.gmail_transport import RawMail

    return RawMail.from_resource(resource)
