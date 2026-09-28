"""``GmailAdapter``: the email channel's view of the engine's contract.

Sits between the transport and the pipeline and does the two things the engine
must not know about - Gmail's MIME shape and Gmail's labels. It never forwards
anything: the escalation forward is a notifier concern over the existing SMTP
path, so the Gmail grant is never the thing that puts mail in front of a
person.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from app.services.channels.base import EMAIL_CHANNEL, IncomingMessage, OutboundReply
from app.services.channels.gmail_transport import (
    LABEL_SEEN,
    MailTransport,
    RawMail,
    category_label,
)
from app.services.channels.mail_builder import build_reply, extract_text
from app.services.channels.mail_guards import normalise_address, sender_key
from app.utils.logging_config import get_logger

logger = get_logger("gmail_adapter")

GMAIL_THREAD_URL = "https://mail.google.com/mail/u/0/#inbox/{thread_id}"


class GmailAdapter:
    """Lists, parses, labels, drafts and sends.

    It never forwards: the escalation forward is a notifier concern over the
    existing SMTP path, so the Gmail grant is never the thing that puts an
    escalation in front of the captain.
    """

    channel = EMAIL_CHANNEL

    def __init__(
        self, transport: MailTransport, from_name: str = "Vietnam Hearts"
    ) -> None:
        self._transport = transport
        self._from_name = from_name

    @property
    def inbox_address(self) -> str:
        return self._transport.inbox_address

    def list_new(self, *, limit: int = 20, newer_than_days: int = 7) -> list[RawMail]:
        return self._transport.list_unprocessed(
            newer_than_days=newer_than_days,
            exclude_label=LABEL_SEEN,
            limit=limit,
        )

    def get_thread(self, thread_id: str) -> list[RawMail]:
        return self._transport.get_thread(thread_id)

    def parse(self, raw: RawMail) -> IncomingMessage:
        """One Gmail message resource as the engine's ``IncomingMessage``.

        Every field is derived defensively, because this is where real mail
        meets our assumptions: a missing ``Message-ID`` falls back to the Gmail
        id (so threading still has something stable to reference), a
        non-ASCII subject is already decoded for us by the API, and a body
        with no ``text/plain`` part comes through the HTML fallback. An empty
        body is left empty rather than papered over - the pipeline escalates it.
        """
        headers = raw.headers()
        from_address = normalise_address(headers.get("from"))
        rfc_message_id = (headers.get("message-id") or "").strip()

        return IncomingMessage(
            channel=EMAIL_CHANNEL,
            thread_key=raw.thread_id,
            provider_message_id=raw.id,
            sender_key=sender_key(from_address),
            sender_address=from_address,
            subject=(headers.get("subject") or "").strip(),
            text=extract_text(raw.payload),
            # Falling back to a Gmail-derived id keeps In-Reply-To populated
            # with something the thread actually contains. A reply with no
            # In-Reply-To at all is the case that renders outside the
            # conversation in strict clients.
            rfc_message_id=rfc_message_id or f"<gmail-{raw.id}@mail.gmail.com>",
            references=_parse_references(headers.get("references")),
            received_at=_received_at(raw),
            headers=headers,
            label_ids=raw.label_ids,
        )

    def ensure_labels(self, names: Iterable[str]) -> dict[str, str]:
        return self._transport.ensure_labels(names)

    def label(self, message_id: str, names: Sequence[str]) -> None:
        """Apply label names to one message, creating any that do not exist yet.

        Names rather than ids at this seam because everything above cares about
        ``VH-Bot/Escalated``, not about an opaque ``Label_17``. Labels are the
        human-visible mirror of the audit rows and are safe to re-apply, which
        is what makes a re-listed message a no-op.
        """
        wanted = [name for name in names if name]
        if not wanted:
            return
        ids = self._transport.ensure_labels(wanted)
        self._transport.add_labels(
            message_id, [ids[name] for name in wanted if name in ids]
        )

    def category_label(self, category: str) -> str:
        return category_label(category)

    def draft(self, reply: OutboundReply) -> str:
        """Save the reply as a Gmail draft inside its thread; return the draft id."""
        mime = build_reply(
            reply, from_address=self.inbox_address, from_name=self._from_name
        )
        return self._transport.create_draft(reply.thread_key, mime)

    def send(self, reply: OutboundReply) -> str:
        """Send the reply in-thread; return the sent Gmail message id.

        Reached only through ``SendSink``, which is reached only when the mode
        is ``auto``, the language is cleared for automatic sending, and the caps
        allow it. This method asks none of those questions, which is why they
        are all answered before it.
        """
        mime = build_reply(
            reply, from_address=self.inbox_address, from_name=self._from_name
        )
        return self._transport.send_reply(reply.thread_key, mime)

    def delete_draft(self, draft_id: str) -> None:
        self._transport.delete_draft(draft_id)

    def get_draft(self, draft_id: str) -> RawMail | None:
        return self._transport.get_draft(draft_id)

    def get_message(self, message_id: str) -> RawMail | None:
        """One message by id, for the weekly sampling that stores nothing."""
        return self._transport.get_message(message_id)

    def thread_url(self, thread_id: str) -> str:
        return GMAIL_THREAD_URL.format(thread_id=thread_id)


def _parse_references(raw: str | None) -> tuple[str, ...]:
    """Split a ``References`` header into its message ids.

    The header is whitespace-separated angle-addr tokens, but real mail arrives
    folded across lines and occasionally comma-separated, so both separators
    are accepted and anything that is not an angle-addr is dropped.
    """
    if not raw:
        return ()
    tokens = raw.replace(",", " ").split()
    return tuple(
        token for token in tokens if token.startswith("<") and token.endswith(">")
    )


def _received_at(raw: RawMail) -> datetime:
    """When Gmail says it received the message.

    ``internalDate`` is epoch milliseconds and is Gmail's own receipt time,
    which is more trustworthy than the sender's ``Date`` header for ordering
    and for the age window. A missing or unparseable value falls back to now,
    so a malformed resource cannot abort a run.
    """
    internal = raw.payload.get("internalDate")
    try:
        return datetime.fromtimestamp(int(internal) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)
