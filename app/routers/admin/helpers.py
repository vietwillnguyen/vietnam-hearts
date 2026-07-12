"""
Shared helpers used across multiple admin sub-routers.
"""

from datetime import datetime

from app.models import Volunteer as VolunteerModel
from app.utils.logging_config import get_api_logger

logger = get_api_logger()


def parse_start_date(date_str):
    """Parse start date from form submission"""
    if not date_str or date_str.upper() == "ASAP":
        return datetime.now().date()
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").date()
    except ValueError:
        logger.warning(f"Invalid date format: {date_str}")
        return None


def get_volunteer_summary(volunteers):
    """Extract volunteer data with confirmation status"""
    volunteer_data = []
    for volunteer in volunteers:
        confirmation_sent = any(
            comm.email_type == "volunteer_confirmation" and comm.status == "sent"
            for comm in volunteer.email_communications
        )
        last_confirmation = next(
            (
                comm
                for comm in volunteer.email_communications
                if comm.email_type == "volunteer_confirmation" and comm.status == "sent"
            ),
            None,
        )
        volunteer_data.append(
            {
                "id": volunteer.id,
                "name": volunteer.name,
                "email": volunteer.email,
                "is_active": volunteer.is_active,
                "weekly_reminders_subscribed": volunteer.weekly_reminders_subscribed,
                "all_emails_subscribed": volunteer.all_emails_subscribed,
                "confirmation_sent": confirmation_sent,
                "last_confirmation_date": last_confirmation.sent_at
                if last_confirmation
                else None,
                "positions": volunteer.positions or [],
                "created_at": volunteer.created_at,
            }
        )
    return volunteer_data


def get_email_summary(communications):
    """Extract email communication data"""
    email_data = []
    for comm in communications:
        email_data.append(
            {
                "id": comm.id,
                "volunteer_id": comm.volunteer_id,
                "volunteer_name": comm.volunteer.name,
                "recipient_email": comm.recipient_email,
                "email_type": comm.email_type,
                "subject": comm.subject,
                "status": comm.status,
                "sent_at": comm.sent_at,
                "delivered_at": comm.delivered_at,
            }
        )
    return email_data


def create_new_volunteer_object(submission: dict) -> VolunteerModel:
    """Create a new VolunteerModel from a form submission dict (no DB writes)."""
    first_name = submission.get("first_name", "").strip()
    last_name = submission.get("last_name", "").strip()
    full_name = f"{first_name} {last_name}".strip() or "Unknown Volunteer"

    return VolunteerModel(
        name=full_name,
        email=submission["email_address"],
        phone=submission["phone_number"],
        positions=[pos.strip() for pos in submission["position_interest"].split(",")]
        if submission.get("position_interest")
        else [],
        location=submission["location"],
        availability=[slot.strip() for slot in submission["availability"].split(",")]
        if submission.get("availability")
        else [],
        start_date=parse_start_date(submission["start_date"]),
        commitment_duration=submission["commitment_duration"],
        teaching_experience=submission["teaching_experience"],
        experience_details=submission["experience_details"],
        teaching_certificate=submission["teaching_certificate"],
        vietnamese_proficiency=submission["vietnamese_speaking"],
        additional_support=[
            support.strip() for support in submission["other_support"].split(",")
        ]
        if submission.get("other_support")
        else [],
        additional_info=f"Social Media: {submission.get('social_media_link', 'N/A')}\nReferral Source: {submission.get('referral_source', 'N/A')}",
        is_active=True,
    )
