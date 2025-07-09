"""
Database models for Vietnam Hearts Scheduler

This file defines the structure of our database tables.
SQLAlchemy ORM converts these Python classes into database tables.
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime, timezone
from pydantic import ConfigDict


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
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
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
        String, default="pending"
    )  # pending, sent, delivered, failed, bounced
    error_message = Column(String)  # If delivery failed
    sent_at = Column(DateTime, default=datetime.now(timezone.utc))
    delivered_at = Column(DateTime, nullable=True)  # When the email was delivered

    # Relationships
    volunteer = relationship("Volunteer", back_populates="email_communications")

    # Metadata
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
    )

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
    description = Column(String, nullable=True)  # Human-readable description of the setting
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
    )

    class Config:
        from_attributes = True
