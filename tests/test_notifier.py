"""Escalation notification: one recipient for the forward, no PII in Discord.

Two assertions here carry most of the weight. The forward must be addressed to
the configured recipient and to nothing else, because that single address is what
makes "nothing can reach a member of the public before E3" checkable. And the
Discord payload must not contain the sender's address or a word of the body,
because a chat channel has a wider audience than a mailbox.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.channels.base import EMAIL_CHANNEL, IncomingMessage
from app.services.notifier import (
    URGENT_CATEGORIES,
    CompositeNotifier,
    DiscordNotifier,
    EmailForwardNotifier,
    EscalationEvent,
    build_discord_payload,
    build_forward_body,
)
from app.services.triage.policy import decide
from tests.fixtures.email_bot import signals

OWNER = "owner@example.com"
SENDER = "a.person@example.com"
BODY = "I have a serious concern about a class yesterday."
SUBJECT = "Concern about a class"
THREAD_URL = "https://mail.google.com/mail/u/0/#inbox/thread-1"


def event(
    category: str = "safeguarding_legal",
    summary: str | None = "A parent raised a safety concern.",
) -> EscalationEvent:
    message = IncomingMessage(
        channel=EMAIL_CHANNEL,
        thread_key="thread-1",
        provider_message_id="msg-1",
        sender_key="hash-a",
        sender_address=SENDER,
        subject=SUBJECT,
        text=BODY,
        rfc_message_id="<abc@x>",
    )
    return EscalationEvent(
        message=message,
        decision=decide(
            signals(category=category, language="vi", confidence=0.91), 0.6
        ),
        reason=f"executive category {category}",
        summary=summary,
        gmail_thread_url=THREAD_URL,
    )


class RecordingEmailService:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self._error = error

    def send_custom_email(self, **kwargs):
        if self._error is not None:
            raise self._error
        self.sent.append(kwargs)
        return True


class RecordingHttp:
    def __init__(self, error: Exception | None = None) -> None:
        self.posts: list[dict] = []
        self._error = error

    def post(self, url, json):  # noqa: A002 - httpx's own parameter name
        if self._error is not None:
            raise self._error
        self.posts.append({"url": url, "json": json})


class TestTheForwardGoesToExactlyOneAddress:
    def test_the_recipient_is_the_configured_owner(self):
        email_service = RecordingEmailService()
        EmailForwardNotifier(email_service, OWNER).notify(event())
        assert email_service.sent[0]["to_email"] == OWNER

    def test_the_recipient_is_never_taken_from_the_event(self):
        # No classification result, header or mid-run setting read can redirect
        # a forward: the recipient is fixed at construction.
        email_service = RecordingEmailService()
        notifier = EmailForwardNotifier(email_service, OWNER)
        for category in ("safeguarding_legal", "donation", "press", "human_other"):
            notifier.notify(event(category))

        recipients = {sent["to_email"] for sent in email_service.sent}
        assert recipients == {OWNER}

    def test_the_senders_address_is_never_a_recipient(self):
        email_service = RecordingEmailService()
        EmailForwardNotifier(email_service, OWNER).notify(event())
        assert SENDER not in email_service.sent[0]["to_email"]

    def test_an_unset_recipient_forwards_nothing(self):
        # A loud no-op: the pipeline refuses to run at all while
        # ESCALATION_OWNER_EMAIL is empty, so this is defence in depth.
        email_service = RecordingEmailService()
        EmailForwardNotifier(email_service, "").notify(event())
        assert email_service.sent == []

    def test_the_subject_names_the_tier(self):
        email_service = RecordingEmailService()
        EmailForwardNotifier(email_service, OWNER).notify(event())
        assert "needs_executive" in email_service.sent[0]["subject"]
        assert SUBJECT in email_service.sent[0]["subject"]


class TestTheForwardBody:
    def test_the_three_line_header_is_present(self):
        body = build_forward_body(event())
        assert "Category: safeguarding_legal" in body
        assert "Why it escalated:" in body
        assert "Summary: A parent raised a safety concern." in body

    def test_the_original_is_quoted_in_full(self):
        # The original is the best context the captain can have, and it needs no
        # re-typing.
        body = build_forward_body(event())
        assert BODY in body
        assert SUBJECT in body
        assert SENDER in body

    def test_a_missing_summary_says_so_rather_than_lying(self):
        assert "Summary: not available" in build_forward_body(event(summary=None))

    def test_the_thread_link_is_included(self):
        assert THREAD_URL in build_forward_body(event())

    def test_html_in_the_original_is_escaped(self):
        # The forward is an HTML mail, so an inbound body containing markup must
        # not become markup in the captain's mailbox.
        original = event()
        hostile = EscalationEvent(
            message=IncomingMessage(
                channel=EMAIL_CHANNEL,
                thread_key="t",
                provider_message_id="m",
                sender_key="h",
                sender_address=SENDER,
                subject="<script>alert(1)</script>",
                text="<img src=x onerror=alert(1)>",
                rfc_message_id="<x@y>",
            ),
            decision=original.decision,
            reason=original.reason,
            summary=original.summary,
            gmail_thread_url=THREAD_URL,
        )
        body = build_forward_body(hostile)
        assert "<script>" not in body
        assert "&lt;script&gt;" in body
        assert "onerror=alert(1)>" not in body


class TestTheDiscordPostCarriesNoPII:
    @pytest.mark.parametrize(
        "category", ["safeguarding_legal", "donation", "press", "human_other"]
    )
    def test_neither_the_address_nor_the_body_appears(self, category):
        content = build_discord_payload(event(category))["content"]
        assert SENDER not in content
        assert BODY not in content
        assert "@example.com" not in content

    def test_the_subject_does_not_appear_either(self):
        # A subject line is often the whole question, and sometimes the whole
        # disclosure.
        assert SUBJECT not in build_discord_payload(event())["content"]

    def test_the_operational_facts_are_all_there(self):
        content = build_discord_payload(event("donation"))["content"]
        assert "donation" in content
        assert "needs_executive" in content
        assert "vi" in content
        assert "0.91" in content
        assert THREAD_URL in content

    def test_the_summary_is_included_when_present(self):
        content = build_discord_payload(event())["content"]
        assert "A parent raised a safety concern." in content

    def test_a_missing_summary_simply_omits_the_line(self):
        content = build_discord_payload(event(summary=None))["content"]
        assert "Summary:" not in content

    @pytest.mark.parametrize("category", sorted(URGENT_CATEGORIES))
    def test_safeguarding_is_urgent_and_mentions_here(self, category):
        payload = build_discord_payload(event(category))
        assert "URGENT" in payload["content"]
        assert "@here" in payload["content"]
        assert payload["allowed_mentions"] == {"parse": ["everyone"]}

    @pytest.mark.parametrize("category", ["donation", "press", "human_other", "faq"])
    def test_everything_else_is_not_urgent(self, category):
        payload = build_discord_payload(event(category))
        assert "URGENT" not in payload["content"]
        assert "@here" not in payload["content"]
        assert "allowed_mentions" not in payload


class TestDiscordNotifier:
    def test_the_payload_is_posted_to_the_webhook(self):
        http = RecordingHttp()
        DiscordNotifier("https://discord.example.com/hook", http=http).notify(event())

        assert http.posts[0]["url"] == "https://discord.example.com/hook"
        assert "safeguarding_legal" in http.posts[0]["json"]["content"]

    def test_an_unset_webhook_posts_nothing(self):
        http = RecordingHttp()
        DiscordNotifier("", http=http).notify(event())
        assert http.posts == []


class TestCompositeNotifier:
    def test_every_notifier_receives_the_event(self):
        email_service = RecordingEmailService()
        http = RecordingHttp()
        CompositeNotifier(
            [
                EmailForwardNotifier(email_service, OWNER),
                DiscordNotifier("https://discord.example.com/hook", http=http),
            ]
        ).notify(event())

        assert len(email_service.sent) == 1
        assert len(http.posts) == 1

    def test_a_discord_failure_does_not_raise_past_the_composite(self):
        # The forward is the durable record, so a Discord outage must not lose
        # the escalation or abort the run that produced it.
        email_service = RecordingEmailService()
        CompositeNotifier(
            [
                DiscordNotifier(
                    "https://discord.example.com/hook",
                    http=RecordingHttp(error=RuntimeError("503")),
                ),
                EmailForwardNotifier(email_service, OWNER),
            ]
        ).notify(event())

        assert len(email_service.sent) == 1

    def test_a_forward_failure_does_not_cost_the_discord_post(self):
        http = RecordingHttp()
        CompositeNotifier(
            [
                EmailForwardNotifier(
                    RecordingEmailService(error=RuntimeError("SMTP auth failed")), OWNER
                ),
                DiscordNotifier("https://discord.example.com/hook", http=http),
            ]
        ).notify(event())

        assert len(http.posts) == 1

    def test_an_empty_composite_is_a_no_op(self):
        CompositeNotifier([]).notify(event())


class TestTheOperationalAlertPath:
    """An alert about the bot itself, which structurally carries no mail.

    Its own type rather than a faked escalation: there is no IncomingMessage,
    no category and no thread, so an alert cannot leak a body or an address
    even by accident.
    """

    def _alert(self, urgent: bool = False):
        from app.services.notifier import OperationalAlert

        return OperationalAlert(
            subject="the inbox bot's Gmail grant was revoked",
            detail="EMAIL_BOT_MODE has been set to off. Re-consent by the runbook.",
            urgent=urgent,
        )

    def test_the_forward_notifier_mails_the_one_recipient(self):
        from app.services.notifier import EmailForwardNotifier

        email_service = RecordingEmailService()
        EmailForwardNotifier(email_service, OWNER).alert(self._alert())

        assert email_service.sent[0]["to_email"] == OWNER
        assert "revoked" in email_service.sent[0]["subject"]

    def test_an_unset_recipient_mails_nothing(self):
        from app.services.notifier import EmailForwardNotifier

        email_service = RecordingEmailService()
        EmailForwardNotifier(email_service, "").alert(self._alert())
        assert email_service.sent == []

    def test_the_discord_payload_carries_the_subject_and_detail(self):
        from app.services.notifier import build_alert_payload

        content = build_alert_payload(self._alert())["content"]
        assert "revoked" in content
        assert "set to off" in content

    def test_an_urgent_alert_mentions_here(self):
        from app.services.notifier import build_alert_payload

        payload = build_alert_payload(self._alert(urgent=True))
        assert "@here" in payload["content"]
        assert payload["allowed_mentions"] == {"parse": ["everyone"]}

    def test_an_ordinary_alert_does_not(self):
        from app.services.notifier import build_alert_payload

        payload = build_alert_payload(self._alert())
        assert "@here" not in payload["content"]
        assert "allowed_mentions" not in payload

    def test_an_alert_cannot_carry_mail_content(self):
        # Structural: the type has no field for it.
        import dataclasses

        from app.services.notifier import OperationalAlert

        fields = {f.name for f in dataclasses.fields(OperationalAlert)}
        assert fields == {"subject", "detail", "urgent"}

    def test_the_composite_fans_out_and_isolates_failures(self):
        from app.services.notifier import (
            CompositeNotifier,
            DiscordNotifier,
            EmailForwardNotifier,
        )

        # An alert about the bot being broken is exactly when one of its own
        # channels is most likely broken too.
        email_service = RecordingEmailService()
        http = RecordingHttp()
        CompositeNotifier(
            [
                DiscordNotifier(
                    "https://discord.example.com/hook",
                    http=RecordingHttp(error=RuntimeError("503")),
                ),
                EmailForwardNotifier(email_service, OWNER),
                DiscordNotifier("https://discord.example.com/hook", http=http),
            ]
        ).alert(self._alert(urgent=True))

        assert len(email_service.sent) == 1
        assert len(http.posts) == 1
