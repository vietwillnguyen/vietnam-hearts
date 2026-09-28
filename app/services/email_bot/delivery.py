"""The delivery mode gate: the only email-specific concept above the transport.

Three modes and one question: given the mode and the reply's language, which
sink - if any - may deliver this? Keeping that in one pure function is what
makes "nothing sends while the mode is off or draft" a property of a table
rather than a claim about every call site.

``SendSink`` is the single caller of the transport's ``send_reply``, and it is
reached only through ``choose_sink``. So every question that bounds an automatic
send - the mode, the language, the daily and per-sender caps - is answered in
one function, and a reply that fails any of them is drafted rather than dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.services.channels.base import OutboundReply
from app.services.email_bot.caps import CapDecision
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


class SendSink:
    """Sends the reply in-thread. The only caller of ``send_reply``.

    Holds the caps because "may this particular reply go out" is a question
    about this sender and this day, which ``choose_sink`` cannot answer from the
    mode and the language alone. It records each send against them as it goes,
    so the caps hold within a single run and not merely between runs - otherwise
    one poll at the per-run cap could send twenty replies to the same person.
    """

    action = "sent"

    def __init__(self, adapter, caps) -> None:
        self._adapter = adapter
        self._caps = caps

    def allows(self, sender_key: str) -> CapDecision:
        return self._caps.allows(sender_key)

    def deliver(self, reply: OutboundReply) -> DeliveryResult:
        message_id = self._adapter.send(reply)
        self._caps.record_send(_sender_key(reply))
        return DeliveryResult(action=self.action, gmail_message_id_out=message_id)


def _sender_key(reply: OutboundReply) -> str:
    """The sender key for a reply, derived rather than carried.

    ``OutboundReply`` holds the address because a reply has to be addressed to
    somebody; the caps need the non-identifying form, and deriving it here keeps
    the hash out of the reply object and out of anything that logs one.
    """
    from app.services.channels.mail_guards import sender_key

    return sender_key(reply.to_address)


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
    sender_key: str = "",
    on_capped: Callable[[CapDecision], None] | None = None,
) -> ReplySink | None:
    """Which sink may deliver this reply, or None for "deliver nothing".

    Every reason not to send resolves to the draft sink rather than to None.
    A capacity limit, an unsigned-off language or a missing send path are all
    reasons to hold the reply for review, not reasons to drop it: the captain
    still gets it one click away, and the run records that it happened.

    The single exception is ``off``, which yields None. That is the kill switch,
    and it means the sender gets nothing at all - while the pipeline still
    labels and still escalates, because knowing about a safeguarding mail is not
    something a delivery switch should be able to turn off.

    ``on_capped`` is told only when a cap is what turned a send into a draft,
    so a run can count held-back replies apart from drafts the language gate
    or a missing send path produced.
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
        # Vietnamese ships and is reviewed as drafts until a native speaker has
        # signed the copy off, which is what this gate is for.
        logger.info(
            "Language %s is not in EMAIL_BOT_AUTO_LANGUAGES; drafting instead", language
        )
        return draft

    allows = getattr(send, "allows", None)
    if allows is not None:
        decision = allows(sender_key)
        if not decision.allowed:
            logger.info(
                "Send cap %s reached (%s); drafting instead",
                decision.cap,
                decision.reason,
            )
            if on_capped is not None:
                on_capped(decision)
            return draft

    return send
