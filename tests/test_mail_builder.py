"""MIME construction and text extraction, both pure.

Threading is the part that is invisible until a real client renders the reply
outside the conversation, so the three headers that create it get direct
assertions rather than being taken on trust from a Gmail draft call.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

from email import message_from_bytes

import pytest

from app.services.channels.base import OutboundReply
from app.services.channels.mail_builder import (
    AUTO_RESPONSE_HEADERS,
    build_reply,
    extract_text,
    reference_chain,
    reply_subject,
    strip_html,
)
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.email_bot import load_gmail

FIXED_ID = "<fixed-id@mail.example.com>"
FIXED_DATE = "Mon, 29 Sep 2026 09:15:00 +0700"


def reply(
    text: str = "Thanks for writing.",
    subject: str = "Volunteering",
    in_reply_to: str = "<abc123@mail.example.com>",
    references: tuple[str, ...] = (),
    language: str = "en",
) -> OutboundReply:
    return OutboundReply(
        thread_key="thread-1",
        to_address=payloads.TEST_SENDER,
        subject=subject,
        in_reply_to=in_reply_to,
        references=references,
        text=text,
        language=language,
        kind="signup",
    )


def parsed(built: bytes):
    return message_from_bytes(built)


class TestReplySubject:
    @pytest.mark.parametrize(
        "subject,expected",
        [
            ("Volunteering", "Re: Volunteering"),
            ("Re: Volunteering", "Re: Volunteering"),
            ("RE: Volunteering", "Re: Volunteering"),
            ("re:Volunteering", "Re: Volunteering"),
            # A thread round-tripped a few times must not gain a fourth Re:.
            ("Re: RE: Re: Volunteering", "Re: Volunteering"),
            ("  Volunteering  ", "Re: Volunteering"),
            ("", "Re:"),
        ],
    )
    def test_re_is_added_exactly_once(self, subject, expected):
        assert reply_subject(subject) == expected

    def test_a_non_ascii_subject_survives(self):
        assert reply_subject("Đăng ký tình nguyện") == "Re: Đăng ký tình nguyện"


class TestReferenceChain:
    def test_the_inherited_chain_is_preserved_in_order(self):
        chain = reference_chain(("<one@x>", "<two@x>"), "<three@x>")
        assert chain == "<one@x> <two@x> <three@x>"

    def test_the_replied_to_id_is_appended(self):
        assert reference_chain((), "<only@x>") == "<only@x>"

    def test_duplicates_are_dropped(self):
        # A client that sees the same id twice may split the thread.
        assert reference_chain(("<one@x>", "<two@x>"), "<two@x>") == "<one@x> <two@x>"

    def test_an_empty_chain_is_empty(self):
        assert reference_chain((), "") == ""


class TestBuildReply:
    def test_the_threading_headers_are_present(self):
        built = parsed(
            build_reply(
                reply(references=("<root@x>",)),
                from_address=payloads.TEST_INBOX,
                message_id=FIXED_ID,
                date=FIXED_DATE,
            )
        )
        assert built["In-Reply-To"] == "<abc123@mail.example.com>"
        assert built["References"] == "<root@x> <abc123@mail.example.com>"
        assert built["Subject"] == "Re: Volunteering"

    @pytest.mark.parametrize("name,value", sorted(AUTO_RESPONSE_HEADERS.items()))
    def test_every_rfc_3834_header_is_present(self, name, value):
        # So a well-behaved responder on the other side stays silent instead of
        # answering our reply and starting a loop.
        built = parsed(build_reply(reply(), from_address=payloads.TEST_INBOX))
        assert built[name] == value

    def test_the_from_and_to_are_the_inbox_and_the_sender(self):
        built = parsed(build_reply(reply(), from_address=payloads.TEST_INBOX))
        assert payloads.TEST_INBOX in built["From"]
        assert built["To"] == payloads.TEST_SENDER

    def test_a_vietnamese_body_round_trips(self):
        body = "Cảm ơn bạn rất nhiều vì đã nhắn tin 💚"
        built = parsed(build_reply(reply(text=body), from_address=payloads.TEST_INBOX))
        decoded = built.get_payload(decode=True).decode("utf-8")
        assert body in decoded

    def test_the_body_is_plain_text_only(self):
        # A conversation, not a newsletter. Plain text is what a human typing
        # in Gmail produces and it avoids the quoted-printable surprises an
        # HTML alternative invites for Vietnamese.
        built = parsed(build_reply(reply(), from_address=payloads.TEST_INBOX))
        assert built.get_content_type() == "text/plain"
        assert not built.is_multipart()

    def test_no_in_reply_to_header_when_there_is_nothing_to_reply_to(self):
        built = parsed(
            build_reply(reply(in_reply_to=""), from_address=payloads.TEST_INBOX)
        )
        assert built["In-Reply-To"] is None
        assert built["References"] is None

    def test_the_body_is_taken_exactly_as_given(self):
        # Signature and all: the copy belongs to email_bot/replies.py, and the
        # stored text has to match what was actually offered.
        built = parsed(
            build_reply(
                reply(text="Body line.\n\n- Signed off"),
                from_address=payloads.TEST_INBOX,
            )
        )
        decoded = built.get_payload(decode=True).decode("utf-8")
        assert decoded.strip() == "Body line.\n\n- Signed off"

    def test_output_is_deterministic_when_id_and_date_are_injected(self):
        first = build_reply(
            reply(), payloads.TEST_INBOX, message_id=FIXED_ID, date=FIXED_DATE
        )
        second = build_reply(
            reply(), payloads.TEST_INBOX, message_id=FIXED_ID, date=FIXED_DATE
        )
        assert first == second


class TestExtractText:
    def test_plain_text_wins(self):
        resource = payloads.message(
            text="The plain part.", html="<p>The HTML part.</p>"
        )
        assert extract_text(resource) == "The plain part."

    def test_html_is_the_fallback_when_there_is_no_plain_part(self):
        # Review Focus item 1: plenty of real clients send HTML only, and
        # treating that as an empty body would route an ordinary question to
        # the wrong tier.
        resource = load_gmail("html_only.json")
        text = extract_text(resource)
        assert "I would like to volunteer as a teaching assistant." in text
        assert "Hello!" in text

    def test_script_and_style_contents_are_dropped(self):
        resource = load_gmail("html_only.json")
        text = extract_text(resource)
        assert "track()" not in text
        assert "color:red" not in text

    def test_an_empty_body_stays_empty(self):
        # Left empty on purpose so the pipeline escalates it rather than the
        # extractor inventing something to classify.
        assert extract_text(load_gmail("empty_body.json")) == ""

    def test_a_resource_with_no_payload_is_empty_rather_than_an_error(self):
        assert extract_text({"id": "x"}) == ""

    def test_nested_multipart_parts_are_found(self):
        resource = payloads.message(text="inner text")
        resource["payload"] = {
            "mimeType": "multipart/mixed",
            "headers": resource["payload"]["headers"],
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {"size": 0},
                    "parts": [payloads.plain_part("inner text")],
                }
            ],
        }
        assert extract_text(resource) == "inner text"

    def test_several_plain_parts_are_concatenated(self):
        # multipart/mixed mail where the sentence the person wrote and their
        # quoted context arrive as separate parts of the same type. Taking only
        # the first would lose half the question.
        resource = payloads.message(text="first", multipart=True)
        resource["payload"]["parts"] = [
            payloads.plain_part("first"),
            payloads.plain_part("second"),
        ]
        assert extract_text(resource) == "first\nsecond"

    def test_a_part_with_only_an_attachment_id_is_not_fetched(self):
        resource = payloads.message(text="")
        resource["payload"]["parts"] = [
            {
                "mimeType": "text/plain",
                "body": {"size": 1000, "attachmentId": "att-1"},
            }
        ]
        assert extract_text(resource) == ""

    def test_undecodable_data_does_not_raise(self):
        resource = payloads.message(text="")
        resource["payload"]["parts"] = [
            {"mimeType": "text/plain", "body": {"data": "!!!not base64!!!"}}
        ]
        assert extract_text(resource) == ""

    def test_a_vietnamese_body_decodes(self):
        text = extract_text(load_gmail("signup_vi.json"))
        assert "tình nguyện viên" in text


class TestStripHtml:
    def test_paragraphs_are_separated_by_a_blank_line(self):
        # Both the opening and the closing tag emit a break, which is what
        # separates paragraphs rather than running them together. A void tag
        # like <br> has no closing tag and so yields a single newline.
        assert strip_html("<p>one</p><p>two</p>") == "one\n\ntwo"

    def test_a_line_break_tag_yields_one_newline(self):
        assert strip_html("one<br>two") == "one\ntwo"

    def test_entities_are_decoded(self):
        assert strip_html("<p>Tom &amp; Jerry</p>") == "Tom & Jerry"

    def test_empty_input_is_empty(self):
        assert strip_html("") == ""
