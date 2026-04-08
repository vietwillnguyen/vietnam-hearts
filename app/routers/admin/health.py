"""
Admin health check, dashboard, and config-validation endpoints
"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from datetime import datetime
from typing import Dict, Any

from app.database import get_db
from app.models import (
    Volunteer as VolunteerModel,
    EmailCommunication as EmailCommunicationModel,
)
from app.services.google_sheets import sheets_service
from app.services.email_service import email_service
from app.utils.logging_config import get_api_logger
from app.utils.config_helper import ConfigHelper
from app.utils.timeout import timeout_handler
from app.dependencies.auth import get_current_admin_user
from app.config import ENVIRONMENT
from app.routers.admin.helpers import get_volunteer_summary, get_email_summary

logger = get_api_logger()

router = APIRouter()
templates = Jinja2Templates(directory="templates/web")


@router.get("/dashboard", response_class=HTMLResponse)
@timeout_handler(timeout_seconds=10.0)
async def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Render the admin dashboard page"""
    try:
        volunteers = db.query(VolunteerModel).options(
            joinedload(VolunteerModel.email_communications)
        ).all()
        volunteer_data = get_volunteer_summary(volunteers)

        communications = (
            db.query(EmailCommunicationModel)
            .options(joinedload(EmailCommunicationModel.volunteer))
            .order_by(EmailCommunicationModel.sent_at.desc())
            .limit(100)
            .all()
        )
        email_data = get_email_summary(communications)

        from app.services.settings_service import get_all_settings
        settings = [
            {"key": s.key, "value": s.value, "description": s.description}
            for s in get_all_settings(db)
        ]

        from app.config import APPLICATION_VERSION
        return templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            {
                "total_volunteers": len(volunteer_data),
                "total_emails": len(email_data),
                "volunteers": volunteer_data,
                "emails": email_data,
                "settings": settings,
                "version": APPLICATION_VERSION,
            },
        )
    except Exception as e:
        logger.error(f"Failed to render admin dashboard: {str(e)}", exc_info=True)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/?error=Authentication failed", status_code=302)


@router.get("/config/validate")
def validate_configuration(db: Session = Depends(get_db)):
    """Validate that all required configuration settings are present"""
    try:
        missing_settings = []
        schedule_sheet_id = ConfigHelper.get_schedule_sheet_id(db)
        if not schedule_sheet_id:
            missing_settings.append("SCHEDULE_SIGNUP_LINK")

        new_signups_sheet_id = ConfigHelper.get_new_signups_sheet_id(db)
        if not new_signups_sheet_id:
            missing_settings.append("NEW_SIGNUPS_RESPONSES_LINK")

        if missing_settings:
            return {
                "status": "error",
                "message": f"Missing required settings: {', '.join(missing_settings)}",
                "missing_settings": missing_settings,
                "instructions": "Please configure these settings via the /settings/ endpoint",
            }
        return {
            "status": "success",
            "message": "All required configuration is set",
            "schedule_sheet_id": schedule_sheet_id,
            "new_signups_sheet_id": new_signups_sheet_id,
        }
    except Exception as e:
        logger.error(f"Configuration validation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Configuration validation failed: {str(e)}")


@router.get("/health")
async def comprehensive_health_check(db: Session = Depends(get_db)):
    """Comprehensive health check for all admin services"""
    try:
        health_status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "environment": ENVIRONMENT,
            "services": {},
        }

        try:
            db.execute("SELECT 1")
            health_status["services"]["database"] = "connected"
        except Exception as e:
            health_status["services"]["database"] = f"error: {str(e)}"
            health_status["status"] = "degraded"

        try:
            sheets_service.get_current_schedule_dates(db)
            health_status["services"]["google_sheets"] = "connected"
        except Exception as e:
            health_status["services"]["google_sheets"] = f"error: {str(e)}"
            health_status["status"] = "degraded"

        try:
            email_service.get_reminder_subject(datetime.now().date(), datetime.now().date())
            health_status["services"]["email_service"] = "available"
        except Exception as e:
            health_status["services"]["email_service"] = f"error: {str(e)}"
            health_status["status"] = "degraded"

        try:
            from app.services.admin_service import admin_service
            from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
            supabase_configured = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
            supabase_client_initialized = admin_service.admin_supabase is not None
            admin_service_available = False
            if supabase_configured and supabase_client_initialized:
                try:
                    await asyncio.wait_for(admin_service._check_admin_db("test@example.com"), timeout=5.0)
                    admin_service_available = True
                except (asyncio.TimeoutError, Exception):
                    pass
            health_status["services"]["admin_service"] = {
                "available": admin_service_available,
                "supabase_configured": supabase_configured,
                "supabase_client_initialized": supabase_client_initialized,
            }
            if not admin_service_available:
                health_status["status"] = "degraded"
        except Exception as e:
            health_status["services"]["admin_service"] = f"error: {str(e)}"
            health_status["status"] = "degraded"

        try:
            from app.services.bot_service import BotService
            bot_service = BotService()
            bot_status = await bot_service.get_knowledge_status()
            health_status["services"]["bot_service"] = {
                "knowledge_base": bot_status["knowledge_service_available"],
                "embeddings": bot_status["embeddings_available"],
                "gemini": bot_status["gemini_available"],
                "google_docs": bot_status["document_service_available"],
                "supabase": bot_status["supabase_available"],
                "documents_count": bot_status["documents_count"],
            }
            if not any([bot_status["knowledge_service_available"], bot_status["embeddings_available"], bot_status["gemini_available"]]):
                health_status["status"] = "degraded"
        except Exception as e:
            health_status["services"]["bot_service"] = f"error: {str(e)}"
            health_status["status"] = "degraded"

        if health_status["status"] == "healthy" and any("error" in str(s) for s in health_status["services"].values()):
            health_status["status"] = "degraded"

        return health_status

    except Exception as e:
        logger.error(f"Comprehensive health check failed: {str(e)}", exc_info=True)
        return {"status": "unhealthy", "timestamp": datetime.now().isoformat(), "error": str(e)}
