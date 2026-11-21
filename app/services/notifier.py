"""Escalation notification: a forward to the captain and a post to Discord.

Two channels because they fail differently. The forward is the durable record -
it lands in a mailbox, it carries the original mail, and it survives everyone
being asleep. The Discord post is the fast one, and it is deliberately the
thinner of the two: it says what category and tier, how confident, and links
the thread, and it carries neither the sender's address nor a word of the body,
because a chat channel has a wider audience than a mailbox.

The forward goes over the existing ``EmailService`` SMTP path, not over the
Gmail grant. That keeps the one outbound mail path in this whole phase pointed
at a single configured recipient, which is what makes "nothing can reach a
member of the public before E3" checkable rather than aspirational.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.services.channels.base import IncomingMessage
from app.services.triage.protocol import TriageDecision
from app.utils.logging_config import get_logger

logger = get_logger("notifier")

URGENT_CATEGORIES = frozenset({"safeguarding_legal"})


@dataclass(frozen=True)
class EscalationEvent:
    """Everything a notifier needs, and nothing it does not.

    Carries the ``IncomingMessage`` because the forward quotes the original -
    the best possible context for the captain, and one he does not have to
    re-type. The Discord notifier receives the same event and simply declines to
    read those fields.
    """

    message: IncomingMessage
    decision: TriageDecision
    reason: str
    summary: str | None
    gmail_thread_url: str


@dataclass(frozen=True)
class OperationalAlert:
    """Something wrong with the bot itself, rather than with a piece of mail.

    Its own type rather than a faked ``EscalationEvent``: there is no
    ``IncomingMessage``, no category and no thread, and pretending otherwise
    would make every notifier grow a "was there actually a message?" branch.

    It also means an alert is structurally incapable of carrying mail content,
    which is the right property for something that goes to a chat channel.
    """

    subject: str
    detail: str
    urgent: bool = False


class Notifier(Protocol):
    def notify(self, event: EscalationEvent) -> None: ...

    def alert(self, alert: OperationalAlert) -> None: ...


class EmailForwardNotifier:
    """Forwards the original mail to one configured recipient. No other address.

    The recipient is fixed at construction and never taken from the event, so
    no classification result, no header and no setting read mid-run can redirect
    a forward. The test asserts exactly this by failing on any other recipient.
    """

    def __init__(self, email_service: Any, recipient: str, db: Any = None) -> None:
        self._email_service = email_service
        self._recipient = (recipient or "").strip()
        self._db = db

    @property
    def recipient(self) -> str:
        return self._recipient

    def notify(self, event: EscalationEvent) -> None:
        if not self._recipient:
            # Without a recipient there is nothing to forward to. The design
            # makes the bot inert in that state rather than dropping the
            # escalation silently, so this is a loud no-op.
            logger.error(
                "No ESCALATION_OWNER_EMAIL configured; cannot forward %s",
                event.message.provider_message_id,
            )
            return

        subject = f"[VH bot: {event.decision.tier}] {event.message.subject}".strip()
        self._email_service.send_custom_email(
            to_email=self._recipient,
            subject=subject,
            html_body=build_forward_body(event),
            db=self._db,
            email_type="bot_escalation",
        )

    def alert(self, alert: OperationalAlert) -> None:
        """Mail an operational alert to the same single recipient.

        Same address as the forwards and for the same reason: one configured
        recipient, fixed at construction, is what makes "nothing here can reach
        anybody else" checkable.
        """
        if not self._recipient:
            logger.error(
                "No ESCALATION_OWNER_EMAIL configured; cannot send the alert: %s",
                alert.subject,
            )
            return

        self._email_service.send_custom_email(
            to_email=self._recipient,
            subject=f"[VH bot] {alert.subject}",
            html_body=_alert_body(alert),
            db=self._db,
            email_type="bot_alert",
        )


def _alert_body(alert: OperationalAlert) -> str:
    return (
        f"<p><b>{html.escape(alert.subject)}</b></p>"
        f'<pre style="white-space: pre-wrap; font-family: inherit">'
        f"{html.escape(alert.detail)}</pre>"
    )


def build_forward_body(event: EscalationEvent) -> str:
    """The forward: a three-line header, then the original quoted below it.

    Three lines because that is what the captain reads before deciding whether
    to open it: what it is, why the bot stepped back, and what it says in one
    sentence. The original follows verbatim so nothing is lost to summarisation.
    """
    decision = event.decision
    header_lines = [
        f"Category: {decision.category} ({decision.tier}, {decision.language})",
        f"Why it escalated: {event.reason}",
        f"Summary: {event.summary or 'not available'}",
    ]
    escaped_header = "<br>".join(html.escape(line) for line in header_lines)
    escaped_from = html.escape(event.message.sender_address)
    escaped_subject = html.escape(event.message.subject)
    escaped_body = html.escape(event.message.text)

    return (
        f"<p>{escaped_header}</p>"
        f'<p><a href="{html.escape(event.gmail_thread_url)}">Open the thread in Gmail</a></p>'
        "<hr>"
        f"<p><b>From:</b> {escaped_from}<br>"
        f"<b>Subject:</b> {escaped_subject}</p>"
        f'<pre style="white-space: pre-wrap; font-family: inherit">{escaped_body}</pre>'
    )


class DiscordNotifier:
    """Posts a short escalation notice to a Discord webhook.

    Never the sender's address and never the body. ``safeguarding_legal`` is
    prefixed URGENT and mentions ``@here``, because that is the one category
    where the cost of nobody looking until tomorrow is not measured in goodwill.
    """

    def __init__(
        self, webhook_url: str, http: Any = None, timeout: float = 10.0
    ) -> None:
        self._webhook_url = (webhook_url or "").strip()
        self._http = http
        self._timeout = timeout

    def notify(self, event: EscalationEvent) -> None:
        if not self._webhook_url:
            logger.warning(
                "No DISCORD_WEBHOOK_URL configured; skipping the post for %s",
                event.message.provider_message_id,
            )
            return

        self._post(build_discord_payload(event))

    def alert(self, alert: OperationalAlert) -> None:
        if not self._webhook_url:
            logger.warning(
                "No DISCORD_WEBHOOK_URL configured; skipping the alert: %s",
                alert.subject,
            )
            return

        self._post(build_alert_payload(alert))

    def _post(self, payload: dict[str, Any]) -> None:
        client = self._http
        if client is None:
            import httpx

            with httpx.Client(timeout=self._timeout) as owned:
                owned.post(self._webhook_url, json=payload)
            return

        client.post(self._webhook_url, json=payload)


def build_discord_payload(event: EscalationEvent) -> dict[str, Any]:
    decision = event.decision
    urgent = decision.category in URGENT_CATEGORIES

    lines = [
        f"{'@here **URGENT**' if urgent else '**Volunteer inbox**'} "
        f"{decision.tier} - {decision.category}",
        f"Language: {decision.language} | Confidence: {decision.confidence:.2f}",
        f"Why: {event.reason}",
    ]
    if event.summary:
        lines.append(f"Summary: {event.summary}")
    lines.append(event.gmail_thread_url)

    payload: dict[str, Any] = {"content": "\n".join(lines)}
    if urgent:
        payload["allowed_mentions"] = {"parse": ["everyone"]}
    return payload


def build_alert_payload(alert: OperationalAlert) -> dict[str, Any]:
    """An operational alert as a Discord post.

    Carries the subject and the detail and nothing else, because there is
    nothing else: an ``OperationalAlert`` holds no mail.
    """
    lines = [
        f"{'@here **URGENT**' if alert.urgent else '**Inbox bot**'} {alert.subject}",
        alert.detail,
    ]
    payload: dict[str, Any] = {"content": "\n".join(lines)}
    if alert.urgent:
        payload["allowed_mentions"] = {"parse": ["everyone"]}
    return payload


class CompositeNotifier:
    """Fans one event out, and never lets one channel's failure lose another's.

    A Discord outage must not cost the captain the forward, and a bad SMTP
    password must not cost the team the Discord post. Every failure is logged
    and reported; none of them propagate, because the alternative is an
    exception in the notification path aborting the run that produced it.
    """

    def __init__(self, notifiers: Sequence[Notifier]) -> None:
        self._notifiers = list(notifiers)

    def notify(self, event: EscalationEvent) -> None:
        for notifier in self._notifiers:
            try:
                notifier.notify(event)
            except Exception as exc:
                logger.error(
                    "Notifier %s failed for %s: %s",
                    type(notifier).__name__,
                    event.message.provider_message_id,
                    type(exc).__name__,
                    exc_info=True,
                )

    def alert(self, alert: OperationalAlert) -> None:
        """Same fan-out and the same isolation as ``notify``.

        An alert about the bot being broken is exactly the moment one of its
        own channels is most likely to be broken too, so one failing must not
        cost the others.
        """
        for notifier in self._notifiers:
            try:
                notifier.alert(alert)
            except Exception as exc:
                logger.error(
                    "Notifier %s failed to alert: %s",
                    type(notifier).__name__,
                    type(exc).__name__,
                    exc_info=True,
                )
