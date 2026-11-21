"""Send caps: the bounds on what one day, one run and one sender can receive.

Three separate limits, because they bound three different failures.

``EMAIL_BOT_PER_RUN_CAP`` bounds the model calls one poll can make, and applies
in every mode. It is enforced by the pipeline rather than here, because it
bounds processing rather than sending.

``EMAIL_BOT_DAILY_SEND_CAP`` bounds how much mail the account sends in a day.
Gmail's own 500-per-day limit is shared with the weekly reminder blast, so an
inbox bot that ran away would take the reminders down with it.

``EMAIL_BOT_PER_SENDER_DAILY_CAP`` bounds how many threads one sender can be
replied to in a day. This is the loop bound: a misbehaving responder on the
other side cannot extract more than this many replies however many times it
writes.

A reached cap is never a refusal. The reply is drafted instead, so the captain
still has it one click away, and the run records that it happened. Dropping the
reply would turn a capacity limit into lost mail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Conversation, Message
from app.services.conversation_service import ACTION_SENT
from app.utils.logging_config import get_logger

logger = get_logger("email_bot_caps")

DEFAULT_DAILY_SEND_CAP = 30
DEFAULT_PER_SENDER_DAILY_CAP = 2


def _as_naive_utc(moment: datetime) -> datetime:
    """A comparison bound in the frame ``messages.created_at`` is stored in.

    Aware in, naive UTC out. Naive in is returned unchanged and assumed to be
    UTC already, which is what every naive datetime in this codebase means.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class CapDecision:
    """Whether a send is allowed, and if not, which cap stopped it."""

    allowed: bool
    reason: str = ""
    cap: str = ""

    @classmethod
    def allow(cls) -> CapDecision:
        return cls(allowed=True)

    @classmethod
    def refuse(cls, cap: str, reason: str) -> CapDecision:
        return cls(allowed=False, cap=cap, reason=reason)


@dataclass
class CapsState:
    """Today's send counts, read once per run.

    "Today" is the organisation's local day, not UTC. Cloud Run sets no
    timezone, so a naive clock reads UTC and would roll the daily cap over at
    07:00 Vietnam time, in the middle of the morning poll - the same class of
    bug the schedule week anchor exists to avoid.
    """

    daily_cap: int = DEFAULT_DAILY_SEND_CAP
    per_sender_cap: int = DEFAULT_PER_SENDER_DAILY_CAP
    sent_today: int = 0
    sent_today_by_sender: dict[str, int] = field(default_factory=dict)
    day_start: datetime | None = None

    @classmethod
    def load(cls, db: Session, now_local: datetime, settings) -> CapsState:
        """Count what has already been sent on ``now_local``'s local day.

        Counted from the ``messages`` rows rather than from an in-memory
        tally, so a cap survives a restart, a second instance and a scheduler
        retry. A counter that lived in the process would reset to zero with it.
        """
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        # The window is decided in local time and queried in naive UTC, because
        # that is what is actually stored: messages.created_at is a plain
        # DateTime written from datetime.now(UTC), so it reads back naive. An
        # aware local bound compares against those as though they carried an
        # offset and silently matches nothing, which would report every cap as
        # untouched and let the bot send without limit.
        start = _as_naive_utc(start_local)
        end = _as_naive_utc(start_local + timedelta(days=1))

        total = (
            db.query(func.count(Message.id))
            .filter(
                Message.action == ACTION_SENT,
                Message.created_at >= start,
                Message.created_at < end,
            )
            .scalar()
            or 0
        )

        # Per sender, and per *thread* rather than per message: the cap the
        # design states is "2 threads", so two replies in one thread count once.
        rows = (
            db.query(
                Conversation.sender_key,
                func.count(func.distinct(Message.conversation_id)),
            )
            .join(Message, Message.conversation_id == Conversation.id)
            .filter(
                Message.action == ACTION_SENT,
                Message.created_at >= start,
                Message.created_at < end,
            )
            .group_by(Conversation.sender_key)
            .all()
        )

        return cls(
            daily_cap=getattr(settings, "daily_send_cap", DEFAULT_DAILY_SEND_CAP),
            per_sender_cap=getattr(
                settings, "per_sender_daily_cap", DEFAULT_PER_SENDER_DAILY_CAP
            ),
            sent_today=int(total),
            sent_today_by_sender={key: int(count) for key, count in rows if key},
            day_start=start_local,
        )

    def allows(self, sender_key: str) -> CapDecision:
        """Whether one more send to this sender is within both caps."""
        if self.sent_today >= self.daily_cap:
            return CapDecision.refuse(
                "daily",
                f"{self.sent_today} of {self.daily_cap} sent today",
            )

        for_sender = self.sent_today_by_sender.get(sender_key, 0)
        if for_sender >= self.per_sender_cap:
            return CapDecision.refuse(
                "per_sender",
                f"{for_sender} of {self.per_sender_cap} threads for this sender today",
            )

        return CapDecision.allow()

    def record_send(self, sender_key: str) -> None:
        """Count a send this run just made, so the caps hold within one run.

        Without this the caps would only bound what previous runs sent, and a
        single run at the per-run cap could send twenty replies to one sender.
        """
        self.sent_today += 1
        self.sent_today_by_sender[sender_key] = (
            self.sent_today_by_sender.get(sender_key, 0) + 1
        )

    @property
    def remaining_today(self) -> int:
        return max(0, self.daily_cap - self.sent_today)

    def as_dict(self) -> dict[str, object]:
        """For the dashboard card. Sender hashes are deliberately not included."""
        return {
            "daily_cap": self.daily_cap,
            "per_sender_cap": self.per_sender_cap,
            "sent_today": self.sent_today,
            "remaining_today": self.remaining_today,
            "senders_today": len(self.sent_today_by_sender),
            "day_start": self.day_start.isoformat() if self.day_start else None,
        }
