"""
API endpoints for schedule management

These endpoints provide schedule-related functionality:
- Schedule rotation
- Email sending
- Volunteer synchronization

All endpoints require Google OAuth authentication.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
import traceback
from app.database import get_db, get_db_session
from app.services.email_service import email_service
from app.utils.logging_config import get_api_logger
from app.services.google_sheets import sheets_service
from jinja2 import Template
from datetime import datetime
from app.models import (
    Volunteer as VolunteerModel,
    EmailCommunication as EmailCommunicationModel,
)
from app.services.classes_config import CLASS_CONFIG
from app.utils.config_helper import ConfigHelper

logger = get_api_logger()

# Create API router
api_router = APIRouter(prefix="/api", tags=["api"])

# Constants for sheet data structure
HEADER_ROW = 0
TEACHER_ROW = 1
ASSISTANT_ROW = 2
MIN_REQUIRED_ROWS = 3


def handle_scheduler_error(context: str, error: Exception):
    """
    Centralized error handler for scheduler endpoints.
    Logs the error and raises a standardized HTTPException.
    """
    # Log the error with context and stack trace
    logger.error(
        f"Scheduler error in {context}: {str(error)}\n{traceback.format_exc()}"
    )
    # Return a generic error to the client (avoid leaking internals)
    raise HTTPException(
        status_code=500, detail=f"Scheduler error in {context}: {str(error)}"
    )


def get_scheduler_context(request: Request) -> dict:
    """Get scheduler context info for logging"""
    return {
        "service_email": getattr(request.state, "service_email", "unknown"),
        "auth_type": getattr(request.state, "auth_type", "unknown"),
    }


@api_router.post("/send-confirmation-emails")
def send_confirmation_emails(
    request: Request,
    db: Session = Depends(get_db)
):
    """Process emails for new volunteers"""
    scheduler_info = get_scheduler_context(request)
    logger.info(
        f"Scheduler service {scheduler_info['service_email']} running confirmation email processing"
    )

    try:
        email_service.send_confirmation_emails(db)
        return {"status": "success", "message": "Confirmation emails sent successfully"}
    except HTTPException:
        raise
    except Exception as e:
        handle_scheduler_error("process confirmation emails", e)


@api_router.post("/sync-volunteers")
def sync_volunteers(
    request: Request,
    db: Session = Depends(get_db)
):
    """Sync volunteers from Google Sheets and process new signups with graceful degradation"""
    scheduler_info = get_scheduler_context(request)
    logger.info(
        f"Scheduler service {scheduler_info['service_email']} syncing volunteers from Google Sheets"
    )

    try:
        from app.routers.admin import get_signup_form_submissions

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


@api_router.post("/send-weekly-reminders")
def send_weekly_reminder_emails():
    """
    Send weekly reminder emails to volunteers
    
    This endpoint uses manual database session management because it performs
    complex operations with multiple commits and rollbacks that need fine-grained control.
    """
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
                    "DRY_RUN is enabled, sending emails to dry run email recipient"
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

                # Build email content using email service
                html_body, subject = email_service.build_weekly_reminder_content(db_volunteer, db)

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
        handle_scheduler_error("send weekly reminder emails", e)


@api_router.post("/rotate-schedule")
def rotate_schedule_sheets(request: Request):
    """Rotate schedule sheets to show next week"""
    scheduler_info = get_scheduler_context(request)
    logger.info(
        f"Scheduler service {scheduler_info['service_email']} rotating schedule sheets"
    )

    try:
        with get_db_session() as db:
            sheets_service.rotate_schedule_sheets(db)
            return {"status": "success", "message": "Schedule sheets rotated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        handle_scheduler_error("rotate schedule sheets", e)


@api_router.get("/health")
def health_check():
    """Health check endpoint to verify service status and Google Sheets connectivity"""
    try:
        # Quick test of Google Sheets API connectivity
        test_result = sheets_service.get_signup_form_submissions()
        return {
            "status": "healthy",
            "google_sheets_connectivity": "ok",
            "submissions_count": len(test_result) if test_result else 0
        }
    except Exception as e:
        return {
            "status": "unhealthy", 
            "google_sheets_connectivity": "error",
            "error": str(e),
            "error_type": type(e).__name__
        }
