"""The only module in the codebase that talks to the Gmail API.

Everything Gmail-shaped stops here. Above it the pipeline sees ``RawMail`` and
the ``MailTransport`` protocol, which is what lets the IMAP fallback the design
documents drop in without anything else changing, and what lets every test
above this layer run against a fake rather than a mocked Discovery client.

``send_reply`` is the one method here that can put mail in front of a member of
the public, and ``SendSink`` is its only caller. Everything that bounds it - the
delivery mode, the per-language gate, the daily and per-sender caps, the
one-reply-per-thread rule - is decided before this module is reached, because a
transport is the wrong place to be making policy. The test for this file asserts
that ``send_reply`` is the *only* send-shaped name on the class, so a second
outbound path cannot appear quietly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from googleapiclient.discovery import build

from app.utils.logging_config import get_logger

logger = get_logger("gmail_transport")

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Label names the bot owns. Everything under VH-Bot/ is ours to create and to
# apply; nothing else in the mailbox is touched.
LABEL_SEEN = "VH-Bot/Seen"
LABEL_DRAFTED = "VH-Bot/Drafted"
LABEL_SENT = "VH-Bot/Sent"
LABEL_ESCALATED = "VH-Bot/Escalated"
LABEL_SKIPPED = "VH-Bot/Skipped"
LABEL_PAUSED = "VH-Bot/Paused"

OUTCOME_LABELS = (
    LABEL_SEEN,
    LABEL_DRAFTED,
    LABEL_SENT,
    LABEL_ESCALATED,
    LABEL_SKIPPED,
    LABEL_PAUSED,
)


def category_label(category: str) -> str:
    """The per-category label under VH-Bot/, e.g. ``VH-Bot/signup``."""
    return f"VH-Bot/{category}"


class GmailAuthRevoked(RuntimeError):
    """The stored refresh token no longer works.

    Almost always a password change on the account or a revoked grant at
    myaccount.google.com, neither of which a retry fixes. E4 raises this from
    the refresh path and flips ``EMAIL_BOT_MODE`` to ``off`` so the run stops
    instead of failing twice a day in silence; the fix is the re-consent
    runbook in ``docs/GMAIL_BOT_SETUP.md``.
    """


@dataclass(frozen=True)
class RawMail:
    """One Gmail message as the API returned it, plus the bits used everywhere.

    ``payload`` is the whole ``users.messages.get(format="full")`` resource
    rather than its ``payload`` sub-object, because the adapter needs
    ``internalDate`` and the label ids alongside the MIME tree. Keeping the
    untouched resource is also what makes the recorded fixtures worth
    committing: they are the real contract, not our summary of it.
    """

    id: str
    thread_id: str
    label_ids: tuple[str, ...]
    payload: Mapping[str, Any]

    @classmethod
    def from_resource(cls, resource: Mapping[str, Any]) -> RawMail:
        return cls(
            id=resource.get("id", ""),
            thread_id=resource.get("threadId", ""),
            label_ids=tuple(resource.get("labelIds") or ()),
            payload=resource,
        )

    def headers(self) -> dict[str, str]:
        """Every header, keyed by lower-cased name.

        Gmail returns headers as a list of ``{"name": ..., "value": ...}``
        with the original casing, and a mail can legitimately repeat a header.
        The first occurrence wins, which is what a reader would take as the
        effective value.
        """
        collected: dict[str, str] = {}
        entries = (self.payload.get("payload") or {}).get("headers") or []
        for entry in entries:
            name = (entry.get("name") or "").lower()
            if name and name not in collected:
                collected[name] = entry.get("value") or ""
        return collected

    def header(self, name: str) -> str:
        return self.headers().get(name.lower(), "")


class MailTransport(Protocol):
    """What the adapter needs from a mailbox, Gmail or otherwise.

    The IMAP fallback in the design implements this same protocol over
    ``X-GM-THRID`` and ``X-GM-LABELS``, so nothing above the transport changes
    if the OAuth consent is ever refused.
    """

    inbox_address: str

    def list_unprocessed(
        self, *, newer_than_days: int, exclude_label: str, limit: int
    ) -> list[RawMail]: ...

    def get_thread(self, thread_id: str) -> list[RawMail]: ...

    def ensure_labels(self, names: Iterable[str]) -> dict[str, str]: ...

    def add_labels(self, message_id: str, label_ids: Iterable[str]) -> None: ...

    def create_draft(self, thread_id: str, mime: bytes) -> str: ...

    def get_draft(self, draft_id: str) -> RawMail | None: ...

    def delete_draft(self, draft_id: str) -> None: ...

    # SendSink is the only caller.
    def send_reply(self, thread_id: str, mime: bytes) -> str: ...


def build_gmail_service(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: Iterable[str] = (GMAIL_MODIFY_SCOPE,),
):
    """A Discovery-built Gmail client that refreshes its own access token.

    Deliberately not a service account: the grant belongs to the volunteer
    inbox itself, obtained once by the captain through
    ``scripts/gmail_oauth_consent.py``, and it is revocable from his own
    account page without touching the SMTP password the outbound mail uses.

    ``cache_discovery=False`` because the default file cache warns on every
    build under a read-only or ephemeral filesystem, which Cloud Run is.
    """
    # Imported here rather than at module scope so that importing this module -
    # which the pure tests above the transport do transitively - does not pull
    # in the OAuth machinery.
    from google.oauth2.credentials import Credentials

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=GOOGLE_TOKEN_URI,
        scopes=list(scopes),
    )
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


class GmailTransport:
    """``MailTransport`` over the Gmail API.

    Stateless between runs on purpose. With two polls a day a stored
    ``historyId`` buys nothing, so listing is "inbox, last N days, not yet
    carrying the marker label": a run that dies halfway leaves its unfinished
    mail unlabelled and the next run picks it up, and the unique index on the
    Gmail message id turns any overlap into a no-op.
    """

    def __init__(self, service, inbox_address: str = "") -> None:
        self._service = service
        self._inbox_address = inbox_address
        self._label_ids: dict[str, str] = {}

    @classmethod
    def from_credentials(
        cls, client_id: str, client_secret: str, refresh_token: str
    ) -> GmailTransport:
        return cls(build_gmail_service(client_id, client_secret, refresh_token))

    @property
    def inbox_address(self) -> str:
        """The address the grant was issued for, read once and remembered.

        Resolved from the grant rather than from configuration so that a token
        for the wrong mailbox cannot masquerade as the volunteer inbox: every
        "is this us?" guard compares against whatever this actually is.
        """
        if not self._inbox_address:
            profile = self._service.users().getProfile(userId="me").execute()
            self._inbox_address = (profile or {}).get("emailAddress", "")
        return self._inbox_address

    def list_unprocessed(
        self,
        *,
        newer_than_days: int = 7,
        exclude_label: str = LABEL_SEEN,
        limit: int = 20,
    ) -> list[RawMail]:
        """Inbox mail from the last N days that the bot has not marked seen.

        The age window bounds the self-healing rather than enabling it: mail
        that stays unlabelled for longer than this - through an outage or a
        revoked grant - is never triaged or escalated by the bot and stays in
        the inbox for a human. The re-consent runbook ends with the hand
        triage that covers exactly that gap.
        """
        query = f"in:inbox newer_than:{newer_than_days}d -label:{exclude_label}"
        listed = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        ) or {}

        mails: list[RawMail] = []
        for stub in (listed.get("messages") or [])[:limit]:
            message_id = stub.get("id")
            if not message_id:
                continue
            mails.append(self._get_message(message_id))
        return mails

    def get_thread(self, thread_id: str) -> list[RawMail]:
        """Every message currently in the thread, oldest first as Gmail returns them.

        Read before the bot acts, because the "never talk over a human" guard
        cannot be answered from our own audit rows alone: a reply the captain
        sent by hand exists only in Gmail.
        """
        thread = (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        ) or {}
        return [
            RawMail.from_resource(resource) for resource in thread.get("messages") or []
        ]

    def ensure_labels(self, names: Iterable[str]) -> dict[str, str]:
        """Map label names to ids, creating any that are missing.

        Called once per run with every label the run might apply, so the
        per-message path never has to decide whether a label exists. Gmail
        rejects a duplicate name with 409, which is treated as "somebody else
        created it first" and resolved by re-reading the list.
        """
        wanted = [name for name in names if name]
        if not wanted:
            return {}

        existing = self._list_labels()
        resolved: dict[str, str] = {}
        for name in wanted:
            if name in existing:
                resolved[name] = existing[name]
                continue
            created = self._create_label(name)
            if created is None:
                existing = self._list_labels()
                if name in existing:
                    resolved[name] = existing[name]
                continue
            existing[name] = created
            resolved[name] = created

        self._label_ids.update(resolved)
        return resolved

    def add_labels(self, message_id: str, label_ids: Iterable[str]) -> None:
        ids = [label_id for label_id in label_ids if label_id]
        if not ids:
            return
        self._service.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": ids}
        ).execute()

    def create_draft(self, thread_id: str, mime: bytes) -> str:
        """Save a reply as a draft inside the thread and return its draft id.

        ``threadId`` on the message is what keeps the draft in the
        conversation rather than starting a new one; Gmail also requires the
        References/In-Reply-To headers the MIME already carries to agree with
        it, which is why the builder and this call are tested together.
        """
        created = (
            self._service.users()
            .drafts()
            .create(
                userId="me",
                body={
                    "message": {
                        "threadId": thread_id,
                        "raw": _b64url(mime),
                    }
                },
            )
            .execute()
        ) or {}
        return created.get("id", "")

    def send_reply(self, thread_id: str, mime: bytes) -> str:
        """Send a reply into its thread and return the sent Gmail message id.

        The returned id is what the audit row stores, and what the next run's
        "never talk over a human" check subtracts so the bot's own reply is not
        mistaken for the captain's. A send whose id is lost would make the bot
        pause its own thread.

        ``threadId`` keeps the reply in the conversation rather than starting a
        new one. Gmail also requires the ``In-Reply-To`` and ``References`` the
        MIME already carries to agree with it, which is why the builder and this
        call are tested together.
        """
        sent = (
            self._service.users()
            .messages()
            .send(
                userId="me",
                body={"threadId": thread_id, "raw": _b64url(mime)},
            )
            .execute()
        ) or {}
        return sent.get("id", "")

    def get_draft(self, draft_id: str) -> RawMail | None:
        """The draft as it stands now, or None once it is gone.

        A missing draft is the signal the draft-acceptance metric reads: the
        captain either sent it or deleted it, and E2's reconciliation decides
        which by looking for a matching sent message in the thread.
        """
        from googleapiclient.errors import HttpError

        try:
            draft = (
                self._service.users()
                .drafts()
                .get(userId="me", id=draft_id, format="full")
                .execute()
            ) or {}
        except HttpError as exc:
            if getattr(exc, "status_code", None) == 404 or "404" in str(exc):
                return None
            raise
        message = draft.get("message")
        if not message:
            return None
        return RawMail.from_resource(message)

    def delete_draft(self, draft_id: str) -> None:
        """Remove the bot's own outstanding draft.

        Called when a thread turns out to have a human reply in it: leaving a
        stale bot draft under a conversation the captain has taken over is how
        an answer that is no longer true gets sent by accident.
        """
        from googleapiclient.errors import HttpError

        try:
            self._service.users().drafts().delete(userId="me", id=draft_id).execute()
        except HttpError as exc:
            if getattr(exc, "status_code", None) == 404 or "404" in str(exc):
                return
            raise

    def _get_message(self, message_id: str) -> RawMail:
        resource = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        ) or {}
        return RawMail.from_resource(resource)

    def _list_labels(self) -> dict[str, str]:
        listed = self._service.users().labels().list(userId="me").execute() or {}
        return {
            label.get("name", ""): label.get("id", "")
            for label in listed.get("labels") or []
            if label.get("name")
        }

    def _create_label(self, name: str) -> str | None:
        from googleapiclient.errors import HttpError

        try:
            created = (
                self._service.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                .execute()
            ) or {}
        except HttpError as exc:
            if getattr(exc, "status_code", None) == 409 or "409" in str(exc):
                return None
            raise
        return created.get("id")


def _b64url(mime: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(mime).decode("ascii")
