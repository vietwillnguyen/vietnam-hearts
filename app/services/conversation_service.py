"""The conversation store: dedupe, the reply cap, pausing, and the audit rows.

Three safety controls live here rather than in the pipeline, because all three
are really statements about persisted state and a control that only holds while
one function runs is not a control.

*Idempotence.* ``is_duplicate`` reads the unique index on the Gmail message id.
The inbound row is committed before any side effect, so a run that dies after
drafting and before labelling cannot draft twice.

*One bot reply per thread.* ``bot_reply_count`` is capped at 1. A second
inbound on a thread the bot already answered escalates instead of getting a
second reply.

*Never talk over a human.* ``pause_manual`` is only undone by ``resume``, which
is a dashboard action. Nothing in the pipeline can decide a thread has become
safe again.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Conversation, Message
from app.services.channels.base import IncomingMessage
from app.services.triage.protocol import Tier, TriageDecision, TriageSignals
from app.utils.logging_config import get_logger

logger = get_logger("conversation_service")

STATUS_BOT = "bot"
STATUS_PAUSED_HANDOFF = "paused_handoff"
STATUS_PAUSED_MANUAL = "paused_manual"

MAX_BOT_REPLIES_PER_THREAD = 1

ACTION_LABELLED = "labelled"
ACTION_DRAFTED = "drafted"
ACTION_SENT = "sent"
ACTION_FORWARDED = "forwarded"
ACTION_SKIPPED = "skipped"
ACTION_PAUSED = "paused"

DRAFT_PENDING = "pending"
DRAFT_SENT_UNCHANGED = "sent_unchanged"
DRAFT_SENT_EDITED = "sent_edited"
DRAFT_DELETED = "deleted"


class ConversationService:
    """Every read and write of ``conversations`` and ``messages``."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def is_duplicate(self, provider_message_id: str) -> bool:
        """True when this provider message already has an inbound row.

        Cheap, and asked before anything else in the per-message loop, so a
        re-listed message costs one indexed lookup instead of a classifier
        call.
        """
        if not provider_message_id:
            return False
        return (
            self.db.query(Message.id)
            .filter(Message.provider_message_id == provider_message_id)
            .first()
            is not None
        )

    def get_or_create(
        self, channel: str, thread_key: str, sender_key: str
    ) -> Conversation:
        conversation = (
            self.db.query(Conversation)
            .filter(
                Conversation.channel == channel,
                Conversation.thread_key == thread_key,
            )
            .first()
        )
        if conversation:
            return conversation

        conversation = Conversation(
            channel=channel,
            thread_key=thread_key,
            sender_key=sender_key,
            status=STATUS_BOT,
            bot_reply_count=0,
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def record_inbound(
        self,
        conversation: Conversation,
        message: IncomingMessage,
        decision: TriageDecision,
        shadow: TriageSignals | None = None,
        action: str = ACTION_LABELLED,
    ) -> Message:
        """The audit row for one inbound mail, committed before any side effect.

        ``text`` is left null on purpose. The inbound body stays in Gmail; what
        the database keeps is the ids, the triage metadata and the action, which
        is everything the dashboard, the metrics and an investigation need
        without the database becoming a second copy of the inbox.
        """
        row = Message(
            conversation_id=conversation.id,
            direction="inbound",
            provider_message_id=message.provider_message_id,
            text=None,
            language=decision.language,
            action=action,
            rfc_message_id=message.rfc_message_id,
            gmail_thread_id=message.thread_key,
            category=decision.category,
            tier=decision.tier,
            triage_confidence=decision.confidence,
            classifier=decision.classifier,
            triage_shadow=shadow.as_record() if shadow else None,
        )
        conversation.last_category = decision.category
        conversation.last_tier = decision.tier
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def record_action(
        self, conversation: Conversation, message: IncomingMessage, action: str
    ) -> Message:
        """An inbound row for a mail that was never triaged.

        Guard skips and manual-pause detections take this path: there is no
        decision to record, but the idempotence key must still be written or
        the next run would process the mail again.
        """
        row = Message(
            conversation_id=conversation.id,
            direction="inbound",
            provider_message_id=message.provider_message_id,
            text=None,
            action=action,
            rfc_message_id=message.rfc_message_id,
            gmail_thread_id=message.thread_key,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def record_outbound(
        self,
        conversation: Conversation,
        *,
        text: str,
        action: str,
        language: str,
        gmail_message_id_out: str | None = None,
        gmail_draft_id: str | None = None,
        confidence: float | None = None,
        sources: list[str] | None = None,
        kind: str | None = None,
    ) -> Message:
        """The audit row for something the bot produced.

        Outbound text *is* stored, unlike inbound. It is the organisation's own
        words, the captain needs to see what was offered on his behalf, and the
        draft-acceptance metric compares it against what was eventually sent.

        Counts toward ``bot_reply_count`` only for a draft or a send, not for a
        forward: the forward goes to the captain, and letting it consume the
        thread's one reply would mean an escalated sender never got the holding
        message.
        """
        row = Message(
            conversation_id=conversation.id,
            direction="outbound",
            provider_message_id=None,
            text=text,
            language=language,
            action=action,
            category=kind,
            gmail_thread_id=conversation.thread_key,
            gmail_message_id_out=gmail_message_id_out,
            gmail_draft_id=gmail_draft_id,
            draft_outcome=DRAFT_PENDING if gmail_draft_id else None,
            triage_confidence=confidence,
            sources=sources,
        )
        if action in (ACTION_DRAFTED, ACTION_SENT):
            conversation.bot_reply_count = (conversation.bot_reply_count or 0) + 1
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def can_bot_reply(self, conversation: Conversation) -> bool:
        """Whether the bot may put another reply in this thread."""
        return (
            conversation.status == STATUS_BOT
            and (conversation.bot_reply_count or 0) < MAX_BOT_REPLIES_PER_THREAD
        )

    def pause(self, conversation: Conversation, tier: Tier, reason: str) -> None:
        """Step back after an escalation.

        Not applied over a manual pause: a human reply is the stronger state and
        an escalation arriving afterwards must not downgrade it to something a
        later escalation could clear.
        """
        if conversation.status == STATUS_PAUSED_MANUAL:
            return
        conversation.status = STATUS_PAUSED_HANDOFF
        conversation.pause_reason = f"{tier}: {reason}"
        conversation.updated_at = datetime.now(UTC)
        self.db.commit()

    def pause_manual(self, conversation: Conversation) -> None:
        """A human has replied in this thread. Only ``resume`` undoes this."""
        conversation.status = STATUS_PAUSED_MANUAL
        conversation.pause_reason = "a human replied in this thread"
        conversation.updated_at = datetime.now(UTC)
        self.db.commit()

    def resume(self, conversation: Conversation) -> None:
        """Hand the thread back to the bot. A dashboard action, never automatic.

        The reply counter is deliberately left alone. Resuming says "the bot may
        act again", not "pretend it never replied"; clearing the counter here
        would quietly turn the one-reply-per-thread cap into one reply per
        resume.
        """
        conversation.status = STATUS_BOT
        conversation.pause_reason = None
        conversation.updated_at = datetime.now(UTC)
        self.db.commit()

    def outstanding_draft(self, conversation: Conversation) -> Message | None:
        """The bot's newest unresolved draft in this thread, if it still has one."""
        return (
            self.db.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.action == ACTION_DRAFTED,
                Message.gmail_draft_id.isnot(None),
                Message.draft_outcome == DRAFT_PENDING,
            )
            .order_by(Message.id.desc())
            .first()
        )

    def bot_sent_message_ids(self, conversation: Conversation) -> set[str]:
        """Gmail ids of everything the bot itself put in this thread.

        The "never talk over a human" guard subtracts these from the messages
        the thread shows as coming from the inbox address. Without it the bot's
        own reply would read as a human reply and pause every thread it
        answered.
        """
        rows = (
            self.db.query(Message.gmail_message_id_out)
            .filter(
                Message.conversation_id == conversation.id,
                Message.gmail_message_id_out.isnot(None),
            )
            .all()
        )
        return {row[0] for row in rows if row[0]}

    def escalations(self, limit: int = 50) -> list[Conversation]:
        """Threads the bot has handed over and nobody has resumed."""
        return (
            self.db.query(Conversation)
            .filter(
                Conversation.status.in_([STATUS_PAUSED_HANDOFF, STATUS_PAUSED_MANUAL])
            )
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .all()
        )

    def draft_acceptance(self) -> dict[str, dict[str, int]]:
        """Draft outcomes grouped by the answer path that produced them.

        The E2 gate is stated per kind - 90 percent of sign-up drafts, 80
        percent of FAQ drafts - so an aggregate number would not answer it.
        """
        rows = (
            self.db.query(
                Message.category, Message.draft_outcome, func.count(Message.id)
            )
            .filter(
                Message.action == ACTION_DRAFTED,
                Message.draft_outcome.isnot(None),
            )
            .group_by(Message.category, Message.draft_outcome)
            .all()
        )
        summary: dict[str, dict[str, int]] = {}
        for kind, outcome, count in rows:
            summary.setdefault(kind or "unknown", {})[outcome] = count
        return summary
