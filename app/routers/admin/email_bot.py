"""Admin endpoints for the volunteer inbox bot.

``POST /admin/email-bot/poll`` is what Cloud Scheduler calls twice a day, and it
is deliberately a plain ``def``: FastAPI runs a sync route in the threadpool, so
the synchronous Gmail, classifier and database clients cannot block the event
loop, and ``EmailBotPipeline`` gets to stay synchronous around them.

``sync-knowledge-base`` is the exception. It only awaits ``sync_documents()``, so
it is an ``async def`` that awaits directly rather than paying for a thread.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.config import EMAIL_BOT_ENABLED
from app.database import get_db
from app.dependencies.auth import get_current_admin_user
from app.dependencies.services import get_bot_service
from app.models import Conversation, EmailBotRun
from app.services.conversation_service import ConversationService, ConversationSummary
from app.services.conversation_store import (
    ErasureResult,
    SqlConversationStore,
    StoredMessage,
    Viewer,
)
from app.services.email_bot.delivery import DeliveryMode, parse_mode
from app.services.email_bot.factory import EmailBotNotConfigured, build_pipeline
from app.services.email_bot.gates import ACCEPTANCE_GATES
from app.services.email_bot.pipeline import ALREADY_RUNNING
from app.services.email_bot.settings import (
    SETTING_KB_CHUNKS,
    SETTING_KB_LAST_SYNC,
    EmailBotSettings,
)
from app.services.settings_service import get_setting, set_setting
from app.utils.field_crypto import DecryptionError, EncryptionKeyError
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

    chunks = int(result.get("chunks") or 0)
    if chunks == 0:
        # sync_documents calls an emptied document a success, because nothing
        # went wrong mechanically. But an empty knowledge base makes the bot
        # refuse every FAQ question, which is a silent outage of the answer
        # path rather than a successful sync.
        raise HTTPException(
            status_code=502,
            detail={
                "message": (
                    "the knowledge base synced to zero chunks; the document is "
                    "empty, which turns FAQ answers off"
                ),
                "result": result,
            },
        )

    # Written only on success, so "last synced two days ago" stays true rather
    # than being overwritten with a time at which nothing was ingested. The
    # dashboard warns when this goes stale, which is the part an operator
    # actually reads.
    set_setting(db, SETTING_KB_LAST_SYNC, datetime.now(UTC).isoformat())
    set_setting(db, SETTING_KB_CHUNKS, str(chunks))

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


@router.get("/metrics")
def bot_metrics(db: Session = Depends(get_db)) -> dict[str, Any]:
    """The two numbers the evaluation gate is argued from.

    Draft acceptance is grouped by the answer path that produced the draft,
    because the gate is stated per kind - ``ACCEPTANCE_GATES`` - so one
    aggregate would not answer it. Each kind carries its gate, or None where
    the design sets none, so the card renders the table rather than a copy.

    Both are computed from the audit rows, so this endpoint is cheap enough
    for the dashboard to poll and carries nothing a row does not already hold.
    """
    service = ConversationService(db)
    acceptance = service.draft_acceptance()

    from app.services.email_bot.caps import CapsState
    from app.utils.schedule_dates import local_now

    settings = EmailBotSettings.load(db)
    caps = CapsState.load(db, local_now(settings.timezone), settings)

    return {
        # What auto mode has left today. Reported even while the mode is off,
        # because an operator about to turn it on wants to know what it would
        # be allowed to do.
        "caps": caps.as_dict(),
        "draft_acceptance": {
            kind: {
                "outcomes": outcomes,
                "resolved": sum(
                    count
                    for outcome, count in outcomes.items()
                    if outcome not in _NOT_RESOLVED
                ),
                "sent_unchanged": outcomes.get("sent_unchanged", 0),
                # None rather than 0 while nothing has resolved: a rate of zero
                # and "not measured yet" would otherwise look the same, and the
                # gate needs two weeks of the latter before it means anything.
                "rate": _acceptance_rate(outcomes),
                "gate": ACCEPTANCE_GATES.get(kind),
            }
            for kind, outcomes in acceptance.items()
        },
        "acceptance_gates": dict(ACCEPTANCE_GATES),
        "shadow_agreement": service.shadow_agreement(),
        "knowledge_base": _knowledge_base_health(db),
    }


# A daily job plus slack. Past this, the sync has almost certainly stopped
# rather than merely run late, and the card says so.
KB_STALE_AFTER = timedelta(hours=36)


def _knowledge_base_health(db: Session) -> dict[str, Any]:
    """When the knowledge base was last read, and whether that is worrying.

    The staleness verdict is computed here rather than left to the page,
    because "is a daily job still running" is the question an operator has, and
    a bare timestamp is something nobody reads.
    """
    last_sync = get_setting(db, SETTING_KB_LAST_SYNC, "") or ""
    chunks = get_setting(db, SETTING_KB_CHUNKS, "") or ""

    stale = None
    if last_sync:
        try:
            when = datetime.fromisoformat(last_sync)
        except ValueError:
            stale = True
        else:
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            stale = datetime.now(UTC) - when > KB_STALE_AFTER

    return {
        "last_sync": last_sync or None,
        "chunks": int(chunks) if chunks.isdigit() else None,
        # None while nothing has ever synced: "never synced" and "synced and
        # now stale" are different problems and the card words them
        # differently.
        "stale": stale,
    }


# Outcomes that say nothing about whether the draft was right: still waiting,
# or deleted on request before anyone could tell.
_NOT_RESOLVED = {"pending", "unmeasured"}


def _acceptance_rate(outcomes: dict[str, int]) -> float | None:
    resolved = sum(
        count for outcome, count in outcomes.items() if outcome not in _NOT_RESOLVED
    )
    if resolved == 0:
        return None
    return outcomes.get("sent_unchanged", 0) / resolved


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


# ---------------------------------------------------------- conversations
#
# The stored record of each conversation. Readable by people only: the cron
# service account is an admin for every scheduled job, and no job reads mail,
# so a leaked scheduler key is not a way into the inbox. Only ids, counts and
# outcomes are listed; content is returned for one conversation at a time, and
# that open is recorded before anything is decrypted. Nothing here logs content
# or puts it in an error.


def require_person_admin(
    admin: dict[str, Any] = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """The signed-in admin, or 403 for the service-role key."""
    if admin.get("is_service_account"):
        raise HTTPException(
            status_code=403,
            detail="Conversations are readable by signed-in admins only",
        )
    return admin


@router.get("/conversations")
def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin: dict[str, Any] = Depends(require_person_admin),
) -> dict[str, Any]:
    """A page of conversations with no subject, body or address in it."""
    summaries, total = ConversationService(db).conversation_page(
        limit=limit, offset=offset
    )
    return {
        "conversations": [_summary_as_dict(summary) for summary in summaries],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/conversations/{conversation_id}")
def read_conversation(
    conversation_id: int,
    response: Response,
    db: Session = Depends(get_db),
    admin: dict[str, Any] = Depends(require_person_admin),
) -> dict[str, Any]:
    """One conversation, decrypted. Records the view first."""
    summary = ConversationService(db).summary(conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    viewer = _viewer(admin)
    store = SqlConversationStore(db)
    store.record_view(conversation_id, viewer)
    logger.info("Conversation %s opened by admin id=%s", conversation_id, viewer.id)

    # Decrypted content must not sit in a browser or proxy cache.
    response.headers["Cache-Control"] = "no-store"
    try:
        messages = store.messages(conversation_id)
    except EncryptionKeyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except DecryptionError as exc:
        # The message names the column and row ids only.
        logger.error("Could not decrypt conversation %s: %s", conversation_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from None

    count, recent = store.views(conversation_id)
    return {
        "conversation": _summary_as_dict(summary),
        "messages": [_message_as_dict(message) for message in messages],
        "views": {
            "count": count,
            "recent": [
                {
                    "viewer_id": view.viewer_id,
                    "viewer_email": view.viewer_email,
                    "viewed_at": _utc_iso(view.viewed_at),
                }
                for view in recent
            ],
        },
        "viewer": {"id": viewer.id, "email": viewer.email},
    }


@router.post("/conversations/erase-by-sender")
def erase_conversations_by_sender(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    admin: dict[str, Any] = Depends(require_person_admin),
) -> dict[str, Any]:
    """Delete the text and address of everything one email address sent.

    The address arrives in the JSON body, never the URL, because request lines
    are logged by Cloud Run and the access log. It is hashed, matched against
    ``sender_key``, and neither logged, echoed back nor stored. The body is
    read as a plain dict and checked by hand so that a validation error cannot
    quote the address back either.
    """
    address = payload.get("email") if isinstance(payload, dict) else None
    if not isinstance(address, str):
        raise HTTPException(status_code=422, detail='Send {"email": <address>}')
    try:
        result = SqlConversationStore(db).erase_sender(address, _viewer(admin))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="That is not an email address"
        ) from None
    logger.info(
        "Deletion %s by address: %s message(s) in %s conversation(s), by admin id=%s",
        result.deletion_id,
        result.messages_erased,
        len(result.conversation_ids),
        admin.get("id"),
    )
    return _erasure_as_dict(result)


@router.post("/conversations/{conversation_id}/erase")
def erase_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    admin: dict[str, Any] = Depends(require_person_admin),
) -> dict[str, Any]:
    """Delete one conversation's text and addresses, keeping what the bot did."""
    result = SqlConversationStore(db).erase_conversation(
        conversation_id, _viewer(admin)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    logger.info(
        "Deletion %s of conversation %s: %s message(s), by admin id=%s",
        result.deletion_id,
        conversation_id,
        result.messages_erased,
        admin.get("id"),
    )
    return _erasure_as_dict(result)


def _viewer(admin: dict[str, Any]) -> Viewer:
    return Viewer(id=str(admin.get("id") or "unknown"), email=admin.get("email"))


def _erasure_as_dict(result: ErasureResult) -> dict[str, Any]:
    return {
        "status": "success",
        "deletion_id": result.deletion_id,
        "conversation_ids": result.conversation_ids,
        "messages_erased": result.messages_erased,
    }


def _summary_as_dict(summary: ConversationSummary) -> dict[str, Any]:
    return {
        "id": summary.id,
        "channel": summary.channel,
        "thread_key": summary.thread_key,
        "status": summary.status,
        "category": summary.category,
        "tier": summary.tier,
        "received_at": _utc_iso(summary.received_at),
        "last_activity_at": _utc_iso(summary.last_activity_at),
        "inbound_count": summary.inbound_count,
        "outbound_count": summary.outbound_count,
        "outcome": summary.outcome,
        "has_content": summary.has_content,
        "erased": summary.erased,
    }


def _message_as_dict(message: StoredMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "direction": message.direction,
        "action": message.action,
        "category": message.category,
        "tier": message.tier,
        "language": message.language,
        "draft_outcome": message.draft_outcome,
        "created_at": _utc_iso(message.created_at),
        "subject": message.subject,
        "body": message.body,
        "sender_address": message.sender_address,
        "erased": message.erased,
    }


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
        "reconciled": row.reconciled,
        "capped": row.capped,
        "errors": row.errors,
        "aborted_reason": row.aborted_reason,
    }
