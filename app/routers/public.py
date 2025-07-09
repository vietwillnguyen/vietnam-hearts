from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    Volunteer as VolunteerModel,
    EmailCommunication as EmailCommunicationModel,
)
from app.services.google_sheets import sheets_service
from app.utils.auth import rate_limit
from app.utils.logging_config import get_api_logger
from datetime import datetime
import os
from app.config import ENVIRONMENT
from app.utils.config_helper import ConfigHelper

logger = get_api_logger()

# Public router for unsubscribe and health
public_router = APIRouter(prefix="/public", tags=["public"])

# Initialize templates
templates = Jinja2Templates(directory="templates")


# Unsubscribe endpoints (public, but rate-limited)
@public_router.get(
    "/unsubscribe", response_class=HTMLResponse, dependencies=[Depends(rate_limit)]
)
def unsubscribe_volunteer_page(
    request: Request, token: str, db: Session = Depends(get_db)
):
    """
    Show unsubscribe page for volunteer

    Args:
        token: Secure unsubscribe token for the volunteer
        db: Database session
    """
    try:
        # Find volunteer by unsubscribe token
        volunteer = (
            db.query(VolunteerModel)
            .filter(VolunteerModel.email_unsubscribe_token == token)
            .first()
        )

        if not volunteer:
            logger.warning(f"Invalid unsubscribe token attempted: {token}")
            return templates.TemplateResponse(
                request,
                "unsubscribe/error.html",
                {
                    "error_message": "Invalid or expired unsubscribe link. Please contact us if you need assistance.",
                },
                status_code=400,
            )

        # Determine current subscription status
        if not volunteer.all_emails_subscribed:
            subscribed_status = "Unsubscribed from all emails (Account deactivated)"
            unsubscribe_type = "all_emails"
        elif not volunteer.weekly_reminders_subscribed:
            subscribed_status = "Subscribed to announcements only (No weekly reminders)"
            unsubscribe_type = "weekly_reminders"
        else:
            subscribed_status = "Subscribed to all emails including weekly reminders"
            unsubscribe_type = "resubscribe"

        return templates.TemplateResponse(
            request,
            "unsubscribe/manage_preferences.html",
            {
                "volunteer_name": volunteer.name,
                "volunteer_email": volunteer.email,
                "token": token,
                "subscribed_status": subscribed_status,
                "unsubscribe_type": unsubscribe_type,
            },
        )

    except Exception as e:
        logger.error(f"Error showing unsubscribe page: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            request,
            "unsubscribe/error.html",
            {
                "error_message": "An error occurred while loading your preferences. Please try again or contact us for assistance.",
            },
        )


@public_router.post(
    "/unsubscribe", response_class=HTMLResponse, dependencies=[Depends(rate_limit)]
)
def update_email_preferences(
    request: Request,
    token: str,
    unsubscribe_type: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Update volunteer email preferences

    Args:
        token: Secure unsubscribe token for the volunteer
        unsubscribe_type: Type of preference update - "weekly_reminders", "all_emails", or "resubscribe"
        db: Database session
    """
    try:
        # Find volunteer by unsubscribe token first
        volunteer = (
            db.query(VolunteerModel)
            .filter(VolunteerModel.email_unsubscribe_token == token)
            .first()
        )

        if not volunteer:
            logger.warning(f"Invalid unsubscribe token attempted: {token}")
            return templates.TemplateResponse(
                request,
                "unsubscribe/error.html",
                {
                    "error_message": "Invalid or expired unsubscribe link. Please contact us if you need assistance.",
                },
                status_code=400,
            )

        if unsubscribe_type not in ["weekly_reminders", "all_emails", "resubscribe"]:
            # Get volunteer info for error display
            volunteer = (
                db.query(VolunteerModel)
                .filter(VolunteerModel.email_unsubscribe_token == token)
                .first()
            )
            
            if volunteer:
                # Determine current subscription status
                if not volunteer.all_emails_subscribed:
                    subscribed_status = "Unsubscribed from all emails (Account deactivated)"
                    current_unsubscribe_type = "all_emails"
                elif not volunteer.weekly_reminders_subscribed:
                    subscribed_status = "Subscribed to announcements only (No weekly reminders)"
                    current_unsubscribe_type = "weekly_reminders"
                else:
                    subscribed_status = "Subscribed to all emails including weekly reminders"
                    current_unsubscribe_type = "resubscribe"
            else:
                subscribed_status = "Unknown"
                current_unsubscribe_type = "resubscribe"

            return templates.TemplateResponse(
                request,
                "unsubscribe/manage_preferences.html",
                {
                    "volunteer_name": volunteer.name if volunteer else "Unknown",
                    "volunteer_email": volunteer.email if volunteer else "Unknown",
                    "error_message": "Invalid preference selection. Please try again.",
                    "token": token,
                    "subscribed_status": subscribed_status,
                    "unsubscribe_type": current_unsubscribe_type,
                },
                status_code=422,
            )

        # Handle preference update based on type
        if unsubscribe_type == "weekly_reminders":
            volunteer.weekly_reminders_subscribed = False
            volunteer.all_emails_subscribed = True  # Keep other emails
            success_message = "You've been unsubscribed from weekly reminders. You'll still receive other important updates."
        elif unsubscribe_type == "all_emails":
            volunteer.all_emails_subscribed = False
            volunteer.weekly_reminders_subscribed = False
            # Auto-deactivate when unsubscribing from all emails
            volunteer.is_active = False
            success_message = "You've been unsubscribed from all emails and your volunteer account has been deactivated."
        else:  # resubscribe
            volunteer.all_emails_subscribed = True
            volunteer.weekly_reminders_subscribed = True
            # Re-activate when resubscribing
            volunteer.is_active = True
            success_message = "You've been resubscribed to all emails and your volunteer account has been reactivated!"

        volunteer.last_email_sent_at = datetime.now()

        # Log the preference change
        email_comm = EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type=f"preference_update_{unsubscribe_type}",
            subject=f"Email Preference Update - {unsubscribe_type.replace('_', ' ').title()}",
            template_name=None,
            status="sent",
            sent_at=datetime.now(),
        )
        db.add(email_comm)
        db.commit()

        logger.info(
            f"Volunteer {volunteer.email} updated preferences to {unsubscribe_type}"
        )

        # If unsubscribing from all emails, optionally update Google Sheets
        if unsubscribe_type == "all_emails":
            # REMOVED: Update Google Sheets for unsubscribe
            # Database is now the source of truth; no write-back to Sheets.
            # Note: Volunteer is now auto-deactivated, so they won't be synced from Sheets
            pass

        # Determine current subscription status after update
        if not volunteer.all_emails_subscribed:
            subscribed_status = "Unsubscribed from all emails (Account deactivated)"
            unsubscribe_type = "all_emails"
        elif not volunteer.weekly_reminders_subscribed:
            subscribed_status = "Subscribed to announcements only (No weekly reminders)"
            unsubscribe_type = "weekly_reminders"
        else:
            subscribed_status = "Subscribed to all emails including weekly reminders"
            unsubscribe_type = "resubscribe"

        return templates.TemplateResponse(
            request,
            "unsubscribe/manage_preferences.html",
            {
                "volunteer_name": volunteer.name,
                "volunteer_email": volunteer.email,
                "token": token,
                "subscribed_status": subscribed_status,
                "unsubscribe_type": unsubscribe_type,
                "success_message": success_message,
            },
        )

    except Exception as e:
        logger.error(f"Error updating email preferences: {str(e)}", exc_info=True)
        db.rollback()
        return templates.TemplateResponse(
            request,
            "unsubscribe/manage_preferences.html",
            {
                "error_message": "An error occurred while updating your preferences. Please try again or contact us for assistance.",
                "token": token,
            },
        )


# Health endpoint (public)
@public_router.get(
    "/health", summary="Health check", description="Returns system health information"
)
def get_health(db: Session = Depends(get_db)):
    """Get system health information"""
    try:
        # Test Google Sheets connection
        sheets_status = "unknown"
        sheets_error = None
        try:
            test_range = sheets_service.get_range_from_sheet(
                db,
                ConfigHelper.get_schedule_sheet_id(db) or "",
                "A1:A1"
            )
            sheets_status = "healthy"
        except Exception as e:
            sheets_status = "unhealthy"
            sheets_error = str(e)

        # Get database stats using the injected session
        total_volunteers = 0
        total_emails = 0
        db_status = "healthy"
        
        try:
            total_volunteers = db.query(VolunteerModel).count()
            total_emails = db.query(EmailCommunicationModel).count()
        except Exception as e:
            db_status = "unhealthy"
            logger.error(f"Database health check failed: {str(e)}")

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "1.1.4",
            "environment": ENVIRONMENT,
            "dry_run": ConfigHelper.get_dry_run(db),
            "services": {
                "database": {
                    "status": db_status,
                    "stats": {"volunteers": total_volunteers, "emails": total_emails},
                    "type": "SQLite"
                    if "sqlite" in os.getenv("DATABASE_URL", "")
                    else "PostgreSQL",
                },
                "google_sheets": {"status": sheets_status, "error": sheets_error},
            },
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e),
        }

@public_router.get("/test-sheets")
def test_sheets_connection(db: Session = Depends(get_db)):
    """Test Google Sheets connection and configuration"""
    try:
        # Test fetching a small range from the schedule sheet
        test_range = sheets_service.get_range_from_sheet(
            db,
            ConfigHelper.get_schedule_sheet_id(db),
            "A1:B2"
        )
        
        return {
            "status": "success",
            "message": "Google Sheets connection successful",
            "test_data": test_range
        }
    except Exception as e:
        logger.error(f"Google Sheets test failed: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Google Sheets test failed: {str(e)}"
        }