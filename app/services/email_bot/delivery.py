"""The delivery mode gate: the only email-specific concept above the transport.

Three modes and one question: given the mode and the reply's language, which
sink - if any - may deliver this? Keeping that in one pure function is what
makes "nothing sends while the mode is off or draft" a property of a table
rather than a claim about every call site.

In this phase there is only a ``DraftSink``, so ``auto`` resolves to it too.
That is not a stub: with no send method on the transport there is nothing for
``auto`` to mean yet, and E3 adds ``SendSink`` as the single caller of
``send_reply`` without changing the shape of this decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.services.channels.base import OutboundReply
from app.utils.logging_config import get_logger

logger = get_logger("email_bot_delivery")


class DeliveryMode(StrEnum):
    OFF = "off"
    DRAFT = "draft"
    AUTO = "auto"


def parse_mode(value: str | None) -> DeliveryMode:
    """Anything unrecognised reads as ``off``.

    Fail-closed parsing rather than a raise: a typo in the dashboard field must
    stop the bot, not crash the poll, and certainly not fall through to a mode
    that sends. ``EMAIL_BOT_MODE=aut`` is a mistake whose safe reading is
    "do nothing".
    """
    candidate = (value or "").strip().lower()
    try:
        return DeliveryMode(candidate)
    except ValueError:
        if candidate:
            logger.warning("Unknown EMAIL_BOT_MODE=%r; treating it as off", candidate)
        return DeliveryMode.OFF


@dataclass(frozen=True)
class DeliveryResult:
    """What a sink did, for the audit row and the run counters."""

    action: str
    gmail_draft_id: str | None = None
    gmail_message_id_out: str | None = None


class ReplySink(Protocol):
    def deliver(self, reply: OutboundReply) -> DeliveryResult: ...


class DraftSink:
    """Saves the reply as a Gmail draft inside its thread. Sends nothing.

    The adapter it holds has no send path at all in this phase, so this class
    could not send even if asked to.
    """

    action = "drafted"

    def __init__(self, adapter) -> None:
        self._adapter = adapter

    def deliver(self, reply: OutboundReply) -> DeliveryResult:
        draft_id = self._adapter.draft(reply)
        return DeliveryResult(action=self.action, gmail_draft_id=draft_id)


def choose_sink(
    mode: DeliveryMode,
    language: str,
    auto_languages: frozenset[str],
    sinks: dict[str, ReplySink],
) -> ReplySink | None:
    """Which sink may deliver this reply, or None for "deliver nothing".

    ``off`` yields None, which is the kill switch: the pipeline still labels and
    still escalates, because knowing about a safeguarding mail is not something
    a delivery switch should be able to turn off, but the sender gets nothing.

    ``auto`` falls back to the draft sink whenever the language is not cleared
    for automatic sending, or whenever no send sink exists - which is every case
    in this phase.
    """
    if mode is DeliveryMode.OFF:
        return None

    draft = sinks.get("draft")
    if mode is DeliveryMode.DRAFT:
        return draft

    send = sinks.get("send")
    if send is None:
        return draft
    if language not in auto_languages:
        logger.info(
            "Language %s is not in EMAIL_BOT_AUTO_LANGUAGES; drafting instead", language
        )
        return draft
    return send
