"""
Public endpoints for the volunteer management system
"""

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
from app.services.bot_service import BotService
from app.utils.logging_config import get_api_logger
from app.config import ENVIRONMENT
from datetime import datetime
import os
from app.utils.config_helper import ConfigHelper

logger = get_api_logger()

# Public router for web pages, unsubscribe, and health
public_router = APIRouter(prefix="", tags=["public"])

# Initialize templates
templates = Jinja2Templates(directory="templates/web")


@public_router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """Serve the home page."""
    from app.config import APPLICATION_VERSION
    return templates.TemplateResponse("home.html", {
        "request": request,
        "version": APPLICATION_VERSION
    })


# ---------------------------------------------------------------------------
# Unsubscribe / email-preferences endpoints
# ---------------------------------------------------------------------------

@public_router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_volunteer_page(
    request: Request, token: str, db: Session = Depends(get_db)
):
    """Show the email-preferences management page for a volunteer."""
    try:
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


@public_router.post("/unsubscribe", response_class=HTMLResponse)
def update_email_preferences(
    request: Request,
    token: str,
    unsubscribe_type: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update volunteer email preferences."""
    try:
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
            if not volunteer.all_emails_subscribed:
                subscribed_status = "Unsubscribed from all emails (Account deactivated)"
                current_unsubscribe_type = "all_emails"
            elif not volunteer.weekly_reminders_subscribed:
                subscribed_status = "Subscribed to announcements only (No weekly reminders)"
                current_unsubscribe_type = "weekly_reminders"
            else:
                subscribed_status = "Subscribed to all emails including weekly reminders"
                current_unsubscribe_type = "resubscribe"

            return templates.TemplateResponse(
                request,
                "unsubscribe/manage_preferences.html",
                {
                    "volunteer_name": volunteer.name,
                    "volunteer_email": volunteer.email,
                    "error_message": "Invalid preference selection. Please try again.",
                    "token": token,
                    "subscribed_status": subscribed_status,
                    "unsubscribe_type": current_unsubscribe_type,
                },
                status_code=422,
            )

        if unsubscribe_type == "weekly_reminders":
            volunteer.weekly_reminders_subscribed = False
            volunteer.all_emails_subscribed = True
            success_message = "You've been unsubscribed from weekly reminders. You'll still receive other important updates."
        elif unsubscribe_type == "all_emails":
            volunteer.all_emails_subscribed = False
            volunteer.weekly_reminders_subscribed = False
            volunteer.is_active = False
            success_message = "You've been unsubscribed from all emails and your volunteer account has been deactivated."
        else:  # resubscribe
            volunteer.all_emails_subscribed = True
            volunteer.weekly_reminders_subscribed = True
            volunteer.is_active = True
            success_message = "You've been resubscribed to all emails and your volunteer account has been reactivated!"

        volunteer.last_email_sent_at = datetime.now()

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

        logger.info(f"Volunteer {volunteer.email} updated preferences to {unsubscribe_type}")

        # Determine display status after update
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
        # Re-query volunteer so the form re-renders with the correct pre-selected radio.
        try:
            db.expire_all()
            err_volunteer = (
                db.query(VolunteerModel)
                .filter(VolunteerModel.email_unsubscribe_token == token)
                .first()
            )
            if err_volunteer:
                if not err_volunteer.all_emails_subscribed:
                    err_subscribed_status = "Unsubscribed from all emails (Account deactivated)"
                    err_unsubscribe_type = "all_emails"
                elif not err_volunteer.weekly_reminders_subscribed:
                    err_subscribed_status = "Subscribed to announcements only (No weekly reminders)"
                    err_unsubscribe_type = "weekly_reminders"
                else:
                    err_subscribed_status = "Subscribed to all emails including weekly reminders"
                    err_unsubscribe_type = "resubscribe"
                return templates.TemplateResponse(
                    request,
                    "unsubscribe/manage_preferences.html",
                    {
                        "volunteer_name": err_volunteer.name,
                        "volunteer_email": err_volunteer.email,
                        "token": token,
                        "subscribed_status": err_subscribed_status,
                        "unsubscribe_type": err_unsubscribe_type,
                        "error_message": "An error occurred while updating your preferences. Please try again or contact us for assistance.",
                    },
                )
        except Exception:
            pass
        return templates.TemplateResponse(
            request,
            "unsubscribe/error.html",
            {
                "error_message": "An error occurred while updating your preferences. Please try again or contact us for assistance.",
            },
        )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@public_router.get(
    "/health", summary="Health check", description="Returns system health information"
)
def get_health(db: Session = Depends(get_db)):
    """Get system health information."""
    try:
        sheets_status = "unknown"
        sheets_error = None
        try:
            sheets_service.get_range_from_sheet(
                db,
                ConfigHelper.get_schedule_sheet_id(db) or "",
                "A1:A1",
            )
            sheets_status = "healthy"
        except Exception as e:
            sheets_status = "unhealthy"
            sheets_error = str(e)

        total_volunteers = 0
        total_emails = 0
        db_status = "healthy"
        try:
            total_volunteers = db.query(VolunteerModel).count()
            total_emails = db.query(EmailCommunicationModel).count()
        except Exception as e:
            db_status = "unhealthy"
            logger.error(f"Database health check failed: {str(e)}")

        bot_status = "unknown"
        bot_error = None
        try:
            from app.routers.bot import get_bot_service
            bot_service = get_bot_service()
            if hasattr(bot_service, "health_check"):
                bot_health = bot_service.health_check()
                if bot_health.get("status") == "healthy":
                    bot_status = "healthy"
                else:
                    bot_status = "unhealthy"
                    bot_error = bot_health.get("error")
            else:
                bot_status = "healthy" if bot_service is not None else "unhealthy"
                if bot_status == "unhealthy":
                    bot_error = "Bot service not initialized"
        except Exception as e:
            bot_status = "unhealthy"
            bot_error = str(e)
            logger.error(f"Bot service health check failed: {str(e)}")

        from app.config import APPLICATION_VERSION
        overall = (
            "healthy"
            if db_status == "healthy" and sheets_status == "healthy" and bot_status == "healthy"
            else "unhealthy"
        )
        return {
            "status": overall,
            "version": APPLICATION_VERSION,
            "timestamp": datetime.now().isoformat(),
            "environment": ENVIRONMENT,
            "dry_run": ConfigHelper.get_dry_run(db),
            "services": {
                "database": {
                    "status": db_status,
                    "stats": {"volunteers": total_volunteers, "emails": total_emails},
                    "type": "SQLite" if "sqlite" in os.getenv("DATABASE_URL", "") else "PostgreSQL",
                },
                "google_sheets": {"status": sheets_status, "error": sheets_error},
                "bot_service": {"status": bot_status, "error": bot_error},
            },
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Debug / integration test endpoints
# ---------------------------------------------------------------------------

@public_router.get("/test-sheets")
def test_sheets_connection(db: Session = Depends(get_db)):
    """Test Google Sheets connection and configuration."""
    try:
        test_range = sheets_service.get_range_from_sheet(
            db,
            ConfigHelper.get_schedule_sheet_id(db),
            "A1:B2",
        )
        return {
            "status": "success",
            "message": "Google Sheets connection successful",
            "test_data": test_range,
        }
    except Exception as e:
        logger.error(f"Google Sheets test failed: {str(e)}", exc_info=True)
        return {"status": "error", "message": f"Google Sheets test failed: {str(e)}"}
