"""
Admin API endpoints for manual control and monitoring

These endpoints provide admin functionality for:
- Manual triggers (weekly reminders, schedule rotation)
- Monitoring and statistics
- Volunteer and email management
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Dict, Any
import asyncio
import functools
from jinja2 import Template

from app.database import get_db, get_db_session
from app.models import (
    Volunteer as VolunteerModel,
    EmailCommunication as EmailCommunicationModel,
)
from app.schemas import Volunteer
from app.services.google_sheets import sheets_service
from app.services.email_service import email_service
from app.services.classes_config import CLASS_CONFIG
from app.utils.logging_config import get_api_logger
from app.config import ENVIRONMENT
from app.utils.config_helper import ConfigHelper
from app.utils.retry_utils import log_ssl_error
from app.services.supabase_auth import get_current_admin_user

logger = get_api_logger()
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
)

# Initialize templates
templates = Jinja2Templates(directory="templates")


def timeout_handler(timeout_seconds: float = 30.0):
    """Decorator to add timeout protection to async functions"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                logger.error(f"Function {func.__name__} timed out after {timeout_seconds} seconds")
                raise HTTPException(
                    status_code=504,
                    detail=f"Operation timed out after {timeout_seconds} seconds"
                )
        return wrapper
    return decorator


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

        from app.config import APPLICATION_VERSION
        
        return templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            {
                "total_volunteers": len(volunteer_data),
                "volunteers": volunteer_data,
                "total_emails": len(email_data),
                "emails": email_data,
                "settings": settings,
                "version": APPLICATION_VERSION,
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


# Admin Management Endpoints
from pydantic import BaseModel
from typing import Optional
from app.services.supabase_auth import get_current_admin_user


class AdminCreateRequest(BaseModel):
    """Request model for creating a new admin"""
    email: str
    role: str = "admin"  # "admin" or "super_admin"


class AdminRoleUpdateRequest(BaseModel):
    """Request model for updating admin role"""
    role: str  # "admin" or "super_admin"


@admin_router.get("/users")
@timeout_handler(timeout_seconds=30.0)
async def get_admins(current_admin: Dict[str, Any] = Depends(get_current_admin_user)):
    """Get all admin users and current user info"""
    try:
        from app.services.admin_user_service import AdminUserService
        from app.services.supabase_auth import supabase
        
        admin_service = AdminUserService(supabase)
        
        # Get admin users from database
        admin_users = await admin_service.get_admin_users(current_admin["email"])
        
        # Get current user info
        current_user = {
            "email": current_admin["email"],
            "role": "admin"  # Default role, will be updated below
        }
        
        # Find current user in admin list to get their role
        for admin_user in admin_users:
            if admin_user.email == current_admin["email"]:
                current_user["role"] = admin_user.role
                break
        
        # Convert to dict format for frontend
        admins = []
        for admin_user in admin_users:
            admins.append({
                "email": admin_user.email,
                "role": admin_user.role,
                "status": "active" if admin_user.is_active else "inactive",
                "last_login": admin_user.last_login.isoformat() if admin_user.last_login else None,
                "created_at": admin_user.created_at.isoformat() if admin_user.created_at else None
            })
        
        logger.info(f"Admin {current_admin['email']} retrieved admin list")
        
        return {
            "current_user": current_user,
            "admins": admins,
            "total": len(admins),
            "message": "Admin list retrieved successfully"
        }
        
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get admins: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/users")
@timeout_handler(timeout_seconds=30.0)
async def create_admin(
    request: AdminCreateRequest,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user)
):
    """Add a new admin user"""
    try:
        logger.info(f"Creating admin user: {request.email} with role: {request.role}")
        
        from app.services.admin_user_service import AdminUserService
        from app.services.supabase_auth import supabase
        
        # Validate email format
        if not request.email or "@" not in request.email:
            logger.warning(f"Invalid email format: {request.email}")
            raise HTTPException(
                status_code=400,
                detail="Invalid email address"
            )
        
        # Validate role
        if request.role not in ["admin", "super_admin"]:
            logger.warning(f"Invalid role: {request.role}")
            raise HTTPException(
                status_code=400,
                detail="Invalid role. Must be 'admin' or 'super_admin'"
            )
        
        logger.info(f"Initializing AdminUserService for {current_admin['email']}")
        admin_service = AdminUserService(supabase)
        
        # Add admin user with timeout protection
        logger.info(f"Adding admin user {request.email}...")
        success = await admin_service.add_admin_user(
            email=request.email,
            role=request.role,
            added_by_email=current_admin["email"]
        )
        
        if success:
            logger.info(f"Super admin {current_admin['email']} added admin {request.email}")
            return {
                "status": "success",
                "message": f"Admin {request.email} added successfully with role {request.role}",
                "admin_email": request.email,
                "role": request.role
            }
        else:
            logger.warning(f"Failed to add admin {request.email} - operation returned False")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to add admin {request.email}. They may already exist."
            )
        
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create admin: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.put("/users/{email}/role")
@timeout_handler(timeout_seconds=30.0)
async def update_admin_role(
    email: str,
    request: AdminRoleUpdateRequest,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user)
):
    """Update admin role"""
    try:
        from app.services.admin_user_service import AdminUserService
        from app.services.supabase_auth import supabase
        
        # Validate role
        if request.role not in ["admin", "super_admin"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid role. Must be 'admin' or 'super_admin'"
            )
        
        # Prevent removing the last super admin
        if request.role == "admin" and email == current_admin["email"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote yourself from super admin"
            )
        
        admin_service = AdminUserService(supabase)
        
        # Update admin role
        success = await admin_service.update_admin_role(
            email=email,
            new_role=request.role,
            updated_by_email=current_admin["email"]
        )
        
        if success:
            logger.info(f"Super admin {current_admin['email']} changed {email} role to {request.role}")
            return {
                "status": "success",
                "message": f"Admin {email} role updated to {request.role}",
                "admin_email": email,
                "new_role": request.role
            }
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Admin {email} not found or update failed"
            )
        
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update admin role: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.delete("/users/{email}")
@timeout_handler(timeout_seconds=30.0)
async def remove_admin(
    email: str,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user)
):
    """Remove an admin user (deactivate)"""
    try:
        from app.services.admin_user_service import AdminUserService
        from app.services.supabase_auth import supabase
        
        # Prevent removing yourself
        if email == current_admin["email"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove yourself as admin"
            )
        
        admin_service = AdminUserService(supabase)
        
        # Remove admin user (deactivate)
        success = await admin_service.remove_admin_user(
            email=email,
            removed_by_email=current_admin["email"]
        )
        
        if success:
            logger.info(f"Super admin {current_admin['email']} removed admin {email}")
            return {
                "status": "success",
                "message": f"Admin {email} has been deactivated",
                "admin_email": email,
                "action": "deactivated"
            }
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Admin {email} not found or removal failed"
            )
        
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove admin: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.delete("/users/{email}/permanent")
@timeout_handler(timeout_seconds=30.0)
async def delete_admin_permanently(
    email: str,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user)
):
    """Permanently delete an admin user from database"""
    try:
        from app.services.admin_user_service import AdminUserService
        from app.services.supabase_auth import supabase
        
        # Prevent deleting yourself
        if email == current_admin["email"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete yourself as admin"
            )
        
        admin_service = AdminUserService(supabase)
        
        # Permanently delete admin user
        success = await admin_service.delete_admin_user(
            email=email,
            deleted_by_email=current_admin["email"]
        )
        
        if success:
            logger.info(f"Super admin {current_admin['email']} permanently deleted admin {email}")
            return {
                "status": "success",
                "message": f"Admin {email} has been permanently deleted from database",
                "admin_email": email,
                "action": "permanently_deleted"
            }
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Admin {email} not found or deletion failed"
            )
        
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete admin: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.get("/users/all")
@timeout_handler(timeout_seconds=30.0)
async def get_all_admins(current_admin: Dict[str, Any] = Depends(get_current_admin_user)):
    """Get all admin users including inactive ones (for super admins)"""
    try:
        from app.services.admin_user_service import AdminUserService
        from app.services.supabase_auth import supabase
        
        admin_service = AdminUserService(supabase)
        
        # Get all admin users including inactive ones
        admin_users = await admin_service.get_all_admin_users(current_admin["email"])
        
        # Get current user info
        current_user = {
            "email": current_admin["email"],
            "role": "admin"  # Default role, will be updated below
        }
        
        # Find current user in admin list to get their role
        for admin_user in admin_users:
            if admin_user.email == current_admin["email"]:
                current_user["role"] = admin_user.role
                break
        
        # Convert to dict format for frontend
        admins = []
        for admin_user in admin_users:
            admins.append({
                "email": admin_user.email,
                "role": admin_user.role,
                "status": "active" if admin_user.is_active else "inactive",
                "last_login": admin_user.last_login.isoformat() if admin_user.last_login else None,
                "created_at": admin_user.created_at.isoformat() if admin_user.created_at else None
            })
        
        logger.info(f"Super admin {current_admin['email']} retrieved all admin list (including inactive)")
        
        return {
            "current_user": current_user,
            "admins": admins,
            "total": len(admins),
            "active_count": len([a for a in admins if a["status"] == "active"]),
            "inactive_count": len([a for a in admins if a["status"] == "inactive"]),
            "message": "All admin list retrieved successfully (including inactive users)"
        }
        
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get all admins: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.get("/health")
async def admin_health_check():
    """Health check for admin system"""
    try:
        from app.services.admin_user_service import AdminUserService
        from app.services.supabase_auth import supabase
        from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        
        health_status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "supabase_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY),
            "supabase_client_initialized": supabase is not None,
            "admin_service_available": False,
            "database_accessible": False
        }
        
        # Test admin service initialization
        try:
            admin_service = AdminUserService(supabase)
            health_status["admin_service_available"] = True
            
            # Test database connection with timeout
            try:
                await asyncio.wait_for(
                    admin_service.is_admin("test@example.com"),
                    timeout=5.0
                )
                health_status["database_accessible"] = True
            except asyncio.TimeoutError:
                health_status["database_accessible"] = False
                health_status["status"] = "degraded"
                health_status["error"] = "Database connection timeout"
            except Exception as e:
                health_status["database_accessible"] = False
                health_status["status"] = "degraded"
                health_status["error"] = f"Database connection error: {str(e)}"
                
        except Exception as e:
            health_status["admin_service_available"] = False
            health_status["status"] = "unhealthy"
            health_status["error"] = f"Admin service error: {str(e)}"
        
        return health_status
        
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }


HEADER_ROW = 0
TEACHER_ROW = 1
ASSISTANT_ROW = 2
MIN_REQUIRED_ROWS = 3


def build_class_table(class_name, config, sheet_service, db):
    """Build HTML table for a specific class, with Day/Teacher/Assistant(s)/Status columns"""
    try:
        sheet_range = config.get("sheet_range")
        class_time = config.get("time", "")
        if not sheet_range:
            logger.error(f"No sheet_range configured for class {class_name}")
            return {
                "class_name": class_name,
                "table_html": f"<p>No sheet range configured for {class_name}</p>",
                "has_data": False,
            }
        class_data = sheet_service.get_schedule_range(db, sheet_range)
        if not class_data or len(class_data) < MIN_REQUIRED_ROWS:
            return {
                "class_name": class_name,
                "table_html": f"<p>No data available for {class_name}</p>",
                "has_data": False,
        }

        # Transpose data: columns are days, rows are header/teacher/assistant
        # class_data: [header_row, teacher_row, assistant_row]
        days = class_data[HEADER_ROW][1:]  # skip first col (label)
        teachers = class_data[TEACHER_ROW][1:]
        assistants = class_data[ASSISTANT_ROW][1:]

        # Table header
        table_html = f"<h3>{class_name} ({class_time})</h3>"
        table_html += "<table border='1' style='border-collapse: collapse; width: 100%;'>"
        table_html += "<thead><tr style='background-color: #f0f0f0;'>"
        table_html += "<th style='padding: 8px; text-align: center;'>Day</th>"
        table_html += "<th style='padding: 8px; text-align: center;'>Teacher</th>"
        table_html += "<th style='padding: 8px; text-align: center;'>Assistant(s)</th>"
        table_html += "<th style='padding: 8px; text-align: center;'>Status</th>"
        table_html += "</tr></thead><tbody>"

        for i, day in enumerate(days):
            teacher = teachers[i] if i < len(teachers) else ""
            assistant = assistants[i] if i < len(assistants) else ""
            # Status logic
            status = ""
            bg_color = ""
            teacher_lower = teacher.strip().lower() if teacher else ""
            assistant_lower = assistant.strip().lower() if assistant else ""
            if "optional" in teacher_lower:
                status = "optional day, volunteers welcome to support existing classes"
                bg_color = "#f5f5f5"
            elif "no class" in teacher_lower:
                if "holiday" in teacher_lower:
                    status = "No class: holiday"
                else:
                    status = "No class"
                bg_color = "#f5f5f5"
            elif "need volunteers" in teacher_lower:
                status = "❌ Missing Teacher"
                bg_color = "#ffcccc"
            elif "need volunteers" in assistant_lower:
                status = "❌ Missing TA's"
                bg_color = "#fff3cd"
            else:
                status = "✅ Fully Covered, TA's welcome to join"
                bg_color = "#d4edda"

            table_html += f"<tr style='background-color: {bg_color};'>"
            table_html += f"<td style='padding: 8px; text-align: center;'>{day}</td>"
            table_html += f"<td style='padding: 8px; text-align: center;'>{teacher}</td>"
            table_html += f"<td style='padding: 8px; text-align: center;'>{assistant}</td>"
            table_html += f"<td style='padding: 8px; text-align: center;'>{status}</td>"
            table_html += "</tr>"

        table_html += "</tbody></table>"
        return {"class_name": class_name, "table_html": table_html, "has_data": True}

    except Exception as e:
        logger.error(f"Failed to build table for {class_name}: {str(e)}")
        return {
            "class_name": class_name,
            "table_html": f"<p>Error loading data for {class_name}</p>",
            "has_data": False,
        }


# API endpoints moved from api.py to admin router
@admin_router.post("/send-confirmation-emails")
async def send_confirmation_emails(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(get_current_admin_user)
):
    """Process emails for new volunteers - accessible by admin dashboard and scheduler"""

    try:
        email_service.send_confirmation_emails(db)
        return {"status": "success", "message": "Confirmation emails sent successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in send confirmation emails: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": "Failed to send confirmation emails due to unexpected error",
            "details": {"error": str(e)}
        }


@admin_router.post("/sync-volunteers")
async def sync_volunteers(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(get_current_admin_user)
):
    """Sync volunteers from Google Sheets and process new signups with graceful degradation"""

    try:
        # Try to sync volunteers with graceful degradation
        result = get_signup_form_submissions(db=db, process_new=True)
        
        # Check if the sync was successful or had partial failures
        if result.get("status") == "success":
            return {"status": "success", "message": "Volunteers synced successfully"}
        elif result.get("status") == "partial_failure":
            return {
                "status": "partial_success", 
                "message": "Volunteers synced with some issues",
                "details": result.get("details", {})
            }
        else:
            # If there was a complete failure, return error but don't raise HTTPException
            return {
                "status": "error",
                "message": "Failed to sync volunteers",
                "details": result.get("details", {})
            }
            
    except HTTPException:
        raise
    except Exception as e:
        # Log the error but return a graceful response instead of raising
        logger.error(f"Unexpected error in sync volunteers: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": "Failed to sync volunteers due to unexpected error",
            "details": {"error": str(e)}
        }


@admin_router.post("/send-weekly-reminders")
async def send_weekly_reminder_emails(
    request: Request,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user)
):
    """Send weekly reminder emails to all active volunteers - accessible by admin dashboard and scheduler"""
    
    try:
        with get_db_session() as db:
            # Check if weekly reminders are globally enabled
            if not ConfigHelper.get_weekly_reminders_enabled(db):
                logger.warning("Weekly reminders are globally disabled, skipping bulk send")
                return {
                    "status": "skipped",
                    "message": "Weekly reminders are currently disabled globally. Enable them in the admin settings to send weekly reminders.",
                }

            if ConfigHelper.get_dry_run(db):
                logger.info(
                    "DRY_RUN is enabled, sending emails to dry run email recipient only"
                )
                dry_run_volunteer = (
                    db.query(VolunteerModel)
                    .filter(VolunteerModel.email == ConfigHelper.get_dry_run_email_recipient(db))
                    .first()
                )
                volunteers = [
                    {"email": dry_run_volunteer.email, "name": dry_run_volunteer.name}
                ]
                volunteer_lookup = {dry_run_volunteer.email: dry_run_volunteer}
            else:
                # Get volunteers from database with lookup dictionary to avoid N+1 queries
                db_volunteers = (
                    db.query(VolunteerModel)
                    .filter(VolunteerModel.is_active == True)
                    .filter(
                        VolunteerModel.weekly_reminders_subscribed == True
                    )  # Only send to volunteers subscribed to weekly reminders
                    .all()
                )

                volunteers = [
                    {"email": volunteer.email, "name": volunteer.name}
                    for volunteer in db_volunteers
                ]
                volunteer_lookup = {
                    volunteer.email: volunteer for volunteer in db_volunteers
                }

            logger.info(
                f"Sending weekly reminder emails to {len(volunteers)} volunteers"
            )

            # Build class tables once
            class_tables = []
            for class_name, config in CLASS_CONFIG.items():
                class_tables.append(
                    build_class_table(class_name, config, sheets_service, db)
                )

            # Get current schedule dates from the visible sheet
            current_monday, current_friday = sheets_service.get_current_schedule_dates(db)
            subject = email_service.get_reminder_subject(current_monday, current_friday)

            # Process emails in batches for better performance
            email_communications = []

            for volunteer in volunteers:
                # Look up volunteer in database using pre-built lookup
                db_volunteer = volunteer_lookup.get(volunteer["email"])
                logger.info(
                    f"Volunteer {volunteer['email']} found in database: {db_volunteer}"
                )

                if not db_volunteer:
                    logger.warning(
                        f"Volunteer {volunteer['email']} not found in database, skipping"
                    )
                    continue

                # Generate unsubscribe token if not exists
                if not db_volunteer.email_unsubscribe_token:
                    db_volunteer.email_unsubscribe_token = (
                        email_service.generate_unsubscribe_token()
                    )
                    db.commit()

                first_name = db_volunteer.name.split()[0] if db_volunteer.name else "Volunteer"
                from jinja2 import Template
                html_body = Template(email_service.reminder_template).render(
                    first_name=first_name,
                    class_tables=[ct['table_html'] for ct in class_tables],
                    SCHEDULE_SIGNUP_LINK=ConfigHelper.get_schedule_signup_link(db) or "#",
                    EMAIL_PREFERENCES_LINK=email_service.get_volunteer_unsubscribe_link(db_volunteer, db),
                    INVITE_LINK_FACEBOOK_MESSENGER=ConfigHelper.get_invite_link_facebook_messenger(db) or "#",
                    INVITE_LINK_DISCORD=ConfigHelper.get_invite_link_discord(db) or "#",
                    ONBOARDING_GUIDE_LINK=ConfigHelper.get_onboarding_guide_link(db) or "#",
                    INSTAGRAM_LINK=ConfigHelper.get_instagram_link(db) or "#",
                    FACEBOOK_PAGE_LINK=ConfigHelper.get_facebook_page_link(db) or "#",
                )

                # Create email communication record
                email_comm = EmailCommunicationModel(
                    volunteer_id=db_volunteer.id,
                    recipient_email=volunteer["email"],
                    email_type="weekly_reminder",
                    subject=subject,
                    status="pending",
                )
                email_communications.append((email_comm, html_body))

            # Batch insert email communications
            for email_comm, _ in email_communications:
                db.add(email_comm)
            db.commit()

            # Send emails and update status
            for email_comm, html_body in email_communications:
                try:
                    email_service.send_custom_email(
                        to_email=email_comm.recipient_email,
                        subject=email_comm.subject,
                        html_body=html_body,
                    )
                    email_comm.status = "sent"
                    email_comm.sent_at = datetime.now()
                except Exception as e:
                    logger.error(
                        f"Failed to send weekly reminder to {email_comm.recipient_email}: {str(e)}"
                    )
                    email_comm.status = "failed"
                    email_comm.error_message = str(e)

            # Batch update statuses
            db.commit()

        logger.info(f"✅ Weekly reminder emails sent. DRY_RUN={ConfigHelper.get_dry_run(db)}")
        return {
            "status": "success",
            "message": "Weekly reminder emails sent successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in send weekly reminder emails: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": "Failed to send weekly reminder emails due to unexpected error",
            "details": {"error": str(e)}
        }


@admin_router.post("/rotate-schedule")
async def rotate_schedule_sheets(
    request: Request,
    display_weeks: int = Query(None, description="Number of weeks to display (1-12), overrides default setting"),
    current_admin: Dict[str, Any] = Depends(get_current_admin_user)
):
    """Rotate schedule sheets to show next week - accessible by admin dashboard and scheduler"""

    try:
        with get_db_session() as db:
            # If display_weeks is provided, use it; otherwise use the default from settings
            if display_weeks is not None:
                # Validate the parameter
                if display_weeks < 1 or display_weeks > 12:
                    return {
                        "status": "error",
                        "message": "display_weeks must be between 1 and 12",
                        "details": {"error": "Invalid display_weeks parameter"}
                    }
                result = sheets_service.rotate_schedule_sheets(db, display_weeks_override=display_weeks)
            else:
                result = sheets_service.rotate_schedule_sheets(db)
            
            return {
                "status": "success", 
                "message": "Schedule sheets rotated successfully",
                "details": result
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in rotate schedule sheets: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": "Failed to rotate schedule sheets due to unexpected error",
            "details": {"error": str(e)}
        }


@admin_router.get("/health")
def health_check(db: Session = Depends(get_db), current_admin: Dict[str, Any] = Depends(get_current_admin_user)):
    """Health check endpoint for admin API"""
    try:
        # Test database connection
        db.execute("SELECT 1")
        
        # Test Google Sheets connection
        sheets_status = "unknown"
        try:
            sheets_service.get_current_schedule_dates(db)
            sheets_status = "connected"
        except Exception as e:
            sheets_status = f"error: {str(e)}"
        
        # Test email service
        email_status = "unknown"
        try:
            # Just test if the service is initialized
            email_service.get_reminder_subject(datetime.now().date(), datetime.now().date())
            email_status = "available"
        except Exception as e:
            email_status = f"error: {str(e)}"
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": "connected",
            "google_sheets": sheets_status,
            "email_service": email_status,
            "environment": ENVIRONMENT
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }
