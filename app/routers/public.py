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
from app.services.messenger.webhook_handler import WebhookHandler
from app.services.messenger.message_sender import MessageSender
from app.services.messenger.mock_message_sender import MockMessageSender
# from app.utils.auth import rate_limit  # Removed auth
from app.utils.logging_config import get_api_logger
from app.config import (
    ENVIRONMENT,
    FACEBOOK_VERIFY_TOKEN,
    FACEBOOK_ACCESS_TOKEN,
)
from datetime import datetime
import os
from app.utils.config_helper import ConfigHelper

logger = get_api_logger()

def get_message_sender():
    """
    Get the appropriate message sender based on environment.
    Uses mock sender for development/testing to avoid Facebook API issues.
    """
    if ENVIRONMENT == "development" or ENVIRONMENT == "test":
        logger.info("Using MockMessageSender for development/testing")
        return MockMessageSender()
    else:
        logger.info("Using MessageSender for production")
        return MessageSender()

# Public router for unsubscribe and health
public_router = APIRouter(prefix="", tags=["public"])

# Initialize templates
templates = Jinja2Templates(directory="templates")


@public_router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """
    Serve the home page
    
    Returns the main landing page with login functionality.
    """
    from app.config import APPLICATION_VERSION
    return templates.TemplateResponse("home.html", {
        "request": request,
        "version": APPLICATION_VERSION
    })


# Unsubscribe endpoints (public)
@public_router.get("/unsubscribe", response_class=HTMLResponse)
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


@public_router.post("/unsubscribe", response_class=HTMLResponse)
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


# Facebook Messenger Webhook endpoints
@public_router.get("/webhook/messenger")
async def verify_webhook(
    mode: str = None,
    verify_token: str = None,
    challenge: str = None
):
    """
    Facebook webhook verification endpoint
    
    This endpoint is called by Facebook to verify the webhook subscription.
    Facebook sends a GET request with mode=subscribe, verify_token, and challenge.
    """
    if not FACEBOOK_VERIFY_TOKEN:
        logger.error("FACEBOOK_VERIFY_TOKEN not configured")
        return {"error": "Webhook not configured"}
    
    if mode == "subscribe" and verify_token == FACEBOOK_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return int(challenge) if challenge else "OK"
    else:
        logger.warning(f"Webhook verification failed: mode={mode}, token_match={verify_token == FACEBOOK_VERIFY_TOKEN}")
        return {"error": "Verification failed"}


@public_router.post("/webhook/messenger")
async def handle_webhook(request: Request):
    """
    Facebook webhook message handling endpoint
    
    This endpoint receives all incoming messages and events from Facebook Messenger.
    For Phase 1, it implements a simple echo functionality.
    """
    try:
        # Parse the webhook payload
        body = await request.json()
        logger.info(f"Received webhook: {body}")
        
        # Verify this is a page event
        if "object" in body and body["object"] == "page":
            # Process each entry
            for entry in body.get("entry", []):
                for messaging_event in entry.get("messaging", []):
                    await _process_messaging_event(messaging_event)
            
            return {"status": "success"}
        else:
            logger.warning(f"Invalid webhook object: {body.get('object', 'unknown')}")
            return {"status": "error", "message": "Invalid webhook object"}
            
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def _process_messaging_event(event: dict):
    """
    Process individual messaging events from Facebook
    
    Args:
        event: The messaging event from Facebook
    """
    try:
        sender_id = event.get("sender", {}).get("id")
        if not sender_id:
            logger.warning("No sender ID in messaging event")
            return
        
        # Handle different types of events
        if "message" in event:
            await _handle_message(sender_id, event["message"])
        elif "postback" in event:
            await _handle_postback(sender_id, event["postback"])
        else:
            logger.info(f"Unhandled event type: {list(event.keys())}")
            
    except Exception as e:
        logger.error(f"Error processing messaging event: {str(e)}", exc_info=True)


async def _handle_message(sender_id: str, message: dict):
    """
    Handle text messages from users
    
    Args:
        sender_id: The sender's Facebook ID
        message: The message object from Facebook
    """
    try:
        if "text" in message:
            text = message["text"]
            logger.info(f"Received message from {sender_id}: {text}")
            
            # Phase 1: Simple echo functionality
            response_text = f"Echo: {text}"
            
            # Get appropriate message sender (mock for dev, real for prod)
            sender = get_message_sender()
            logger.info(f"Message sender type: {type(sender).__name__}")
            
            success = sender.send_text_message(sender_id, response_text)
            logger.info(f"Message send result: {success}")
            
            if success:
                logger.info(f"Echo response sent to {sender_id}")
            else:
                logger.error(f"Failed to send echo response to {sender_id}")
        else:
            logger.info(f"Received non-text message from {sender_id}: {message}")
            
    except Exception as e:
        logger.error(f"Error handling message: {str(e)}", exc_info=True)


async def _handle_postback(sender_id: str, postback: dict):
    """
    Handle postback events (button clicks, etc.)
    
    Args:
        sender_id: The sender's Facebook ID
        postback: The postback object from Facebook
    """
    try:
        payload = postback.get("payload", "")
        logger.info(f"Received postback from {sender_id}: {payload}")
        
        # Phase 1: Simple acknowledgment
        response_text = f"Postback received: {payload}"
        
        # Get appropriate message sender (mock for dev, real for prod)
        sender = get_message_sender()
        logger.info(f"Postback sender type: {type(sender).__name__}")
        
        success = sender.send_text_message(sender_id, response_text)
        logger.info(f"Postback send result: {success}")
        
        if success:
            logger.info(f"Postback acknowledgment sent to {sender_id}")
        else:
            logger.error(f"Failed to send postback acknowledgment to {sender_id}")
            
    except Exception as e:
        logger.error(f"Error handling postback: {str(e)}", exc_info=True)


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

        from app.config import APPLICATION_VERSION
        return {
            "status": "healthy",
            "version": APPLICATION_VERSION,
            "timestamp": datetime.now().isoformat(),
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
                "facebook_messenger": {
                    "status": "healthy" if FACEBOOK_VERIFY_TOKEN and FACEBOOK_ACCESS_TOKEN else "unhealthy",
                    "webhook_url": "/webhook/messenger"
                },
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


@public_router.get("/test-messenger")
def test_messenger_configuration():
    """Test Facebook Messenger configuration and connectivity"""
    try:
        # Check if required environment variables are set
        config_status = {
            "FACEBOOK_VERIFY_TOKEN": bool(FACEBOOK_VERIFY_TOKEN),
            "FACEBOOK_ACCESS_TOKEN": bool(FACEBOOK_ACCESS_TOKEN),
        }
        
        # Test message sender initialization (but don't fail if token is invalid)
        sender = MessageSender()
        page_info = None
        page_info_error = None
        
        try:
            page_info = sender.get_page_info()
        except Exception as e:
            page_info_error = str(e)
            logger.warning(f"Could not get page info (token may be expired): {e}")
        
        return {
            "status": "success",
            "message": "Facebook Messenger configuration test",
            "config": config_status,
            "page_info": page_info,
            "page_info_error": page_info_error,
            "webhook_url": "/webhook/messenger",
            "note": "Webhook will work for testing even with expired token"
        }
    except Exception as e:
        logger.error(f"Facebook Messenger test failed: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Facebook Messenger test failed: {str(e)}"
        }


@public_router.get("/test-messenger-mock")
def test_messenger_mock():
    """Test Facebook Messenger webhook logic using mock sender (no Facebook API calls)"""
    try:
        # Use the helper function to get appropriate sender
        sender = get_message_sender()
        
        # Test sending a message
        success = sender.send_text_message("test_user_123", "Hello from mock sender!")
        
        # Get sent messages (if it's a mock sender)
        sent_messages = []
        if hasattr(sender, 'get_sent_messages'):
            sent_messages = sender.get_sent_messages()
        
        # Test the mock sender directly to verify it's working
        test_sender = MockMessageSender()
        test_success = test_sender.send_text_message("direct_test_user", "Direct test message")
        direct_messages = test_sender.get_sent_messages()
        
        return {
            "status": "success",
            "message": "Messenger test completed",
            "message_sent": success,
            "sent_messages": sent_messages,
            "sender_type": type(sender).__name__,
            "environment": ENVIRONMENT,
            "direct_test": {
                "success": test_success,
                "messages": direct_messages
            },
            "note": f"Using {type(sender).__name__} - {'Mock sender for development' if 'Mock' in type(sender).__name__ else 'Real sender for production'}"
        }
    except Exception as e:
        logger.error(f"Messenger test failed: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Messenger test failed: {str(e)}"
        }