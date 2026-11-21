"""Channel-neutral shapes the inbound answer engine works in.

Everything above a transport - dedupe, the conversation store, triage, the
category gate, retrieval, escalation and the audit rows - sees only these two
dataclasses. Email fills them from the Gmail API; Messenger will fill them
from its webhook payload when it moves onto the same engine.

Both are frozen: a pipeline that could rewrite the message it is triaging
would make the audit row a guess about what was actually classified.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

EMAIL_CHANNEL = "email"


@dataclass(frozen=True)
class IncomingMessage:
    """One inbound message, normalised.

    ``sender_address`` is the one field here that must never be persisted or
    logged. It exists because a reply has to be addressed to somebody, and it
    lives in memory for the length of one pipeline step. ``sender_key`` is the
    durable form: a SHA-256 of the lower-cased address, which the per-sender
    cap and the grouping can use without storing who wrote in.
    """

    channel: str
    thread_key: str
    provider_message_id: str
    sender_key: str
    sender_address: str
    subject: str
    text: str
    rfc_message_id: str
    references: tuple[str, ...] = ()
    received_at: datetime | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    label_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutboundReply:
    """One reply the bot wants delivered, before a sink decides how.

    ``kind`` records which answer path produced the text, which is what the
    draft-acceptance metric is grouped by in E2. It is not a routing decision:
    the sink chooses draft or send from the mode and the language.
    """

    thread_key: str
    to_address: str
    subject: str
    in_reply_to: str
    references: tuple[str, ...]
    text: str
    language: str
    kind: str


class ChannelAdapter(Protocol):
    """What the pipeline needs from a channel, whatever the platform.

    Deliberately narrow: listing, parsing and labelling are common to every
    channel, while drafts are an email concept the engine never learns about
    (it goes through a ReplySink instead).
    """

    channel: str

    def list_new(self, *, limit: int) -> list[object]: ...

    def parse(self, raw: object) -> IncomingMessage: ...

    def label(self, message_id: str, names: list[str]) -> None: ...
