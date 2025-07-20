"""
Admin API endpoints for manual control and monitoring

These endpoints provide admin functionality for:
- Manual triggers (weekly reminders, schedule rotation)
- Monitoring and statistics
- Volunteer and email management
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import os

from app.database import get_db
from app.models import (
    Volunteer as VolunteerModel,
    EmailCommunication as EmailCommunicationModel,
)
from app.schemas import Volunteer
from app.services.google_sheets import sheets_service
from app.services.email_service import email_service
from app.utils.logging_config import get_api_logger
from app.config import ENVIRONMENT
from app.utils.config_helper import ConfigHelper
from app.utils.retry_utils import log_ssl_error, safe_api_call
from app.utils.sheet_utils import validate_google_sheets_url

logger = get_api_logger()
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
)

# Initialize templates
templates = Jinja2Templates(directory="templates")


def normalize_date(date_obj):
    """Convert date object or string to YYYY-MM-DD format"""
    if date_obj is None:
        return None
    return (
        date_obj.strftime("%Y-%m-%d")
        if hasattr(date_obj, "strftime")
        else str(date_obj)
    )


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
        # Check if confirmation email was sent by looking at EmailCommunication records
        confirmation_sent = any(
            comm.email_type == "volunteer_confirmation" and comm.status == "sent"
            for comm in volunteer.email_communications
        )

        # Get the last confirmation email sent
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


# Volunteer endpoints
@admin_router.get("/volunteers")
def view_volunteers(
    db: Session = Depends(get_db),
):
    """View all volunteers and their email status"""
    try:
        volunteers = db.query(VolunteerModel).all()
        volunteer_data = get_volunteer_summary(volunteers)

        return {
            "status": "success",
            "total_volunteers": len(volunteer_data),
            "volunteers": volunteer_data,
        }

    except Exception as e:
        logger.error(f"Failed to fetch volunteer data: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch volunteer data: {str(e)}"
        )


@admin_router.get(
    "/volunteers/active",
    summary="List active volunteers",
    description="Returns list of active volunteers",
    response_model=List[Volunteer],
)
def list_active_volunteers(db: Session = Depends(get_db)):
    """Get only active volunteers"""
    return db.query(VolunteerModel).filter(VolunteerModel.is_active == True).all()


@admin_router.get("/volunteers/announcement-recipients")
def get_announcement_recipients(db: Session = Depends(get_db)):
    """Get all volunteers who are subscribed to announcements (all emails)"""
    try:
        # Get all active volunteers who are subscribed to all emails
        recipients = (
            db.query(VolunteerModel)
            .filter(VolunteerModel.is_active == True)
            .filter(VolunteerModel.all_emails_subscribed == True)
            .order_by(VolunteerModel.name)
            .all()
        )
        
        # Format the response
        recipient_data = []
        for volunteer in recipients:
            recipient_data.append({
                "id": volunteer.id,
                "name": volunteer.name,
                "email": volunteer.email,
                "weekly_reminders_subscribed": volunteer.weekly_reminders_subscribed,
                "all_emails_subscribed": volunteer.all_emails_subscribed,
                "created_at": volunteer.created_at.isoformat() if volunteer.created_at else None
            })
        
        return {
            "status": "success",
            "total_recipients": len(recipient_data),
            "recipients": recipient_data,
            "message": f"Found {len(recipient_data)} volunteers subscribed to announcements"
        }
        
    except Exception as e:
        logger.error(f"Failed to get announcement recipients: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get announcement recipients: {str(e)}"
        )


@admin_router.get(
    "/volunteers/{volunteer_id}",
    summary="Get volunteer details",
    description="Returns details for a specific volunteer",
    response_model=Volunteer,
)
def get_volunteer_by_id(volunteer_id: int, db: Session = Depends(get_db)):
    volunteer = (
        db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
    )
    if not volunteer:
        raise HTTPException(status_code=404, detail="Volunteer not found")
    return volunteer


@admin_router.get("/email-logs")
def view_email_logs(
    db: Session = Depends(get_db),
):
    """View all email communications"""
    try:
        communications = db.query(EmailCommunicationModel).all()
        email_data = get_email_summary(communications)

        return {
            "status": "success",
            "total_emails": len(email_data),
            "emails": email_data,
        }

    except Exception as e:
        logger.error(f"Failed to fetch email logs: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch email logs: {str(e)}"
        )


@admin_router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request, 
    db: Session = Depends(get_db),
):
    """Admin dashboard to view volunteers and email logs"""
    try:
        # Get volunteer data
        volunteers = db.query(VolunteerModel).all()
        volunteer_data = get_volunteer_summary(volunteers)

        # Get email data
        communications = db.query(EmailCommunicationModel).all()
        email_data = get_email_summary(communications)
        
        # Get settings for template
        settings = []
        from app.services.settings_service import get_all_settings
        all_settings = get_all_settings(db)
        
        for setting in all_settings:
            settings.append({
                "key": setting.key,
                "value": setting.value,
                "description": setting.description
            })

        # Get settings data
        from app.services.settings_service import get_all_settings
        settings = get_all_settings(db)

        return templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            {
                "total_volunteers": len(volunteer_data),
                "volunteers": volunteer_data,
                "total_emails": len(email_data),
                "emails": email_data,
                "settings": settings,
                "config": {
                            # "SUPABASE_URL": os.getenv("SUPABASE_URL", ""),  # Removed auth
        # "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY", ""),  # Removed auth
                }
            },
        )

    except Exception as e:
        logger.error(f"Failed to render admin dashboard: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to render admin dashboard: {str(e)}"
        )


@admin_router.get("/reminder-stats")
def get_reminder_stats(db: Session = Depends(get_db)):
    """Get statistics about weekly reminder emails"""
    try:
        # Get all reminder emails
        reminder_emails = (
            db.query(EmailCommunicationModel)
            .filter(EmailCommunicationModel.email_type == "weekly_reminder")
            .all()
        )

        # Calculate statistics
        total_sent = len(reminder_emails)
        successful = sum(1 for e in reminder_emails if e.status == "sent")
        failed = sum(1 for e in reminder_emails if e.status == "failed")

        # Get last 4 weeks of data
        four_weeks_ago = datetime.now() - timedelta(weeks=4)
        recent_emails = [e for e in reminder_emails if e.sent_at >= four_weeks_ago]

        # Group by week
        weekly_stats = {}
        for email in recent_emails:
            week_start = email.sent_at - timedelta(days=email.sent_at.weekday())
            week_key = week_start.strftime("%Y-%m-%d")
            if week_key not in weekly_stats:
                weekly_stats[week_key] = {"total": 0, "successful": 0, "failed": 0}
            weekly_stats[week_key]["total"] += 1
            if email.status == "sent":
                weekly_stats[week_key]["successful"] += 1
            elif email.status == "failed":
                weekly_stats[week_key]["failed"] += 1

        return {
            "status": "success",
            "statistics": {
                "total_sent": total_sent,
                "successful": successful,
                "failed": failed,
                "success_rate": (successful / total_sent * 100)
                if total_sent > 0
                else 0,
                "weekly_stats": weekly_stats,
            },
        }

    except Exception as e:
        logger.error(f"Failed to get reminder statistics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get reminder statistics: {str(e)}"
        )


@admin_router.get("/schedule-status")
def get_schedule_status(db: Session = Depends(get_db)):
    """Get current status of schedule sheets"""
    try:
        # Get all schedule sheets
        sheets = sheets_service.get_schedule_sheets(db)

        # Process sheet information
        sheet_info = []
        for sheet in sheets:
            title = sheet["properties"]["title"]
            try:
                sheet_date = datetime.strptime(
                    title.replace("Schedule ", ""), "%m/%d/%Y"
                )
            except ValueError:
                sheet_date = None

            sheet_info.append(
                {
                    "title": title,
                    "date": sheet_date.isoformat() if sheet_date else None,
                    "hidden": sheet["properties"].get("hidden", False),
                    "index": sheet["properties"].get("index", 0),
                }
            )

        # Sort by date
        sheet_info.sort(key=lambda x: x["date"] if x["date"] else "")

        return {
            "status": "success",
            "details": {
                "display_weeks_count": ConfigHelper.get_schedule_sheets_display_weeks_count(db),
                "total_sheets": len(sheet_info),
                "visible_sheets": len([s for s in sheet_info if not s["hidden"]]),
                "hidden_sheets": len([s for s in sheet_info if s["hidden"]]),
                "sheets": sheet_info,
            },
        }

    except Exception as e:
        logger.error(f"Failed to get schedule status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get schedule status: {str(e)}"
        )


# Form submission endpoints
@admin_router.get(
    "/forms/submissions",
    summary="Get form submissions",
    description="Fetches and optionally processes volunteer signup form submissions, syncing to database and sending confirmation emails to new volunteers",
)
def get_signup_form_submissions(
    db: Session = Depends(get_db), process_new: bool = True
):
    """
    Fetch and optionally process volunteer signup form submissions from Google Sheets
    with graceful degradation for SSL and connection errors

    Args:
        db: Database session
        process_new: If True, process new submissions sync to database and send confirmation emails to new volunteers
    """
    import ssl
    
    try:
        logger.info("Fetching form submissions from Google Sheets...")
        submissions = sheets_service.get_signup_form_submissions(db)
        logger.info(f"Found {len(submissions)} form submissions from Google Sheets")

        # Initialize variables for both process_new=True and process_new=False cases
        new_submissions = []
        new_volunteers = []
        failed_submissions = []
        
        # Calculate status statistics for all cases
        total_submissions = len(submissions)
        accepted_submissions = len([sub for sub in submissions if sub.get("applicant_status", "").upper() == "ACCEPTED"])
        non_accepted_submissions = total_submissions - accepted_submissions

        if process_new:
            # Get all existing emails in one query
            existing_emails = set(
                email[0] for email in db.query(VolunteerModel.email).all()
            )
            logger.info(f"Found {len(existing_emails)} existing emails in database")
            
            # Filter new submissions - only process those with STATUS = 'ACCEPTED'
            new_submissions = [
                sub for sub in submissions 
                if sub["email_address"] not in existing_emails and sub.get("applicant_status", "").upper() == "ACCEPTED"
            ]
            
            # Log statistics about status filtering
            logger.info(f"Status filtering: {total_submissions} total submissions, {accepted_submissions} accepted, {non_accepted_submissions} non-accepted")
            
            # Log details about non-accepted submissions for transparency
            if non_accepted_submissions > 0:
                non_accepted_details = [
                    {"email": sub.get("email_address", "unknown"), "applicant_status": sub.get("applicant_status", "missing")}
                    for sub in submissions 
                    if sub.get("applicant_status", "").upper() != "ACCEPTED"
                ]
                logger.info(f"Skipped non-accepted submissions: {non_accepted_details}")
            
            logger.info(f"Found {len(new_submissions)} new submissions to process...")
            
            # Batch create new volunteers with error handling
            for submission in new_submissions:
                try:
                    volunteer = create_new_volunteer_object(submission)
                    new_volunteers.append(volunteer)
                except Exception as e:
                    logger.error(f"Failed to process submission for {submission.get('email_address', 'unknown')}: {str(e)}")
                    failed_submissions.append({
                        "email": submission.get('email_address', 'unknown'),
                        "error": str(e)
                    })
                    continue
            
            # Bulk insert with error handling
            if new_volunteers:
                try:
                    db.bulk_save_objects(new_volunteers)
                    db.commit()
                    logger.info(f"Added {len(new_volunteers)} new volunteers to database")
                    
                    # Log the created volunteers with their actual IDs after database commit
                    for volunteer in new_volunteers:
                        logger.info(f"New Volunteer {volunteer.email} created with id: {volunteer.id}")
                except Exception as e:
                    logger.error(f"Failed to save new volunteers to database: {str(e)}")
                    db.rollback()
                    return {
                        "status": "partial_failure",
                        "message": f"Retrieved {len(submissions)} form submissions but failed to save new volunteers",
                        "data": submissions,
                        "details": {
                            "submissions_retrieved": len(submissions),
                            "accepted_submissions": accepted_submissions,
                            "non_accepted_submissions": non_accepted_submissions,
                            "new_submissions_found": len(new_submissions),
                            "volunteers_created": len(new_volunteers),
                            "failed_submissions": failed_submissions,
                            "database_error": str(e)
                        }
                    }

            # Send confirmation emails to volunteers with no confirmation emails sent
            try:
                email_service.send_confirmation_emails(db)
            except Exception as e:
                logger.error(f"Failed to send confirmation emails: {str(e)}")

        # Determine response status based on results
        if failed_submissions:
            return {
                "status": "partial_failure",
                "message": f"Retrieved {len(submissions)} form submissions with some processing issues",
                "data": submissions,
                "details": {
                    "submissions_retrieved": len(submissions),
                    "accepted_submissions": accepted_submissions,
                    "non_accepted_submissions": non_accepted_submissions,
                    "new_submissions_found": len(new_submissions),
                    "volunteers_created": len(new_volunteers),
                    "failed_submissions": failed_submissions
                }
            }
        else:
            return {
                "status": "success",
                "message": f"Retrieved {len(submissions)} form submissions ({accepted_submissions} accepted, {non_accepted_submissions} non-accepted)",
                "data": submissions,
                "details": {
                    "submissions_retrieved": len(submissions),
                    "accepted_submissions": accepted_submissions,
                    "non_accepted_submissions": non_accepted_submissions,
                    "new_submissions_found": len(new_submissions),
                    "volunteers_created": len(new_volunteers)
                }
            }
            
    except ssl.SSLEOFError as e:
        log_ssl_error(e, "get_signup_form_submissions")
        logger.error(f"SSL EOF error while fetching form submissions: {str(e)}")
        return {
            "status": "partial_failure",
            "message": "Failed to fetch form submissions due to SSL connection issue",
            "data": [],
            "details": {
                "error_type": "ssl_eof_error",
                "error_message": str(e),
                "submissions_retrieved": 0,
                "accepted_submissions": 0,
                "non_accepted_submissions": 0,
                "new_submissions_found": 0,
                "volunteers_created": 0
            }
        }
    except Exception as e:
        logger.error(f"Failed to fetch form submissions: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to fetch form submissions: {str(e)}",
            "data": [],
            "details": {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "submissions_retrieved": 0,
                "accepted_submissions": 0,
                "non_accepted_submissions": 0,
                "new_submissions_found": 0,
                "volunteers_created": 0
            }
        }


def create_new_volunteer_object(submission: dict) -> VolunteerModel:
    """Create a new volunteer object without database operations"""
    # Combine first and last name for the full name
    first_name = submission.get("first_name", "").strip()
    last_name = submission.get("last_name", "").strip()
    full_name = f"{first_name} {last_name}".strip()
    
    # Handle empty name case
    if not full_name:
        full_name = "Unknown Volunteer"
    
    return VolunteerModel(
        name=full_name,
        email=submission["email_address"],
        phone=submission["phone_number"],
        positions=[pos.strip() for pos in submission["position_interest"].split(",")] if submission.get("position_interest") else [],
        location=submission["location"],
        availability=[slot.strip() for slot in submission["availability"].split(",")] if submission.get("availability") else [],
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
        # Store additional info including social media and referral source
        additional_info=f"Social Media: {submission.get('social_media_link', 'N/A')}\nReferral Source: {submission.get('referral_source', 'N/A')}",
        is_active=True,
    )


@admin_router.post("/volunteers/{volunteer_id}/send-confirmation")
def send_confirmation_email_to_volunteer(
    volunteer_id: int, db: Session = Depends(get_db)
):
    """Manually send a confirmation email to a specific volunteer"""
    try:
        volunteer = (
            db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        )
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        # Check if confirmation email was already sent
        existing_confirmation = (
            db.query(EmailCommunicationModel)
            .filter(
                EmailCommunicationModel.volunteer_id == volunteer_id,
                EmailCommunicationModel.email_type == "volunteer_confirmation",
                EmailCommunicationModel.status == "sent",
            )
            .first()
        )

        if existing_confirmation:
            logger.info(
                f"Confirmation email already sent to {volunteer.email} on {existing_confirmation.sent_at}"
            )
            return {
                "status": "info",
                "message": f"Confirmation email already sent to {volunteer.email} on {existing_confirmation.sent_at}",
                "volunteer_email": volunteer.email,
                "last_sent": existing_confirmation.sent_at.isoformat(),
            }

        # Send confirmation email
        success = email_service.send_confirmation_email(db, volunteer)

        if success:
            # REMOVED: Update Google Sheets to reflect the confirmation was sent
            # Database is now the source of truth; no write-back to Sheets.
            return {
                "status": "success",
                "message": f"Confirmation email sent successfully to {volunteer.email}",
                "volunteer_email": volunteer.email,
            }
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to send confirmation email to {volunteer.email}",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to send confirmation email to volunteer {volunteer_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to send confirmation email: {str(e)}"
        )


@admin_router.post("/volunteers/{volunteer_id}/reset-confirmation")
def reset_confirmation_email_status(volunteer_id: int, db: Session = Depends(get_db)):
    """Reset confirmation email status for a volunteer (mark as not sent)"""
    try:
        volunteer = (
            db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        )
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        # Delete existing confirmation email records
        deleted_count = (
            db.query(EmailCommunicationModel)
            .filter(
                EmailCommunicationModel.volunteer_id == volunteer_id,
                EmailCommunicationModel.email_type == "volunteer_confirmation",
            )
            .delete()
        )

        # REMOVED: Update Google Sheets to reflect confirmation was not sent
        # Database is now the source of truth; no write-back to Sheets.
        db.commit()

        return {
            "status": "success",
            "message": f"Confirmation email status reset for {volunteer.email}",
            "volunteer_email": volunteer.email,
            "deleted_records": deleted_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to reset confirmation status for volunteer {volunteer_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to reset confirmation status: {str(e)}"
        )


@admin_router.post("/volunteers/{volunteer_id}/resubscribe")
def resubscribe_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Manually resubscribe a volunteer to emails (admin only)"""
    try:
        volunteer = (
            db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        )
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        volunteer.weekly_reminders_subscribed = True
        volunteer.all_emails_subscribed = True

        # Log the resubscribe action
        email_comm = EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="resubscribe_all",
            subject="Resubscribe Request - All Emails",
            template_name=None,
            status="sent",
            sent_at=datetime.now(),
        )
        db.add(email_comm)
        db.commit()

        logger.info(f"Volunteer {volunteer.email} resubscribed to all emails by admin")

        return {
            "status": "success",
            "message": f"Volunteer {volunteer.email} has been resubscribed to all emails",
            "volunteer_email": volunteer.email,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to resubscribe volunteer {volunteer_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to resubscribe volunteer: {str(e)}"
        )


@admin_router.post("/volunteers/{volunteer_id}/resubscribe-weekly")
def resubscribe_volunteer_weekly(volunteer_id: int, db: Session = Depends(get_db)):
    """Manually resubscribe a volunteer to weekly reminders only (admin only)"""
    try:
        volunteer = (
            db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        )
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        volunteer.weekly_reminders_subscribed = True

        # Log the resubscribe action
        email_comm = EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="resubscribe_weekly",
            subject="Resubscribe Request - Weekly Reminders",
            template_name=None,
            status="sent",
            sent_at=datetime.now(),
        )
        db.add(email_comm)
        db.commit()

        logger.info(
            f"Volunteer {volunteer.email} resubscribed to weekly reminders by admin"
        )

        return {
            "status": "success",
            "message": f"Volunteer {volunteer.email} has been resubscribed to weekly reminders",
            "volunteer_email": volunteer.email,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to resubscribe volunteer {volunteer_id} to weekly reminders: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to resubscribe volunteer: {str(e)}"
        )


@admin_router.post("/volunteers/{volunteer_id}/deactivate")
def deactivate_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Soft delete a volunteer by setting is_active to False (admin only)"""
    try:
        volunteer = (
            db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        )
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        if not volunteer.is_active:
            return {
                "status": "info",
                "message": f"Volunteer {volunteer.email} is already deactivated",
                "volunteer_email": volunteer.email,
            }

        # Soft delete by setting is_active to False
        volunteer.is_active = False

        # Log the deactivation action
        email_comm = EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="volunteer_deactivated",
            subject="Volunteer Account Deactivated",
            template_name=None,
            status="sent",
            sent_at=datetime.now(),
        )
        db.add(email_comm)
        db.commit()

        logger.info(f"Volunteer {volunteer.email} deactivated by admin")

        return {
            "status": "success",
            "message": f"Volunteer {volunteer.email} has been deactivated",
            "volunteer_email": volunteer.email,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to deactivate volunteer {volunteer_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to deactivate volunteer: {str(e)}"
        )


@admin_router.post("/volunteers/{volunteer_id}/reactivate")
def reactivate_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Reactivate a volunteer by setting is_active to True (admin only)"""
    try:
        volunteer = (
            db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        )
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        if volunteer.is_active:
            return {
                "status": "info",
                "message": f"Volunteer {volunteer.email} is already active",
                "volunteer_email": volunteer.email,
            }

        # Reactivate by setting is_active to True
        volunteer.is_active = True

        # Log the reactivation action
        email_comm = EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="volunteer_reactivated",
            subject="Volunteer Account Reactivated",
            template_name=None,
            status="sent",
            sent_at=datetime.now(),
        )
        db.add(email_comm)
        db.commit()

        logger.info(f"Volunteer {volunteer.email} reactivated by admin")

        return {
            "status": "success",
            "message": f"Volunteer {volunteer.email} has been reactivated",
            "volunteer_email": volunteer.email,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to reactivate volunteer {volunteer_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to reactivate volunteer: {str(e)}"
        )


@admin_router.post("/volunteers/{volunteer_id}/send-weekly-reminder")
def send_weekly_reminder_to_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Send a weekly reminder email to a specific volunteer (admin only)"""
    try:
        # Check if weekly reminders are globally enabled
        if not ConfigHelper.get_weekly_reminders_enabled(db):
            raise HTTPException(
                status_code=400, 
                detail="Weekly reminders are currently disabled globally. Enable them in the Settings tab to send weekly reminders."
            )

        # Get the volunteer
        volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        if not volunteer.is_active:
            raise HTTPException(
                status_code=400, detail=f"Volunteer {volunteer.email} is not active"
            )

        if not volunteer.weekly_reminders_subscribed:
            raise HTTPException(
                status_code=400, detail=f"Volunteer {volunteer.email} is not subscribed to weekly reminders"
            )

        # Build email content using email service
        html_body, subject = email_service.build_weekly_reminder_content(volunteer, db)

        # Send the email
        success = email_service.send_custom_email(
            to_email=volunteer.email,
            subject=subject,
            html_body=html_body,
            db=db,
            volunteer_id=volunteer.id,
            email_type="weekly_reminder"
        )

        if success:
            logger.info(f"✅ Weekly reminder email sent to {volunteer.email}")
            return {
                "status": "success",
                "message": f"Weekly reminder email sent to {volunteer.email}",
                "volunteer_id": volunteer_id,
                "email": volunteer.email
            }
        else:
            raise HTTPException(
                status_code=500, detail=f"Failed to send weekly reminder email to {volunteer.email}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send weekly reminder to volunteer {volunteer_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to send weekly reminder email: {str(e)}"
        )


@admin_router.get("/config/validate")
def validate_configuration(db: Session = Depends(get_db)):
    """Validate that all required configuration is set"""
    try:
        missing_settings = []
        
        # Check required Google Sheets settings
        schedule_sheet_id = ConfigHelper.get_schedule_sheet_id(db)
        if not schedule_sheet_id:
            missing_settings.append("SCHEDULE_SHEETS_LINK")
        
        new_signups_sheet_id = ConfigHelper.get_new_signups_sheet_id(db)
        if not new_signups_sheet_id:
            missing_settings.append("NEW_SIGNUPS_RESPONSES_LINK")
        
        if missing_settings:
            return {
                "status": "error",
                "message": f"Missing required settings: {', '.join(missing_settings)}",
                "missing_settings": missing_settings,
                "instructions": "Please configure these settings via the /settings/ endpoint"
            }
        
        return {
            "status": "success",
            "message": "All required configuration is set",
            "schedule_sheet_id": schedule_sheet_id,
            "new_signups_sheet_id": new_signups_sheet_id
        }
        
    except Exception as e:
        logger.error(f"Configuration validation failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Configuration validation failed: {str(e)}"
        )
