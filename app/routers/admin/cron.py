"""
Admin cron schedule management endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.cron_sync_service import sync_cron_schedules
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

router = APIRouter()


@router.post("/sync-cron-schedules")
def sync_cron_schedules_endpoint(db: Session = Depends(get_db)):
    """Apply the CRON_* settings to their Cloud Scheduler jobs.

    Makes the dashboard's cron fields authoritative instead of decorative.
    Only the cadence is written - job existence and the apikey header remain
    owned by scripts/create-or-update-scheduler-jobs.sh.
    """
    try:
        result = sync_cron_schedules(db)
    except ValueError as e:
        # Configuration problem (e.g. GCP_PROJECT_ID unset) - the caller can
        # fix this, so report it as a client-visible misconfiguration.
        logger.error(f"Cron sync misconfigured: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to sync cron schedules: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to sync cron schedules: {e}"
        ) from e

    failures = result.get("failed", [])
    if failures:
        # A partial sync must not read as success - that is exactly how the
        # rotation failures went unnoticed for weeks.
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"{len(failures)} cron job(s) could not be synced",
                "details": result,
            },
        )

    return {
        "status": "success",
        "message": f"{len(result['synced'])} job(s) updated, "
        f"{len(result['unchanged'])} already current",
        "details": result,
    }
