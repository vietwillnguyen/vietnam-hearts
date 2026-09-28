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
        drafts: dict[str, bytes] | None = None,
    ) -> None:
        self.inbox_address = inbox_address
        self._mails = [RawMail.from_resource(dict(mail)) for mail in mails]
        self._threads = {
            thread_id: [RawMail.from_resource(dict(m)) for m in messages]
            for thread_id, messages in (threads or {}).items()
        }
        self.labels: dict[str, str] = {}
        self.applied: list[tuple[str, tuple[str, ...]]] = []
        # Carried over between polls in a test that runs more than one, because
        # Gmail remembers a draft across requests and a fresh fake would
        # otherwise report it as deleted - which reconciliation would believe.
        self.drafts: dict[str, bytes] = dict(drafts or {})
        self.deleted_drafts: list[str] = []
        self.list_calls: list[dict[str, Any]] = []
        self._next_draft = len(self.drafts)
        self._draft_threads: dict[str, str] = dict.fromkeys(self.drafts, "thread-1")

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
        self._draft_threads[draft_id] = thread_id
        return draft_id

    def get_draft(self, draft_id: str) -> RawMail | None:
        """The draft as Gmail returns it: the message itself, DRAFT-labelled.

        The earlier version of this handed back the first *inbound* mail as a
        stand-in. It answered "is it still there" correctly and everything else
        wrongly, and that is not a hypothetical objection: a fake that never
        produced a DRAFT-labelled message From the inbox is exactly what hid
        the bug where the bot read its own pending draft as a human reply.
        """
        if draft_id in self.deleted_drafts or draft_id not in self.drafts:
            return None
        return RawMail.from_resource(
            payloads.message(
                message_id=f"draft-msg-{draft_id}",
                thread_id=self._draft_threads.get(draft_id, "thread-1"),
                from_address=f"Vietnam Hearts <{self.inbox_address}>",
                to_address=payloads.TEST_SENDER,
                text=self._draft_body(draft_id),
                label_ids=("DRAFT",),
            )
        )

    def get_message(self, message_id: str) -> RawMail | None:
        for mail in self._mails:
            if mail.id == message_id:
                return mail
        return None

    def _draft_body(self, draft_id: str) -> str:
        from email import message_from_bytes

        parsed = message_from_bytes(self.drafts[draft_id])
        payload = parsed.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")

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


class SendingFakeTransport(FakeTransport):
    """``FakeTransport`` plus the send path E3 introduces.

    Separate from ``FakeTransport`` on purpose: every test written before this
    phase asserts against a transport that *cannot* send, and that property is
    worth keeping for them rather than quietly giving every fake a send method.
    """

    def __init__(
        self, *args, send_fails: bool = False, raise_on_send: bool = False, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[bytes] = []
        self._send_fails = send_fails
        self._raise_on_send = raise_on_send
        self._next_sent = 0

    def send_reply(self, thread_id: str, mime: bytes) -> str:
        if self._raise_on_send:
            raise AssertionError(
                "send_reply must not be reached while the mode is off or draft"
            )
        if self._send_fails:
            raise RuntimeError("Gmail refused messages.send")
        self._next_sent += 1
        self.sent.append(mime)
        return f"sent-{self._next_sent}"

    def sent_bodies(self) -> list[str]:
        """Every sent body, decoded, for the same reason draft_bodies exists."""
        from email import message_from_bytes

        bodies = []
        for mime in self.sent:
            parsed = message_from_bytes(mime)
            payload = parsed.get_payload(decode=True) or b""
            bodies.append(payload.decode("utf-8", errors="replace"))
        return bodies
