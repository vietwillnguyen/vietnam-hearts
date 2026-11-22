"""The one place inbox conversation content is written, read and erased.

Decided 2026-11-22: the bot keeps a full record of each conversation, with no
expiry, under four safeguards - disclosed in the privacy policy, deletable on
request, readable by admins only with every view recorded, and never present in
logs, Discord notices or Sentry. This module is where the second and third are
kept and where the fourth is easiest to break, so everything that touches a
subject, a body or an address goes through ``ConversationStore`` and nothing
else decrypts those columns.

What is kept: an inbound mail's subject and plain-text body (the HTML fallback
is already stripped to text by ``extract_text``), cut at ``SUBJECT_MAX_CHARS``
and ``BODY_MAX_CHARS``, and its sender's address; the bot's own reply text.
What is not: HTML, headers, attachments (``extract_text`` never fetches them),
and anything from mail the guards skip before triage, which is automated mail,
our own mail, or mail with no usable sender - not a conversation with a person.

``ConversationStore`` is a protocol so the engine can move into the shared
package with this interface and a different backing store behind it.
``SqlConversationStore`` is the SQLAlchemy implementation this app uses. It
logs nothing, and its errors carry row ids only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.models import ConversationView, Message
from app.services.channels.base import IncomingMessage
from app.utils.field_crypto import FieldCipher, cipher_from_env

# Long enough for any real subject line; RFC 5322 itself recommends 78.
SUBJECT_MAX_CHARS = 500
# Long enough for the longest question a person types, short enough that a
# forwarded newsletter or a pasted document does not become a large stored copy.
BODY_MAX_CHARS = 20_000
TRUNCATION_MARKER = "\n\n[Stored copy truncated at {limit} characters.]"

SUBJECT_CONTEXT = "messages.subject_encrypted"
BODY_CONTEXT = "messages.body_encrypted"
ADDRESS_CONTEXT = "messages.sender_address_encrypted"


@dataclass(frozen=True)
class StoredMessage:
    """One row of a conversation, with its content decrypted."""

    id: int
    direction: str
    action: str
    category: str | None
    tier: str | None
    language: str | None
    draft_outcome: str | None
    created_at: datetime | None
    # Kept out of the repr so a log line or a Sentry frame shows ids only.
    subject: str | None = field(repr=False)
    body: str | None = field(repr=False)
    sender_address: str | None = field(repr=False)
    erased: bool = False


@dataclass(frozen=True)
class Viewer:
    """Who is reading: the signed-in admin's id and address, nothing more."""

    id: str
    email: str | None


@dataclass(frozen=True)
class ViewRecord:
    viewer_id: str
    viewer_email: str | None
    viewed_at: datetime | None


@runtime_checkable
class ConversationStore(Protocol):
    """Content in and out of the conversation record, and nothing else."""

    def keep_inbound(self, row: Message, message: IncomingMessage) -> None:
        """Put the mail's subject, body and address on its (uncommitted) row."""

    def keep_outbound(self, row: Message, text: str) -> None:
        """Put the bot's reply text on its (uncommitted) row."""

    def outbound_text(self, row: Message) -> str:
        """The bot's text on an outbound row; empty if erased."""

    def messages(self, conversation_id: int) -> list[StoredMessage]:
        """Every row of one conversation, oldest first, decrypted."""

    def record_view(self, conversation_id: int, viewer: Viewer) -> None:
        """Commit a record that ``viewer`` opened this conversation, now."""

    def views(
        self, conversation_id: int, limit: int = 10
    ) -> tuple[int, list[ViewRecord]]:
        """How many times this conversation was opened, and the latest views."""


class SqlConversationStore:
    """``ConversationStore`` over the ``messages`` table.

    The cipher is resolved on first use rather than at construction, so the
    read-only dashboard routes that never touch content work without the key.
    The pipeline passes one in, so it fails before touching Gmail instead.
    """

    def __init__(self, db: Session, cipher: FieldCipher | None = None) -> None:
        self.db = db
        self._cipher = cipher

    @property
    def cipher(self) -> FieldCipher:
        if self._cipher is None:
            self._cipher = cipher_from_env()
        return self._cipher

    # ------------------------------------------------------------- writing

    def keep_inbound(self, row: Message, message: IncomingMessage) -> None:
        if row.content_erased_at is not None:
            # Deleted on request while the mail was still unhandled. A retry
            # must not quietly put the text back.
            return
        cipher = self.cipher
        row.subject_encrypted = cipher.encrypt(
            (message.subject or "")[:SUBJECT_MAX_CHARS], SUBJECT_CONTEXT
        )
        row.body_encrypted = cipher.encrypt(_cut(message.text or ""), BODY_CONTEXT)
        row.sender_address_encrypted = (
            cipher.encrypt(message.sender_address, ADDRESS_CONTEXT)
            if message.sender_address
            else None
        )

    def keep_outbound(self, row: Message, text: str) -> None:
        row.body_encrypted = self.cipher.encrypt(text, BODY_CONTEXT)

    # ------------------------------------------------------------- reading

    def outbound_text(self, row: Message) -> str:
        if row.body_encrypted is None:
            return ""
        return self.cipher.decrypt(row.body_encrypted, BODY_CONTEXT, ids=_ids(row))

    def messages(self, conversation_id: int) -> list[StoredMessage]:
        rows = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
            .all()
        )
        return [self._decrypted(row) for row in rows]

    # ------------------------------------------------------------- viewing

    def record_view(self, conversation_id: int, viewer: Viewer) -> None:
        # Committed on its own, before anything is decrypted, so a view that
        # happened is on the record even if what follows fails.
        self.db.add(
            ConversationView(
                conversation_id=conversation_id,
                viewer_id=viewer.id,
                viewer_email=viewer.email,
            )
        )
        self.db.commit()

    def views(
        self, conversation_id: int, limit: int = 10
    ) -> tuple[int, list[ViewRecord]]:
        query = self.db.query(ConversationView).filter(
            ConversationView.conversation_id == conversation_id
        )
        recent = (
            query.order_by(
                ConversationView.viewed_at.desc(), ConversationView.id.desc()
            )
            .limit(limit)
            .all()
        )
        return query.count(), [
            ViewRecord(row.viewer_id, row.viewer_email, row.viewed_at) for row in recent
        ]

    def _decrypted(self, row: Message) -> StoredMessage:
        has_content = any(
            (row.subject_encrypted, row.body_encrypted, row.sender_address_encrypted)
        )
        cipher = self.cipher if has_content else None
        ids = _ids(row)

        def read(token: str | None, context: str) -> str | None:
            if token is None or cipher is None:
                return None
            return cipher.decrypt(token, context, ids=ids)

        return StoredMessage(
            id=row.id,
            direction=row.direction,
            action=row.action,
            category=row.category,
            tier=row.tier,
            language=row.language,
            draft_outcome=row.draft_outcome,
            created_at=row.created_at,
            subject=read(row.subject_encrypted, SUBJECT_CONTEXT),
            body=read(row.body_encrypted, BODY_CONTEXT),
            sender_address=read(row.sender_address_encrypted, ADDRESS_CONTEXT),
            erased=row.content_erased_at is not None,
        )


def _cut(body: str) -> str:
    if len(body) <= BODY_MAX_CHARS:
        return body
    return body[:BODY_MAX_CHARS] + TRUNCATION_MARKER.format(limit=BODY_MAX_CHARS)


def _ids(row: Message) -> str:
    return f"message id={row.id}, conversation id={row.conversation_id}"
