"""Fakes and loaders shared by the email bot tests.

The fake transport is the important one. Every test above the transport runs
against it rather than against a mocked Discovery client, which is what keeps
those tests about behaviour instead of about call chains - and it records enough
that "the marker label was applied last" is assertable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.services.channels.gmail_transport import RawMail
from app.services.email_bot.delivery import DeliveryResult
from app.services.notifier import EscalationEvent
from app.services.triage.protocol import TriageSignals, TriageUnavailable
from tests.fixtures import gmail_payloads as payloads

RECORDED_GMAIL = Path(__file__).parent / "gmail_recorded"
RECORDED_TRIAGE = Path(__file__).parent / "triage_recorded"


def load_gmail(name: str) -> dict[str, Any]:
    return json.loads((RECORDED_GMAIL / name).read_text(encoding="utf-8"))


def load_triage(name: str) -> dict[str, Any]:
    return json.loads((RECORDED_TRIAGE / name).read_text(encoding="utf-8"))


def raw(name: str) -> RawMail:
    return RawMail.from_resource(load_gmail(name))


class FakeTransport:
    """In-memory ``MailTransport``. Has no send method, like the real one.

    Deliberately not a MagicMock: a mock would answer ``send_reply`` too, and
    the absence of that method is the safety property these tests exist to
    protect.
    """

    def __init__(
        self,
        mails: Iterable[Mapping[str, Any]] = (),
        threads: Mapping[str, list[Mapping[str, Any]]] | None = None,
        inbox_address: str = payloads.TEST_INBOX,
    ) -> None:
        self.inbox_address = inbox_address
        self._mails = [RawMail.from_resource(dict(mail)) for mail in mails]
        self._threads = {
            thread_id: [RawMail.from_resource(dict(m)) for m in messages]
            for thread_id, messages in (threads or {}).items()
        }
        self.labels: dict[str, str] = {}
        self.applied: list[tuple[str, tuple[str, ...]]] = []
        self.drafts: dict[str, bytes] = {}
        self.deleted_drafts: list[str] = []
        self.list_calls: list[dict[str, Any]] = []
        self._next_draft = 0

    def list_unprocessed(
        self, *, newer_than_days: int, exclude_label: str, limit: int
    ) -> list[RawMail]:
        self.list_calls.append(
            {
                "newer_than_days": newer_than_days,
                "exclude_label": exclude_label,
                "limit": limit,
            }
        )
        return self._mails[:limit]

    def get_thread(self, thread_id: str) -> list[RawMail]:
        if thread_id in self._threads:
            return self._threads[thread_id]
        return [mail for mail in self._mails if mail.thread_id == thread_id]

    def ensure_labels(self, names: Iterable[str]) -> dict[str, str]:
        for name in names:
            self.labels.setdefault(name, f"Label_{abs(hash(name)) % 10_000}")
        return {name: self.labels[name] for name in names}

    def add_labels(self, message_id: str, label_ids: Iterable[str]) -> None:
        by_id = {value: key for key, value in self.labels.items()}
        self.applied.append(
            (message_id, tuple(by_id.get(label_id, label_id) for label_id in label_ids))
        )

    def create_draft(self, thread_id: str, mime: bytes) -> str:
        self._next_draft += 1
        draft_id = f"draft-{self._next_draft}"
        self.drafts[draft_id] = mime
        return draft_id

    def get_draft(self, draft_id: str) -> RawMail | None:
        return (
            None
            if draft_id in self.deleted_drafts
            else self._mails[0]
            if self._mails
            else None
        )

    def delete_draft(self, draft_id: str) -> None:
        self.deleted_drafts.append(draft_id)
        self.drafts.pop(draft_id, None)

    # ------------------------------------------------------- test assertions

    def draft_bodies(self) -> list[str]:
        """Every drafted body, decoded.

        The MIME the builder produces is transfer-encoded - quoted-printable
        for English, base64 once Vietnamese diacritics are involved - so a
        substring assertion against the raw bytes fails on a soft line break in
        the middle of a word. Decoding here keeps the tests about the copy.
        """
        from email import message_from_bytes

        bodies = []
        for mime in self.drafts.values():
            parsed = message_from_bytes(mime)
            payload = parsed.get_payload(decode=True) or b""
            bodies.append(payload.decode("utf-8", errors="replace"))
        return bodies

    def draft_body(self) -> str:
        """The single drafted body, decoded. Fails loudly if there is not one."""
        bodies = self.draft_bodies()
        assert len(bodies) == 1, f"expected exactly one draft, found {len(bodies)}"
        return bodies[0]

    def labels_for(self, message_id: str) -> list[str]:
        """Every label applied to one message, in the order they were applied."""
        return [
            name
            for applied_id, names in self.applied
            for name in names
            if applied_id == message_id
        ]


class FakeClassifier:
    """Returns a scripted answer per Gmail message id, or raises."""

    name = "fake"

    def __init__(
        self,
        by_message: Mapping[str, TriageSignals | Exception] | None = None,
        default: TriageSignals | Exception | None = None,
        shadow: TriageSignals | None = None,
    ) -> None:
        self._by_message = dict(by_message or {})
        self._default = default
        self.last_shadow = shadow
        self.seen: list[str] = []

    def classify(self, message: Any) -> TriageSignals:
        self.seen.append(message.provider_message_id)
        answer = self._by_message.get(message.provider_message_id, self._default)
        if answer is None:
            raise TriageUnavailable("no scripted answer for this message")
        if isinstance(answer, Exception):
            raise answer
        return answer


class RecordingNotifier:
    """Captures escalation events instead of sending anything."""

    def __init__(self) -> None:
        self.events: list[EscalationEvent] = []

    def notify(self, event: EscalationEvent) -> None:
        self.events.append(event)

    @property
    def categories(self) -> list[str]:
        return [event.decision.category for event in self.events]


class FakeSendSink:
    """Stands in for E3's SendSink so E1 can prove it is never reached."""

    action = "sent"

    def __init__(self) -> None:
        self.delivered: list[Any] = []

    def deliver(self, reply: Any) -> DeliveryResult:
        self.delivered.append(reply)
        return DeliveryResult(action="sent", gmail_message_id_out="sent-1")


class FakeBotService:
    """An async ``chat`` that returns or raises what a test scripted."""

    def __init__(self, answer: Any = None, error: Exception | None = None) -> None:
        self._answer = answer or {
            "response": "No certificate is needed to help as a teaching assistant.",
            "context_used": 2,
            "confidence": 0.82,
            "sources": ["kb-doc"],
        }
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        message: str,
        user_context: dict | None = None,
        *,
        channel: str = "web",
        language: str = "en",
    ) -> dict[str, Any]:
        self.calls.append(
            {"message": message, "channel": channel, "language": language}
        )
        if self._error is not None:
            raise self._error
        return self._answer


def signals(
    category: str = "signup",
    language: str = "en",
    confidence: float = 0.9,
    asks_for_human: bool = False,
    money: bool = False,
    classifier: str = "fake",
) -> TriageSignals:
    return TriageSignals(
        category=category,
        language=language,
        confidence=confidence,
        asks_for_human=asks_for_human,
        mentions_money_or_commitment=money,
        classifier=classifier,
    )
