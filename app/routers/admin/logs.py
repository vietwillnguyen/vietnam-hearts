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
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

router = APIRouter()

MAX_PAGE_SIZE = 200


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
