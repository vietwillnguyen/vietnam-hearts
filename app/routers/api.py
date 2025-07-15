"""
API endpoints for schedule management

These endpoints provide schedule-related functionality:
- Schedule rotation
- Email sending
- Volunteer synchronization

All endpoints require Google OAuth authentication.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
import traceback
from app.database import get_db, get_db_session
from app.services.email_service import email_service
from app.utils.logging_config import get_api_logger
from app.services.google_sheets import sheets_service
from contextlib import contextmanager
from google.oauth2 import id_token
from google.auth.transport import requests
from datetime import datetime
from app.models import (
    Volunteer as VolunteerModel,
)
from app.config import (
    GOOGLE_OAUTH_CLIENT_ID,
    SERVICE_ACCOUNT_EMAIL,
)
from app.utils.config_helper import ConfigHelper
from app.utils.retry_utils import safe_api_call, log_ssl_error
from typing import List, Dict, Any
import ssl
from typing import Callable

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


class SchedulerAuthError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def require_scheduler_auth(request: Request):
    """
    Authenticate Google Cloud Scheduler using OIDC tokens.
    This validates that the request comes from your authorized service account.
    """

    # Get authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("Scheduler auth failed: Missing or invalid Authorization header")
        raise SchedulerAuthError("Missing or invalid Authorization header")

    token = auth_header.split(" ", 1)[1]

    try:
        # Verify the OIDC token
        # audience should be your API's URL (where scheduler calls)
        idinfo = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            audience=GOOGLE_OAUTH_CLIENT_ID,  # or your API's URL
        )

        # Verify it's from your expected service account
        token_email = idinfo.get("email")
        logger.info(f"Token email: {token_email}")
        if not token_email:
            raise SchedulerAuthError("Token missing email claim")

        # # Check if it's your service account (optional but recommended)
        if SERVICE_ACCOUNT_EMAIL and token_email != SERVICE_ACCOUNT_EMAIL:
            logger.warning(f"Scheduler auth failed: Unexpected service account {token_email}")
            raise SchedulerAuthError("Invalid service account")

        # Log successful auth
        logger.info(f"Scheduler authenticated successfully: {token_email}")

        # Store service info in request state for logging
        request.state.service_email = token_email
        request.state.auth_type = "scheduler"

    except ValueError as e:
        logger.error(f"Scheduler auth failed: Invalid token - {str(e)}")
        raise SchedulerAuthError("Invalid OIDC token")
    except Exception as e:
        logger.error(f"Scheduler auth failed: {str(e)}")
        raise SchedulerAuthError("Authentication failed")


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
    """Send weekly reminder emails to all active volunteers"""
    try:
        with get_db_session() as db:
            # Check if weekly reminders are globally enabled
            if not ConfigHelper.get_weekly_reminders_enabled(db):
                logger.warning("Weekly reminders are globally disabled, skipping bulk send")
                return {
                    "status": "skipped",
                    "message": "Weekly reminders are currently disabled globally. Enable them in the admin settings to send weekly reminders.",
                }

            # Get volunteers to send emails to
            if ConfigHelper.get_dry_run(db):
                logger.info("DRY_RUN is enabled, sending emails to dry run email recipient")
                dry_run_email = ConfigHelper.get_dry_run_email_recipient(db)
                db_volunteers = (
                    db.query(VolunteerModel)
                    .filter(VolunteerModel.email == dry_run_email)
                    .all()
                )
            else:
                # Get all active volunteers subscribed to weekly reminders
                db_volunteers = (
                    db.query(VolunteerModel)
                    .filter(VolunteerModel.is_active == True)
                    .filter(VolunteerModel.weekly_reminders_subscribed == True)
                    .all()
                )

            logger.info(f"Sending weekly reminder emails to {len(db_volunteers)} volunteers")

            # Track results
            emails_sent = 0
            emails_failed = 0
            failed_emails = []

            # Send emails to each volunteer
            for volunteer in db_volunteers:
                try:
                    # Build email content using the centralized service method
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
                        emails_sent += 1
                        logger.info(f"✅ Weekly reminder sent to {volunteer.email}")
                    else:
                        emails_failed += 1
                        failed_emails.append(volunteer.email)
                        logger.error(f"❌ Failed to send weekly reminder to {volunteer.email}")

                except Exception as e:
                    emails_failed += 1
                    failed_emails.append(volunteer.email)
                    logger.error(f"❌ Error sending weekly reminder to {volunteer.email}: {str(e)}")

        # Prepare response
        total_volunteers = len(db_volunteers)
        if emails_failed == 0:
            return {
                "status": "success",
                "message": f"Weekly reminder emails sent successfully to {emails_sent} volunteers",
                "emails_sent": emails_sent,
                "volunteers_processed": total_volunteers
            }
        else:
            return {
                "status": "partial_success",
                "message": f"Weekly reminder emails sent to {emails_sent} volunteers, {emails_failed} failed",
                "emails_sent": emails_sent,
                "emails_failed": emails_failed,
                "volunteers_processed": total_volunteers,
                "failed_emails": failed_emails
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
