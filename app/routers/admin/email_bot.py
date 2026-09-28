"""Admin endpoints for the volunteer inbox bot.

``POST /admin/email-bot/poll`` is what Cloud Scheduler calls twice a day, and it
is deliberately a plain ``def``: FastAPI runs a sync route in the threadpool, so
the synchronous Gmail, classifier and database clients cannot block the event
loop, and ``EmailBotPipeline`` gets to stay synchronous around them.

``sync-knowledge-base`` is the exception. It only awaits ``sync_documents()``, so
it is an ``async def`` that awaits directly rather than paying for a thread.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import EMAIL_BOT_ENABLED
from app.database import get_db
from app.dependencies.services import get_bot_service
from app.models import Conversation, EmailBotRun
from app.services.conversation_service import ConversationService
from app.services.email_bot.delivery import DeliveryMode, parse_mode
from app.services.email_bot.factory import EmailBotNotConfigured, build_pipeline
from app.services.email_bot.pipeline import ALREADY_RUNNING
from app.services.email_bot.settings import EmailBotSettings
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

router = APIRouter(prefix="/email-bot")


@router.post("/poll")
def poll_inbox(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Poll the volunteer inbox once. Idempotent, so a scheduler retry is safe.

    Two stops before anything is built, in this order, because each is cheaper
    and harder to bypass than the next. ``EMAIL_BOT_ENABLED`` is the operator's
    env-var stop and survives a database an admin can edit. ``EMAIL_BOT_MODE``
    is the dashboard kill switch, read fresh here rather than cached, so
    flipping it stops the very next poll with no deploy.
    """
    if not EMAIL_BOT_ENABLED:
        # Deliberately before any construction: no Gmail client, no classifier,
        # no credentials read. A deployment that never turns the bot on cannot
        # be broken by a key it does not have.
        return {"status": "disabled", "reason": "EMAIL_BOT_ENABLED is not true"}

    settings = EmailBotSettings.load(db)
    mode = parse_mode(settings.mode)
    if mode is DeliveryMode.OFF:
        return {"status": "off", "reason": "EMAIL_BOT_MODE is off"}

    try:
        pipeline = build_pipeline(db, settings)
    except EmailBotNotConfigured as exc:
        logger.error("Inbox bot is not configured: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = pipeline.run()

    if summary.aborted_reason == ALREADY_RUNNING:
        # 200, not 409: this is Cloud Scheduler retrying, and a non-2xx would
        # make it retry again on a growing backoff for a condition that is
        # already correct.
        return {"status": ALREADY_RUNNING, "run": summary.as_dict()}

    status = "aborted" if summary.aborted_reason else "success"
    return {"status": status, "run": summary.as_dict()}


@router.post("/sync-knowledge-base")
async def sync_knowledge_base(
    db: Session = Depends(get_db), bot_service=Depends(get_bot_service)
) -> dict[str, Any]:
    """Re-ingest the curated knowledge-base doc.

    A thin endpoint over the existing document sync, exposed here because the
    parent spec's ``app/routers/bot.py`` stays unwired until its own phase. The
    scheduler calls this daily so an edit a coordinator makes is answerable the
    next day with no deploy and nobody clicking anything.
    """
    settings = EmailBotSettings.load(db)
    if not settings.knowledge_base_doc_id:
        raise HTTPException(
            status_code=400,
            detail="KNOWLEDGE_BASE_DOC_ID is not set; nothing to sync",
        )

    result = await bot_service.sync_documents(
        settings.knowledge_base_doc_id, {"source": "knowledge_base"}
    )

    if result.get("status") != "success":
        # Reported, not swallowed. A sync that silently fails leaves the bot
        # answering from a knowledge base nobody realises is stale.
        raise HTTPException(status_code=502, detail=result)

    return {"status": "success", "result": result}


@router.get("/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Recent polls, newest first, for the dashboard card."""
    rows = (
        db.query(EmailBotRun)
        .order_by(EmailBotRun.started_at.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    return {"runs": [_run_as_dict(row) for row in rows]}


@router.get("/escalations")
def list_escalations(limit: int = 50, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Threads the bot handed over and nobody has resumed.

    Carries the thread key so the dashboard can link straight into Gmail, and
    the category and tier. Not the sender: the conversation row holds only a
    hash of the address, by design.
    """
    conversations = ConversationService(db).escalations(limit=max(1, min(limit, 200)))
    return {
        "escalations": [
            {
                "id": row.id,
                "channel": row.channel,
                "thread_key": row.thread_key,
                "status": row.status,
                "pause_reason": row.pause_reason,
                "category": row.last_category,
                "tier": row.last_tier,
                "bot_reply_count": row.bot_reply_count,
                "updated_at": _utc_iso(row.updated_at),
            }
            for row in conversations
        ]
    }


@router.post("/conversations/{conversation_id}/resume")
def resume_conversation(
    conversation_id: int, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Hand a paused thread back to the bot.

    The only way out of ``paused_manual``, and deliberately a human action: if
    the pipeline could decide a thread the captain had answered was safe again,
    the "never talk over a human" control would not be one.
    """
    conversation = (
        db.query(Conversation).filter(Conversation.id == conversation_id).first()
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    ConversationService(db).resume(conversation)
    return {"status": "success", "conversation_id": conversation.id, "state": "bot"}


def _utc_iso(value: datetime | None) -> str | None:
    """ISO 8601 with an explicit offset, so a browser reads it as UTC.

    The columns are written in UTC but come back naive, and a naive ISO string
    is parsed as local time: a captain in Vietnam would see a run seven hours off.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _run_as_dict(row: EmailBotRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "started_at": _utc_iso(row.started_at),
        "finished_at": _utc_iso(row.finished_at),
        "mode": row.mode,
        "listed": row.listed,
        "processed": row.processed,
        "drafted": row.drafted,
        "sent": row.sent,
        "forwarded": row.forwarded,
        "skipped": row.skipped,
        "errors": row.errors,
        "aborted_reason": row.aborted_reason,
    }
