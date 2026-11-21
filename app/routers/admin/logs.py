"""
Admin system logs endpoints

Serves persisted log records from the system_logs table (written by
DatabaseLogHandler) so the dashboard can show log history that survives
Cloud Run container restarts.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SystemLog
from app.services.log_retention import log_retention_days, prune_system_logs
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

router = APIRouter()

MAX_PAGE_SIZE = 200


@router.post("/prune-system-logs")
def prune_old_system_logs(db: Session = Depends(get_db)):
    """Delete system_logs rows older than LOG_RETENTION_DAYS (default 30).

    Called daily by the prune-system-logs Cloud Scheduler job. Idempotent.
    """
    retention_days = log_retention_days()
    try:
        deleted = prune_system_logs(db, retention_days)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to prune system logs: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to prune system logs: {str(e)}"
        ) from e
    logger.info(f"Pruned {deleted} system_logs row(s) older than {retention_days} days")
    return {"status": "success", "deleted": deleted, "retention_days": retention_days}


@router.get("/logs")
def get_system_logs(
    level: str = Query(
        None, description="Filter by log level (DEBUG/INFO/WARNING/ERROR)"
    ),
    q: str = Query(
        None, description="Case-insensitive search in message and logger name"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1),
    db: Session = Depends(get_db),
):
    """Get persisted system logs, newest first, with filtering and pagination"""
    try:
        page_size = min(page_size, MAX_PAGE_SIZE)

        query = db.query(SystemLog)
        if level:
            query = query.filter(SystemLog.level == level.upper())
        if q:
            pattern = f"%{q}%"
            query = query.filter(
                or_(
                    SystemLog.message.ilike(pattern),
                    SystemLog.logger_name.ilike(pattern),
                )
            )

        total = query.count()
        logs = (
            query.order_by(SystemLog.created_at.desc(), SystemLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "status": "success",
            "details": {
                "logs": [
                    {
                        "id": log.id,
                        "created_at": log.created_at.isoformat()
                        if log.created_at
                        else None,
                        "level": log.level,
                        "logger_name": log.logger_name,
                        "message": log.message,
                    }
                    for log in logs
                ],
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }
    except Exception as e:
        logger.error(f"Failed to fetch system logs: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch system logs: {str(e)}"
        ) from e
