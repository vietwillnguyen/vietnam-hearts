"""
Pydantic schemas for API request/response models

These schemas define the structure of data that can be sent to and received from the API.
They provide automatic validation and serialization of data.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VolunteerBase(BaseModel):
    """Base schema for volunteer data"""

    name: str
    email: str
    phone: str | None = None
    location: str | None = None
    positions: list[str] | None = None
    availability: list[str] | None = None
    start_date: datetime | None = None
    commitment_duration: str | None = None
    teaching_experience: str | None = None
    experience_details: str | None = None
    teaching_certificate: str | None = None
    vietnamese_proficiency: str | None = None
    additional_support: list[str] | None = None
    additional_info: str | None = None
    is_active: bool = True


class VolunteerCreate(VolunteerBase):
    """Schema for creating a new volunteer"""

    pass


class Volunteer(VolunteerBase):
    """Schema for volunteer responses"""

    id: int
    created_at: datetime
    email_unsubscribe_token: str | None = None
    last_email_sent_at: datetime | None = None

    # Granular unsubscribe options
    weekly_reminders_subscribed: bool = True
    all_emails_subscribed: bool = True

    model_config = ConfigDict(from_attributes=True)


class EmailCommunicationBase(BaseModel):
    """Base schema for email communication data"""

    volunteer_id: int
    recipient_email: str
    email_type: str
    subject: str
    template_name: str | None = None
    status: str = "PENDING"
    error_message: str | None = None


class EmailCommunicationCreate(EmailCommunicationBase):
    """Schema for creating a new email communication"""

    pass


class EmailCommunication(EmailCommunicationBase):
    """Schema for email communication responses"""

    id: int
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Settings schemas
class SettingBase(BaseModel):
    """Base schema for setting data"""

    key: str
    value: str
    description: str | None = None


class SettingCreate(SettingBase):
    """Schema for creating a new setting"""

    pass


class SettingUpdate(BaseModel):
    """Schema for updating a setting"""

    value: str
    description: str | None = None


class Setting(SettingBase):
    """Schema for setting responses"""

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SettingsList(BaseModel):
    """Schema for listing all settings"""

    settings: list[Setting]
    total: int
