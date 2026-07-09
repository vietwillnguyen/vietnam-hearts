"""
Admin router package

Assembles all admin feature sub-routers under the /admin prefix with
shared authentication. Exported admin_router is the single mount point
used in main.py.
"""

from fastapi import APIRouter, Depends
from app.dependencies.auth import get_current_admin_user

from app.routers.admin import volunteers, emails, signups, schedules, users, health, logs

admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_admin_user)],
)

admin_router.include_router(volunteers.router)
admin_router.include_router(emails.router)
admin_router.include_router(signups.router)
admin_router.include_router(schedules.router)
admin_router.include_router(users.router)
admin_router.include_router(health.router)
admin_router.include_router(logs.router)
