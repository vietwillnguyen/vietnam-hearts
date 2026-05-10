"""
Admin schedule management endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db, get_db_session
from app.services.google_sheets import sheets_service
from app.utils.logging_config import get_api_logger
from app.utils.config_helper import ConfigHelper

logger = get_api_logger()

router = APIRouter()


@router.get("/schedule-status")
def get_schedule_status(db: Session = Depends(get_db)):
    """Get current status of schedule sheets"""
    try:
        sheets = sheets_service.get_schedule_sheets(db)
        sheet_info = []
        for sheet in sheets:
            title = sheet["properties"]["title"]
            try:
                sheet_date = datetime.strptime(title.replace("Schedule ", ""), "%m/%d").replace(year=datetime.now().year)
            except ValueError:
                sheet_date = None
            sheet_info.append({
                "title": title,
                "date": sheet_date.isoformat() if sheet_date else None,
                "hidden": sheet["properties"].get("hidden", False),
                "index": sheet["properties"].get("index", 0),
            })
        sheet_info.sort(key=lambda x: x["date"] if x["date"] else "")
        visible_schedule_sheets = [
            s for s in sheet_info
            if not s["hidden"] and s["title"] != "Schedule Template"
        ]
        return {
            "status": "success",
            "details": {
                "display_weeks_count": len(visible_schedule_sheets),
                "configured_display_weeks": ConfigHelper.get_schedule_sheets_display_weeks_count(db),
                "total_sheets": len(sheet_info),
                "visible_sheets": len([s for s in sheet_info if not s["hidden"]]),
                "hidden_sheets": len([s for s in sheet_info if s["hidden"]]),
                "sheets": sheet_info,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get schedule status: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get schedule status: {str(e)}")


@router.post("/rotate-schedule")
async def rotate_schedule_sheets(
    request: Request,
    display_weeks: int = Query(None, description="Number of weeks to display (1-12), overrides default setting"),
):
    """Rotate schedule sheets to show next week"""
    try:
        with get_db_session() as db:
            if display_weeks is not None:
                if display_weeks < 1 or display_weeks > 12:
                    return {"status": "error", "message": "display_weeks must be between 1 and 12", "details": {"error": "Invalid display_weeks parameter"}}
                result = sheets_service.rotate_schedule_sheets(db, display_weeks_override=display_weeks)
            else:
                result = sheets_service.rotate_schedule_sheets(db)
            return {"status": "success", "message": "Schedule sheets rotated successfully", "details": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in rotate schedule sheets: {str(e)}", exc_info=True)
        return {"status": "error", "message": "Failed to rotate schedule sheets due to unexpected error", "details": {"error": str(e)}}
