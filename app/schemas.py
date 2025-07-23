"""
Pydantic schemas for API request/response models

These schemas define the structure of data that can be sent to and received from the API.
They provide automatic validation and serialization of data.
"""

from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime


class VolunteerBase(BaseModel):
    """Base schema for volunteer data"""

    name: str
    email: str
    phone: Optional[str] = None
    location: Optional[str] = None
    positions: Optional[List[str]] = None
    availability: Optional[List[str]] = None
    start_date: Optional[datetime] = None
    commitment_duration: Optional[str] = None
    teaching_experience: Optional[str] = None
    experience_details: Optional[str] = None
    teaching_certificate: Optional[str] = None
    vietnamese_proficiency: Optional[str] = None
    additional_support: Optional[List[str]] = None
    additional_info: Optional[str] = None
    is_active: bool = True


class VolunteerCreate(VolunteerBase):
    """Schema for creating a new volunteer"""

    pass


class Volunteer(VolunteerBase):
    """Schema for volunteer responses"""

    id: int
    created_at: datetime
    email_unsubscribe_token: Optional[str] = None
    last_email_sent_at: Optional[datetime] = None

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
    template_name: Optional[str] = None
    status: str = "pending"
    error_message: Optional[str] = None


class EmailCommunicationCreate(EmailCommunicationBase):
    """Schema for creating a new email communication"""

    pass


class EmailCommunication(EmailCommunicationBase):
    """Schema for email communication responses"""

    id: int
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Settings schemas
class SettingBase(BaseModel):
    """Base schema for setting data"""
    key: str
    value: str
    description: Optional[str] = None

class SettingCreate(SettingBase):
    """Schema for creating a new setting"""
    pass

class SettingUpdate(BaseModel):
    """Schema for updating a setting"""
    value: str
    description: Optional[str] = None

class Setting(SettingBase):
    """Schema for setting responses"""
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class SettingsList(BaseModel):
    """Schema for listing all settings"""
    settings: List[Setting]
    total: int
