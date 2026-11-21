"""
Retention for the system_logs table.

One definition of "too old", shared by the daily POST /admin/prune-system-logs
job and the DatabaseLogHandler's opportunistic prune on its first flush.
"""

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import SystemLog

DEFAULT_RETENTION_DAYS = 30


def log_retention_days() -> int:
    """LOG_RETENTION_DAYS, read at call time so a changed env takes effect."""
    return int(os.getenv("LOG_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))


def retention_cutoff(retention_days: int) -> datetime:
    # Naive UTC, matching how DatabaseLogHandler stores created_at.
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)


def prune_system_logs(db: Session, retention_days: int | None = None) -> int:
    """Delete system_logs rows older than the retention window; return the count.

    Does not commit, so the caller decides the transaction boundary.
    """
    days = retention_days if retention_days is not None else log_retention_days()
    return (
        db.query(SystemLog)
        .filter(SystemLog.created_at < retention_cutoff(days))
        .delete(synchronize_session=False)
    )
