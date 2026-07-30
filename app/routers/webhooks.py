"""Meta webhook endpoint for Messenger, and from phase 3 also Instagram.

One app, one URL, one signature scheme. Every response is 200 except a failed
signature or handshake, because Meta disables webhooks that repeatedly fail.
"""

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import (
    ENVIRONMENT,
    FACEBOOK_APP_SECRET,
    FACEBOOK_VERIFY_TOKEN,
)
from app.dependencies.services import get_bot_service
from app.services.channels.meta_signature import resolve_challenge, verify_signature
from app.services.messenger.message_sender import MessageSender
from app.services.messenger.mock_message_sender import MockMessageSender
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

webhooks_router = APIRouter(prefix="", tags=["webhooks"])


def get_message_sender():
    """Real sender in production, mock everywhere else."""
    if ENVIRONMENT in ("development", "test"):
        return MockMessageSender()
    return MessageSender()


@webhooks_router.get("/webhook/meta")
async def verify_webhook(
    mode: str | None = Query(None, alias="hub.mode"),
    verify_token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Meta's subscription handshake.

    The parameter names carry literal dots, so each needs an explicit alias.
    Binding them as plain identifiers silently never matches.
    """
    echo = resolve_challenge(FACEBOOK_VERIFY_TOKEN, mode, verify_token, challenge)
    if echo is None:
        logger.warning("Webhook verification refused")
        return PlainTextResponse("Verification failed", status_code=403)

    logger.info("Webhook verified successfully")
    return PlainTextResponse(echo, status_code=200)


@webhooks_router.post("/webhook/meta")
async def handle_webhook(request: Request) -> Response:
    """Receive and dispatch Meta messaging events."""
    raw_body = await request.body()
    if not verify_signature(
        FACEBOOK_APP_SECRET, raw_body, request.headers.get("X-Hub-Signature-256")
    ):
        logger.warning("Rejected webhook with an invalid signature")
        return PlainTextResponse("Invalid signature", status_code=403)

    try:
        body = await request.json()
    except ValueError:
        logger.warning("Rejected webhook with an unparseable body")
        return PlainTextResponse("EVENT_RECEIVED", status_code=200)

    if body.get("object") != "page":
        logger.info(f"Ignoring webhook object: {body.get('object')}")
        return PlainTextResponse("EVENT_RECEIVED", status_code=200)

    bot_service = get_bot_service()
    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            await _process_event(event, bot_service)

    return PlainTextResponse("EVENT_RECEIVED", status_code=200)


async def _process_event(event: dict, bot_service) -> None:
    """Route a single messaging event. Never raises."""
    try:
        sender_id = event.get("sender", {}).get("id")
        if not sender_id:
            return

        if "message" in event:
            await _handle_message(sender_id, event["message"], bot_service)
        elif "postback" in event:
            _handle_postback(sender_id, event["postback"])
    except Exception as exc:
        logger.error(f"Error processing messaging event: {exc}", exc_info=True)


async def _handle_message(sender_id: str, message: dict, bot_service) -> None:
    """Answer an inbound text message.

    Phase 1 replaces this with the triage pipeline, which turns the silent
    failure path below into a holding message plus an escalation.
    """
    if message.get("is_echo"):
        return
    text = message.get("text")
    if not text:
        logger.info(f"Ignoring non-text message from {sender_id}")
        return

    try:
        result = await bot_service.chat(text)
    except Exception as exc:
        logger.error(f"Bot service failed for {sender_id}: {exc}", exc_info=True)
        return

    if get_message_sender().send_text_message(sender_id, result["response"]):
        logger.info(f"Response sent to {sender_id}")
    else:
        logger.error(f"Failed to send response to {sender_id}")


def _handle_postback(sender_id: str, postback: dict) -> None:
    """Acknowledge a button or Get Started postback."""
    logger.info(f"Postback from {sender_id}: {postback.get('payload', '')}")
