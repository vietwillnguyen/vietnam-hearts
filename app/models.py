"""
Database models for Vietnam Hearts Scheduler

This file defines the structure of our database tables.
SQLAlchemy ORM converts these Python classes into database tables.
"""

from datetime import UTC, datetime

from pydantic import ConfigDict
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


# Base class for all our models
class Base(DeclarativeBase):
    pass


class Volunteer(Base):
    """
    Represents a volunteer who can teach or be a TA

    Fields are based on the Google Form responses:
    - Basic Info: name, email
    - Role Preferences: positions interested in
    - Experience: teaching experience
    """

    __tablename__ = "volunteers"

    # Basic Information
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, index=True, nullable=False)

    # Role Preferences
    positions = Column(
        JSON
    )  # List of positions they're interested in: ["Teacher", "Teaching Assistant", "Non Teaching Role"]

    # Experience
    teaching_experience = Column(
        String
    )  # None, Some experience as TA, Teaching experience (less than 1 year), Teaching experience (1+ years)

    # Metadata
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    is_active = Column(Boolean, default=True)

    # Email-related fields
    email_unsubscribe_token = Column(String, unique=True, nullable=True)
    last_email_sent_at = Column(DateTime, nullable=True)

    # Granular unsubscribe options
    weekly_reminders_subscribed = Column(Boolean, default=True)
    all_emails_subscribed = Column(Boolean, default=True)

    # Contact Information
    phone = Column(String, nullable=True)
    location = Column(String, nullable=True)

    # Availability and Commitment
    availability = Column(JSON)  # List of available time slots
    start_date = Column(DateTime, nullable=True)
    commitment_duration = Column(String, nullable=True)

    # Experience and Qualifications
    experience_details = Column(String, nullable=True)
    teaching_certificate = Column(String, nullable=True)
    vietnamese_proficiency = Column(String, nullable=True)

    # Additional Information
    additional_support = Column(JSON)  # List of additional support they can provide
    additional_info = Column(String, nullable=True)

    # Relationships
    email_communications = relationship(
        "EmailCommunication", back_populates="volunteer"
    )

    model_config = ConfigDict(from_attributes=True)


class EmailCommunication(Base):
    """
    Tracks all email communications sent to volunteers

    Why track emails?
    - Monitor delivery success/failure
    - Track open rates and engagement
    - Maintain communication history
    - Handle unsubscribe requests
    """

    __tablename__ = "email_communications"

    id = Column(Integer, primary_key=True, index=True)

    # Who was the email sent to?
    volunteer_id = Column(Integer, ForeignKey("volunteers.id"), nullable=False)
    recipient_email = Column(String, nullable=False)

    # What was the email about?
    email_type = Column(
        String, nullable=False
    )  # "volunteer_confirmation", "weekly_reminder"
    subject = Column(String, nullable=False)
    template_name = Column(String)  # Which email template was used

    # Status tracking
    status = Column(
        String, default="PENDING"
    )  # pending, sent, delivered, failed, bounced
    error_message = Column(String)  # If delivery failed
    sent_at = Column(DateTime, default=lambda: datetime.now(UTC))
    delivered_at = Column(DateTime, nullable=True)  # When the email was delivered

    # Relationships
    volunteer = relationship("Volunteer", back_populates="email_communications")

    # Metadata
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    class Config:
        from_attributes = True


class SystemLog(Base):
    """
    Persisted application log record.

    Backs the admin dashboard Logs page so log history survives Cloud Run
    container restarts (the container filesystem is ephemeral).
    """

    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, index=True, nullable=False)
    level = Column(String, index=True, nullable=False)
    logger_name = Column(String, nullable=True)
    message = Column(Text, nullable=False)

    class Config:
        from_attributes = True


class Setting(Base):
    """
    Stores dynamic configuration settings that can be updated by admins

    This allows runtime configuration changes without requiring code deployments
    """

    __tablename__ = "settings"

    key = Column(String, primary_key=True, index=True)
    value = Column(String, nullable=False)
    description = Column(
        String, nullable=True
    )  # Human-readable description of the setting
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    class Config:
        from_attributes = True


class Conversation(Base):
    """One thread the inbound bot is tracking, on any channel.

    Channel-neutral on purpose: email fills ``thread_key`` with the Gmail
    threadId, Messenger will fill it with its own thread id when it moves onto
    the same engine. The sender address is never stored - ``sender_key`` is a
    SHA-256 of the lower-cased address, which is all the per-sender cap and
    the grouping need.

    ``status`` is the "never talk over a human" control:
      ``bot``            the bot may still act
      ``paused_handoff`` the bot escalated and stepped back
      ``paused_manual``  a human replied from the inbox, so only a dashboard
                         resume moves it back
    """

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "channel", "thread_key", name="uq_conversations_channel_thread"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    channel = Column(String, nullable=False, index=True)
    thread_key = Column(String, nullable=False, index=True)
    sender_key = Column(String, nullable=False, index=True)

    status = Column(String, nullable=False, default="bot")
    pause_reason = Column(String, nullable=True)
    bot_reply_count = Column(Integer, nullable=False, default=0)

    last_category = Column(String, nullable=True)
    last_tier = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    messages = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )

    class Config:
        from_attributes = True


class Message(Base):
    """One audited action on a conversation, inbound or outbound.

    The unique index on ``provider_message_id`` is the pipeline's idempotence
    key: the inbound row is committed before any side effect, so a re-listed
    Gmail message re-applies labels and does nothing else.

    Privacy: on the email channel ``text`` holds outbound bot text only.
    Inbound bodies are never copied out of Gmail; the row carries ids, triage
    metadata and the action instead.
    """

    __tablename__ = "messages"
    # Unique rather than merely indexed: this is the idempotence key. Both
    # SQLite and Postgres treat NULLs as distinct in a unique index, so the
    # outbound rows (which have no provider id) are unconstrained by it.
    __table_args__ = (
        Index(
            "uq_messages_provider_message_id",
            "provider_message_id",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=False, index=True
    )

    direction = Column(String, nullable=False)  # "inbound" | "outbound"
    # Outbound rows have no provider id of their own, so this stays nullable;
    # the unique index therefore constrains inbound rows only.
    provider_message_id = Column(String, nullable=True)

    text = Column(Text, nullable=True)
    language = Column(String, nullable=True)  # "en" | "vi" | "other"
    action = Column(String, nullable=False)

    rfc_message_id = Column(String, nullable=True)
    gmail_thread_id = Column(String, nullable=True)
    gmail_message_id_out = Column(String, nullable=True)
    gmail_draft_id = Column(String, nullable=True)
    draft_outcome = Column(String, nullable=True)
    # When reconciliation last looked at this draft. Pending drafts are
    # checked least recently first, so a pile of drafts nobody touches cannot
    # crowd newer ones out of every run.
    draft_checked_at = Column(DateTime, nullable=True)

    # When this inbound mail finished being handled: answered, escalated,
    # skipped or paused. NULL means the pipeline started on it and did not get
    # to a terminal state, so the next run must pick it up again.
    #
    # Dedupe reads this rather than the existence of the row. The row is
    # committed early, before any side effect, so that a crash cannot produce
    # a second draft - but "we have seen this" and "a person has been told
    # about this" are different facts, and conflating them is how a mail gets
    # recorded as done without ever being answered or forwarded.
    handled_at = Column(DateTime, nullable=True, index=True)

    category = Column(String, nullable=True)
    tier = Column(String, nullable=True)
    triage_confidence = Column(Float, nullable=True)
    classifier = Column(String, nullable=True)
    triage_shadow = Column(JSON, nullable=True)
    sources = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(UTC), index=True)

    conversation = relationship("Conversation", back_populates="messages")

    class Config:
        from_attributes = True


class EmailBotRun(Base):
    """One poll of the volunteer inbox, for the dashboard card and the audit.

    A row with no ``finished_at`` is an in-flight run, which is how the
    endpoint refuses a concurrent scheduler retry. ``aborted_reason`` is set
    when the circuit breaker stops a run so a systemic outage is visible
    rather than looking like a quiet day.
    """

    __tablename__ = "email_bot_runs"

    id = Column(Integer, primary_key=True, index=True)

    started_at = Column(DateTime, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True)
    mode = Column(String, nullable=False)

    listed = Column(Integer, nullable=False, default=0)
    processed = Column(Integer, nullable=False, default=0)
    drafted = Column(Integer, nullable=False, default=0)
    sent = Column(Integer, nullable=False, default=0)
    forwarded = Column(Integer, nullable=False, default=0)
    skipped = Column(Integer, nullable=False, default=0)
    # Drafts whose outcome this run settled. Its own counter rather than folded
    # into processed, because the dashboard card reads it as the health of the
    # draft-acceptance metric: a run that reconciles nothing for a fortnight
    # means the metric the evaluation gate depends on is not being collected.
    reconciled = Column(Integer, nullable=False, default=0)
    # Replies that were drafted while the mode said send, because a cap was
    # reached or the language is not signed off for automatic sending. Its own
    # counter so the dashboard does not read a capped run as a quiet one.
    capped = Column(Integer, nullable=False, default=0)
    errors = Column(Integer, nullable=False, default=0)

    aborted_reason = Column(String, nullable=True)

    class Config:
        from_attributes = True
