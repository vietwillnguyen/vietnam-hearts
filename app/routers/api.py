"""
API endpoints for schedule management

These endpoints provide schedule-related functionality:
- Schedule rotation
- Email sending
- Volunteer synchronization

All endpoints require authentication (Supabase admin or Google Cloud Scheduler).
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
import traceback
from app.database import get_db, get_db_session
from app.services.email_service import email_service
from app.utils.logging_config import get_api_logger
from app.services.google_sheets import sheets_service
from contextlib import contextmanager
from datetime import datetime
from app.models import (
    Volunteer as VolunteerModel,
    EmailCommunication as EmailCommunicationModel,
)
from app.services.classes_config import CLASS_CONFIG
from app.config import (
    GOOGLE_OAUTH_CLIENT_ID,
    SERVICE_ACCOUNT_EMAIL,
)
from app.utils.config_helper import ConfigHelper
from app.utils.retry_utils import safe_api_call, log_ssl_error
from app.utils.auth import require_admin_or_scheduler_auth, get_auth_context
from typing import List, Dict, Any
import ssl
from typing import Callable
from jinja2 import Template

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


@api_router.post("/send-confirmation-emails")
async def send_confirmation_emails(
    request: Request,
    _: None = Depends(require_admin_or_scheduler_auth),
    db: Session = Depends(get_db)
):
    """Process emails for new volunteers - accessible by admin dashboard and scheduler"""
    auth_info = get_auth_context(request)
    logger.info(
        f"User/service {auth_info} running confirmation email processing"
    )

    try:
        email_service.send_confirmation_emails(db)
        return {"status": "success", "message": "Confirmation emails sent successfully"}
    except HTTPException:
        raise
    except Exception as e:
        handle_scheduler_error("process confirmation emails", e)


@api_router.post("/sync-volunteers")
async def sync_volunteers(
    request: Request,
    _: None = Depends(require_admin_or_scheduler_auth),
    db: Session = Depends(get_db)
):
    """Sync volunteers from Google Sheets and process new signups with graceful degradation"""
    auth_info = get_auth_context(request)
    logger.info(
        f"User/service {auth_info} syncing volunteers from Google Sheets"
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
async def send_weekly_reminder_emails(
    request: Request,
    _: None = Depends(require_admin_or_scheduler_auth)
):
    """Send weekly reminder emails to all active volunteers - accessible by admin dashboard and scheduler"""
    auth_info = get_auth_context(request)
    logger.info(f"User/service {auth_info} sending weekly reminders")
    
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
        handle_scheduler_error("send weekly reminder emails", e)


@api_router.post("/rotate-schedule")
async def rotate_schedule_sheets(
    request: Request,
    _: None = Depends(require_admin_or_scheduler_auth)
):
    """Rotate schedule sheets to show next week - accessible by admin dashboard and scheduler"""
    auth_info = get_auth_context(request)
    logger.info(
        f"User/service {auth_info} rotating schedule sheets"
    )

    try:
        with get_db_session() as db:
            sheets_service.rotate_schedule_sheets(db)
            return {"status": "success", "message": "Schedule sheets rotated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        handle_scheduler_error("rotate schedule sheets", e)


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


@api_router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Health check endpoint to verify service status and Google Sheets connectivity"""
    try:
        # Quick test of Google Sheets API connectivity
        test_result = sheets_service.get_signup_form_submissions(db)
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
