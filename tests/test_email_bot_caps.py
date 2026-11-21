"""Send caps: the bounds on what one day and one sender can receive.

Two properties carry the weight. The daily count rolls over on the
organisation's local day, not on UTC - Cloud Run sets no timezone, so a naive
clock would roll the cap at 07:00 Vietnam time, in the middle of the morning
poll. And a reached cap drafts rather than refuses, because dropping the reply
would turn a capacity limit into lost mail.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import Conversation, Message
from app.services.email_bot.caps import (
    DEFAULT_DAILY_SEND_CAP,
    DEFAULT_PER_SENDER_DAILY_CAP,
    CapsState,
)

VIETNAM = ZoneInfo("Asia/Ho_Chi_Minh")

# Mid-morning on a Tuesday, Vietnam time, which is the previous day in UTC.
# Any cap that counts on the UTC day gets this wrong.
NOW = datetime(2026, 9, 29, 8, 30, tzinfo=VIETNAM)


class Settings:
    def __init__(self, daily: int = 30, per_sender: int = 2) -> None:
        self.daily_send_cap = daily
        self.per_sender_daily_cap = per_sender


def as_stored(moment: datetime) -> datetime:
    """A local instant in the frame ``messages.created_at`` is actually stored in.

    The column is a plain DateTime written from ``datetime.now(UTC)``, so it
    reads back naive UTC. A test that seeded aware local datetimes would be
    comparing like with like against an aware bound and would pass while
    production, which writes naive UTC, counted nothing. That is the bug this
    conversion exists to keep out of the tests.
    """
    return moment.astimezone(UTC).replace(tzinfo=None)


@pytest.fixture
def seed(test_db):
    """Record sends at chosen local times, in the frame the pipeline writes."""

    def _seed(entries):
        for index, (sender_key, when, thread) in enumerate(entries):
            conversation = (
                test_db.query(Conversation)
                .filter(Conversation.thread_key == thread)
                .first()
            )
            if conversation is None:
                conversation = Conversation(
                    channel="email",
                    thread_key=thread,
                    sender_key=sender_key,
                    status="bot",
                    bot_reply_count=0,
                )
                test_db.add(conversation)
                test_db.commit()
                test_db.refresh(conversation)

            test_db.add(
                Message(
                    conversation_id=conversation.id,
                    direction="outbound",
                    action="sent",
                    language="en",
                    text="a reply",
                    gmail_message_id_out=f"sent-{index}",
                    created_at=as_stored(when),
                )
            )
        test_db.commit()

    return _seed


class TestTheLocalDayIsWhatCounts:
    def test_a_send_earlier_today_counts(self, test_db, seed):
        seed([("hash-a", NOW.replace(hour=1), "t-1")])
        state = CapsState.load(test_db, NOW, Settings())
        assert state.sent_today == 1

    def test_a_send_yesterday_evening_does_not_count(self, test_db, seed):
        # 23:00 the previous local day. A UTC-based count would include it.
        seed([("hash-a", NOW.replace(day=28, hour=23), "t-1")])
        state = CapsState.load(test_db, NOW, Settings())
        assert state.sent_today == 0

    def test_a_send_later_today_still_counts(self, test_db, seed):
        seed([("hash-a", NOW.replace(hour=18), "t-1")])
        assert CapsState.load(test_db, NOW, Settings()).sent_today == 1

    def test_a_send_tomorrow_does_not_count(self, test_db, seed):
        seed([("hash-a", NOW + timedelta(days=1), "t-1")])
        assert CapsState.load(test_db, NOW, Settings()).sent_today == 0

    def test_the_day_boundary_is_local_midnight(self, test_db):
        state = CapsState.load(test_db, NOW, Settings())
        assert state.day_start.hour == 0
        assert state.day_start.date() == NOW.date()


class TestOnlySendsCount:
    def test_a_draft_is_not_a_send(self, test_db):
        # Drafts are what the whole draft-acceptance metric measures. Counting
        # them against the send cap would stop the bot drafting in draft mode.
        conversation = Conversation(
            channel="email",
            thread_key="t-1",
            sender_key="hash-a",
            status="bot",
            bot_reply_count=0,
        )
        test_db.add(conversation)
        test_db.commit()
        test_db.refresh(conversation)
        test_db.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                action="drafted",
                gmail_draft_id="draft-1",
                created_at=as_stored(NOW),
            )
        )
        test_db.commit()

        assert CapsState.load(test_db, NOW, Settings()).sent_today == 0

    def test_a_forward_is_not_a_send(self, test_db):
        # The forward goes to the captain, not to the public, so it must not
        # consume the public send budget.
        conversation = Conversation(
            channel="email",
            thread_key="t-1",
            sender_key="hash-a",
            status="bot",
            bot_reply_count=0,
        )
        test_db.add(conversation)
        test_db.commit()
        test_db.refresh(conversation)
        test_db.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                action="forwarded",
                created_at=as_stored(NOW),
            )
        )
        test_db.commit()

        assert CapsState.load(test_db, NOW, Settings()).sent_today == 0


class TestTheDailyCap:
    def test_below_the_cap_allows(self, test_db, seed):
        seed([("hash-a", NOW, f"t-{index}") for index in range(5)])
        assert CapsState.load(test_db, NOW, Settings(daily=30)).allows("hash-z").allowed

    def test_at_the_cap_refuses(self, test_db, seed):
        seed([("hash-a", NOW, f"t-{index}") for index in range(3)])
        decision = CapsState.load(test_db, NOW, Settings(daily=3)).allows("hash-z")

        assert not decision.allowed
        assert decision.cap == "daily"
        assert "3 of 3" in decision.reason

    def test_the_cap_applies_to_a_sender_who_has_had_nothing(self, test_db, seed):
        # The daily cap is about the account's total output, not about fairness
        # between senders.
        seed([("hash-a", NOW, f"t-{index}") for index in range(3)])
        assert not CapsState.load(test_db, NOW, Settings(daily=3)).allows("new").allowed

    def test_remaining_never_goes_negative(self, test_db, seed):
        seed([("hash-a", NOW, f"t-{index}") for index in range(5)])
        assert CapsState.load(test_db, NOW, Settings(daily=3)).remaining_today == 0


class TestThePerSenderCap:
    def test_a_sender_below_the_cap_is_allowed(self, test_db, seed):
        seed([("hash-a", NOW, "t-1")])
        assert (
            CapsState.load(test_db, NOW, Settings(per_sender=2))
            .allows("hash-a")
            .allowed
        )

    def test_a_sender_at_the_cap_is_refused(self, test_db, seed):
        seed([("hash-a", NOW, "t-1"), ("hash-a", NOW, "t-2")])
        decision = CapsState.load(test_db, NOW, Settings(per_sender=2)).allows("hash-a")

        assert not decision.allowed
        assert decision.cap == "per_sender"

    def test_the_cap_counts_threads_not_messages(self, test_db, seed):
        # The design states two *threads*. Two replies in one thread cannot
        # happen anyway (the one-reply cap), but counting messages would make
        # the two caps disagree about what they bound.
        seed([("hash-a", NOW, "t-1"), ("hash-a", NOW.replace(hour=12), "t-1")])
        assert (
            CapsState.load(test_db, NOW, Settings(per_sender=2))
            .allows("hash-a")
            .allowed
        )

    def test_another_sender_is_unaffected(self, test_db, seed):
        # The loop bound is per sender: one misbehaving responder must not stop
        # everyone else being answered.
        seed([("hash-a", NOW, "t-1"), ("hash-a", NOW, "t-2")])
        state = CapsState.load(test_db, NOW, Settings(per_sender=2))

        assert not state.allows("hash-a").allowed
        assert state.allows("hash-b").allowed

    def test_yesterdays_threads_do_not_count_against_today(self, test_db, seed):
        seed(
            [
                ("hash-a", NOW - timedelta(days=1), "t-1"),
                ("hash-a", NOW - timedelta(days=1), "t-2"),
            ]
        )
        assert (
            CapsState.load(test_db, NOW, Settings(per_sender=2))
            .allows("hash-a")
            .allowed
        )


class TestCapsHoldWithinOneRun:
    def test_recording_a_send_moves_the_daily_count(self, test_db):
        # Without this the caps would only bound what previous runs sent, and a
        # single run at the per-run cap could send twenty replies.
        state = CapsState.load(test_db, NOW, Settings(daily=2))
        assert state.allows("hash-a").allowed

        state.record_send("hash-a")
        state.record_send("hash-b")

        assert not state.allows("hash-c").allowed

    def test_recording_a_send_moves_the_per_sender_count(self, test_db):
        state = CapsState.load(test_db, NOW, Settings(per_sender=1))
        assert state.allows("hash-a").allowed

        state.record_send("hash-a")

        assert not state.allows("hash-a").allowed
        assert state.allows("hash-b").allowed


class TestTheDesignDefaults:
    def test_the_documented_defaults_are_what_the_code_uses(self):
        assert DEFAULT_DAILY_SEND_CAP == 30
        assert DEFAULT_PER_SENDER_DAILY_CAP == 2

    def test_settings_without_the_cap_fields_fall_back_to_them(self, test_db):
        class Bare:
            pass

        state = CapsState.load(test_db, NOW, Bare())
        assert state.daily_cap == DEFAULT_DAILY_SEND_CAP
        assert state.per_sender_cap == DEFAULT_PER_SENDER_DAILY_CAP


class TestTheDashboardView:
    def test_it_reports_the_counts_and_never_a_sender(self, test_db, seed):
        seed([("hash-a", NOW, "t-1"), ("hash-b", NOW, "t-2")])
        summary = CapsState.load(test_db, NOW, Settings(daily=30)).as_dict()

        assert summary["sent_today"] == 2
        assert summary["remaining_today"] == 28
        assert summary["senders_today"] == 2
        # Sender hashes are not identifying, but there is no reason to render
        # them either.
        assert "hash-a" not in str(summary)


class TestTheStoredFrameIsTheOneProductionWrites:
    """The bug this class exists for.

    ``messages.created_at`` is a plain DateTime written from
    ``datetime.now(UTC)``, so it reads back naive UTC. An earlier version of
    ``CapsState.load`` compared it against an aware Vietnam-midnight bound,
    which matched nothing: every cap read as untouched and the bot would have
    sent without limit.
    """

    def test_a_send_written_the_way_production_writes_it_is_counted(self, test_db):
        from datetime import datetime as real_datetime

        from app.models import Conversation, Message

        conversation = Conversation(
            channel="email",
            thread_key="t-1",
            sender_key="hash-a",
            status="bot",
            bot_reply_count=1,
        )
        test_db.add(conversation)
        test_db.commit()
        test_db.refresh(conversation)
        now_utc = real_datetime.now(UTC)
        test_db.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                action="sent",
                language="en",
                text="a reply",
                gmail_message_id_out="sent-1",
                created_at=now_utc,
            )
        )
        test_db.commit()

        state = CapsState.load(test_db, now_utc.astimezone(VIETNAM), Settings())
        assert state.sent_today == 1

    def test_the_reported_day_start_is_local(self, test_db):
        # The window is decided in local time even though it is queried in
        # naive UTC, and the dashboard shows the local boundary.
        state = CapsState.load(test_db, NOW, Settings())
        assert state.day_start.tzinfo is not None
        assert state.day_start.hour == 0
