"""
Admin user management endpoints
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies.auth import get_current_admin_user
from app.utils.logging_config import get_api_logger
from app.utils.timeout import timeout_handler

logger = get_api_logger()

router = APIRouter()


class AdminCreateRequest(BaseModel):
    email: str
    role: str = "admin"


class AdminRoleUpdateRequest(BaseModel):
    role: str


def _format_admin_list(admin_users):
    return [
        {
            "email": u.email,
            "role": u.role,
            "status": "active" if u.is_active else "inactive",
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in admin_users
    ]


@router.get("/users")
@timeout_handler(timeout_seconds=30.0)
async def get_admins(current_admin: dict[str, Any] = Depends(get_current_admin_user)):
    """Get all admin users and current user info"""
    try:
        from app.services.admin_service import admin_service

        admin_users = await admin_service.get_admin_users(current_admin["email"])
        admins = _format_admin_list(admin_users)
        current_role = next(
            (u["role"] for u in admins if u["email"] == current_admin["email"]), "admin"
        )
        return {
            "current_user": {"email": current_admin["email"], "role": current_role},
            "admins": admins,
            "total": len(admins),
            "message": "Admin list retrieved successfully",
        }
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get admins: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/all")
@timeout_handler(timeout_seconds=30.0)
async def get_all_admins(
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    """Get all admin users including inactive ones (super admins only)"""
    try:
        from app.services.admin_service import admin_service

        admin_users = await admin_service.get_all_admin_users(current_admin["email"])
        admins = _format_admin_list(admin_users)
        current_role = next(
            (u["role"] for u in admins if u["email"] == current_admin["email"]), "admin"
        )
        return {
            "current_user": {"email": current_admin["email"], "role": current_role},
            "admins": admins,
            "total": len(admins),
            "active_count": len([a for a in admins if a["status"] == "active"]),
            "inactive_count": len([a for a in admins if a["status"] == "inactive"]),
            "message": "All admin list retrieved successfully (including inactive users)",
        }
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get all admins: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users")
@timeout_handler(timeout_seconds=30.0)
async def create_admin(
    request: AdminCreateRequest,
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    """Add a new admin user"""
    try:
        if not request.email or "@" not in request.email:
            raise HTTPException(status_code=400, detail="Invalid email address")
        if request.role not in ["admin", "super_admin"]:
            raise HTTPException(
                status_code=400, detail="Invalid role. Must be 'admin' or 'super_admin'"
            )

        from app.services.admin_service import admin_service

        success = await admin_service.add_admin_user(
            email=request.email,
            role=request.role,
            added_by_email=current_admin["email"],
        )
        if success:
            logger.info(
                f"Super admin {current_admin['email']} added admin {request.email}"
            )
            return {
                "status": "success",
                "message": f"Admin {request.email} added successfully with role {request.role}",
                "admin_email": request.email,
                "role": request.role,
            }
        raise HTTPException(
            status_code=400,
            detail=f"Failed to add admin {request.email}. They may already exist.",
        )

    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create admin: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/users/{email}/role")
@timeout_handler(timeout_seconds=30.0)
async def update_admin_role(
    email: str,
    request: AdminRoleUpdateRequest,
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    """Update an admin's role"""
    try:
        if request.role not in ["admin", "super_admin"]:
            raise HTTPException(
                status_code=400, detail="Invalid role. Must be 'admin' or 'super_admin'"
            )
        if request.role == "admin" and email == current_admin["email"]:
            raise HTTPException(
                status_code=400, detail="Cannot demote yourself from super admin"
            )

        from app.services.admin_service import admin_service

        success = await admin_service.update_admin_role(
            email=email, new_role=request.role, updated_by_email=current_admin["email"]
        )
        if success:
            logger.info(
                f"Super admin {current_admin['email']} changed {email} role to {request.role}"
            )
            return {
                "status": "success",
                "message": f"Admin {email} role updated to {request.role}",
                "admin_email": email,
                "new_role": request.role,
            }
        raise HTTPException(
            status_code=404, detail=f"Admin {email} not found or update failed"
        )

    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update admin role: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{email}")
@timeout_handler(timeout_seconds=30.0)
async def remove_admin(
    email: str,
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    """Deactivate an admin user"""
    try:
        if email == current_admin["email"]:
            raise HTTPException(
                status_code=400, detail="Cannot remove yourself as admin"
            )

        from app.services.admin_service import admin_service

        success = await admin_service.remove_admin_user(
            email=email, removed_by_email=current_admin["email"]
        )
        if success:
            logger.info(f"Super admin {current_admin['email']} removed admin {email}")
            return {
                "status": "success",
                "message": f"Admin {email} has been deactivated",
                "admin_email": email,
                "action": "deactivated",
            }
        raise HTTPException(
            status_code=404, detail=f"Admin {email} not found or removal failed"
        )

    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove admin: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{email}/permanent")
@timeout_handler(timeout_seconds=30.0)
async def delete_admin_permanently(
    email: str,
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    """Permanently delete an admin user from the database"""
    try:
        if email == current_admin["email"]:
            raise HTTPException(
                status_code=400, detail="Cannot delete yourself as admin"
            )

        from app.services.admin_service import admin_service

        success = await admin_service.delete_admin_user(
            email=email, deleted_by_email=current_admin["email"]
        )
        if success:
            logger.info(
                f"Super admin {current_admin['email']} permanently deleted admin {email}"
            )
            return {
                "status": "success",
                "message": f"Admin {email} has been permanently deleted from database",
                "admin_email": email,
                "action": "permanently_deleted",
            }
        raise HTTPException(
            status_code=404, detail=f"Admin {email} not found or deletion failed"
        )

    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete admin: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
