"""
Facebook Messenger webhook and test endpoints
"""

from fastapi import APIRouter, Request
from app.services.messenger.message_sender import MessageSender
from app.services.messenger.mock_message_sender import MockMessageSender
from app.services.bot_service import BotService
from app.utils.logging_config import get_api_logger
from app.config import (
    ENVIRONMENT,
    FACEBOOK_VERIFY_TOKEN,
    FACEBOOK_ACCESS_TOKEN,
)

logger = get_api_logger()

messenger_router = APIRouter(prefix="", tags=["messenger"])


def get_message_sender():
    """
    Return the appropriate message sender based on environment.
    Uses mock sender for development/testing to avoid Facebook API calls.
    """
    if ENVIRONMENT in ("development", "test"):
        logger.info("Using MockMessageSender for development/testing")
        return MockMessageSender()
    logger.info("Using MessageSender for production")
    return MessageSender()


# ---------------------------------------------------------------------------
# Webhook verification + event handling
# ---------------------------------------------------------------------------

@messenger_router.get("/webhook/messenger")
async def verify_webhook(
    mode: str = None,
    verify_token: str = None,
    challenge: str = None,
):
    """Facebook webhook verification endpoint."""
    if not FACEBOOK_VERIFY_TOKEN:
        logger.error("FACEBOOK_VERIFY_TOKEN not configured")
        return {"error": "Webhook not configured"}

    if mode == "subscribe" and verify_token == FACEBOOK_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return int(challenge) if challenge else "OK"

    logger.warning(
        f"Webhook verification failed: mode={mode}, "
        f"token_match={verify_token == FACEBOOK_VERIFY_TOKEN}"
    )
    return {"error": "Verification failed"}


@messenger_router.post("/webhook/messenger")
async def handle_webhook(request: Request):
    """Receive and dispatch all incoming Facebook Messenger events."""
    try:
        body = await request.json()
        logger.info(f"Received webhook: {body}")

        if "object" in body and body["object"] == "page":
            for entry in body.get("entry", []):
                for messaging_event in entry.get("messaging", []):
                    await _process_messaging_event(messaging_event)
            return {"status": "success"}

        logger.warning(f"Invalid webhook object: {body.get('object', 'unknown')}")
        return {"status": "error", "message": "Invalid webhook object"}

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def _process_messaging_event(event: dict):
    """Dispatch a single Facebook messaging event to the appropriate handler."""
    try:
        sender_id = event.get("sender", {}).get("id")
        if not sender_id:
            logger.warning("No sender ID in messaging event")
            return

        if "message" in event:
            await _handle_message(sender_id, event["message"])
        elif "postback" in event:
            await _handle_postback(sender_id, event["postback"])
        else:
            logger.info(f"Unhandled event type: {list(event.keys())}")

    except Exception as e:
        logger.error(f"Error processing messaging event: {str(e)}", exc_info=True)


async def _handle_message(sender_id: str, message: dict):
    """Handle text messages: use bot service with echo fallback."""
    try:
        if "text" not in message:
            logger.info(f"Received non-text message from {sender_id}: {message}")
            return

        text = message["text"]
        logger.info(f"Received message from {sender_id}: {text}")

        try:
            bot_service = BotService()
            chat_result = await bot_service.chat(text)
            response_text = chat_result["response"]
            logger.info(f"Bot service response: {response_text[:100]}...")
        except Exception as e:
            logger.error(f"Bot service failed, falling back to echo: {e}")
            response_text = f"Echo: {text}"

        sender = get_message_sender()
        success = sender.send_text_message(sender_id, response_text)
        if success:
            logger.info(f"Response sent to {sender_id}")
        else:
            logger.error(f"Failed to send response to {sender_id}")

    except Exception as e:
        logger.error(f"Error handling message: {str(e)}", exc_info=True)


async def _handle_postback(sender_id: str, postback: dict):
    """Handle postback events (button clicks, etc.)."""
    try:
        payload = postback.get("payload", "")
        logger.info(f"Received postback from {sender_id}: {payload}")

        response_text = f"Postback received: {payload}"
        sender = get_message_sender()
        success = sender.send_text_message(sender_id, response_text)
        if success:
            logger.info(f"Postback acknowledgment sent to {sender_id}")
        else:
            logger.error(f"Failed to send postback acknowledgment to {sender_id}")

    except Exception as e:
        logger.error(f"Error handling postback: {str(e)}", exc_info=True)


# ---------------------------------------------------------------------------
# Test / debug endpoints
# ---------------------------------------------------------------------------

@messenger_router.get("/test-messenger")
def test_messenger_configuration():
    """Test Facebook Messenger configuration and connectivity."""
    try:
        config_status = {
            "FACEBOOK_VERIFY_TOKEN": bool(FACEBOOK_VERIFY_TOKEN),
            "FACEBOOK_ACCESS_TOKEN": bool(FACEBOOK_ACCESS_TOKEN),
        }

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
            "note": "Webhook will work for testing even with expired token",
        }
    except Exception as e:
        logger.error(f"Facebook Messenger test failed: {str(e)}", exc_info=True)
        return {"status": "error", "message": f"Facebook Messenger test failed: {str(e)}"}


@messenger_router.get("/test-messenger-mock")
def test_messenger_mock():
    """Test Messenger webhook logic using mock sender (no Facebook API calls)."""
    try:
        sender = get_message_sender()
        success = sender.send_text_message("test_user_123", "Hello from mock sender!")
        sent_messages = sender.get_sent_messages() if hasattr(sender, "get_sent_messages") else []

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
            "direct_test": {"success": test_success, "messages": direct_messages},
            "note": (
                f"Using {type(sender).__name__} — "
                f"{'Mock sender for development' if 'Mock' in type(sender).__name__ else 'Real sender for production'}"
            ),
        }
    except Exception as e:
        logger.error(f"Messenger test failed: {str(e)}", exc_info=True)
        return {"status": "error", "message": f"Messenger test failed: {str(e)}"}
