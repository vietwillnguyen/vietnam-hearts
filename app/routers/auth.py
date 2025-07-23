"""
Authentication Router

Handles Google OAuth sign-in, callback processing, and user session management
for the Vietnam Hearts application.
"""

from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from app.services.supabase_auth import auth_service, get_current_user, get_current_admin_user
from app.utils.logging_config import get_logger

logger = get_logger("auth_router")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Initialize templates
templates = Jinja2Templates(directory="templates")


class SignInRequest(BaseModel):
    """Request model for initiating sign-in"""
    redirect_to: Optional[str] = None


class RefreshSessionRequest(BaseModel):
    """Request model for refreshing session"""
    refresh_token: str


@router.get("/login")
async def login_page(request: Request):
    """
    Redirect to home page for login
    
    The login functionality is now integrated into the home page.
    """
    return RedirectResponse(url="/", status_code=302)


@router.post("/signin/google")
async def sign_in_with_google(request: SignInRequest) -> Dict[str, Any]:
    """
    Initiate Google OAuth sign-in process
    
    This endpoint generates a Google OAuth URL that the frontend can redirect to.
    The user will be redirected to Google's consent screen, and after approval,
    they'll be redirected back to the callback endpoint.
    """
    try:
        result = await auth_service.sign_in_with_google(request.redirect_to)
        logger.info("Google OAuth sign-in initiated successfully")
        return result
    except Exception as e:
        logger.error(f"Failed to initiate Google sign-in: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/callback")
async def auth_callback(
    request: Request,
    code: str,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None
) -> Response:
    """
    Handle OAuth callback from Google
    
    This endpoint processes the authorization code returned by Google
    and exchanges it for user session information.
    """
    try:
        # Check for OAuth errors
        if error:
            logger.error(f"OAuth error: {error} - {error_description}")
            return templates.TemplateResponse(
                "auth/oauth_error.html",
                {
                    "request": request,
                    "error": error,
                    "error_description": error_description
                },
                status_code=400
            )
        
        # Process the authorization code
        result = await auth_service.handle_auth_callback(code, state)
        
        # Check if user is an admin using the existing get_current_admin_user logic
        user_email = result['user']['email']
        logger.info(f"Checking admin access for user: {user_email}")
        
        try:
            # Use the existing is_admin method from auth_service
            is_admin = await auth_service.is_admin(user_email)
            
            if not is_admin:
                logger.warning(f"Access denied for non-admin user: {user_email}")
                error_message = f"Access denied. The email {user_email} is not authorized to access the admin system."
                return templates.TemplateResponse(
                    "auth/access_denied.html",
                    {
                        "request": request,
                        "error_message": error_message
                    },
                    status_code=403
                )
            
            logger.info(f"Admin access granted for user: {user_email}")
            
        except Exception as admin_check_error:
            logger.error(f"Failed to check admin status for {user_email}: {admin_check_error}")
            # If admin check fails, deny access for security
            error_message = f"Unable to verify admin access for {user_email}. Please contact the system administrator."
            return templates.TemplateResponse(
                "auth/access_verification_failed.html",
                {
                    "request": request,
                    "error_message": error_message
                },
                status_code=500
            )
        
        # User is admin, show success page
        return templates.TemplateResponse(
            "auth/signin_success.html",
            {
                "request": request,
                "user_email": result['user']['email'],
                "user_name": result['user']['name'],
                "access_token": result['session']['access_token'],
                "refresh_token": result['session']['refresh_token']
            }
        )
        
    except Exception as e:
        logger.error(f"Failed to handle auth callback: {str(e)}")
        return templates.TemplateResponse(
            "auth/auth_failed.html",
            {
                "request": request,
                "error_message": str(e)
            },
            status_code=400
        )


@router.get("/me")
async def get_current_user_info(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Get current user information
    
    Returns information about the currently authenticated user.
    """
    return {
        "user": current_user,
        "message": "Current user information retrieved successfully"
    }


@router.post("/signout")
async def sign_out(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, str]:
    """
    Sign out the current user
    
    Invalidates the current session and signs out the user.
    """
    try:
        result = await auth_service.sign_out(current_user.get("access_token", ""))
        logger.info(f"User {current_user['email']} signed out successfully")
        return result
    except Exception as e:
        logger.error(f"Failed to sign out user: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh")
async def refresh_session(request: RefreshSessionRequest) -> Dict[str, Any]:
    """
    Refresh the user's session
    
    Uses a refresh token to get a new access token.
    """
    try:
        result = await auth_service.refresh_session(request.refresh_token)
        logger.info("Session refreshed successfully")
        return result
    except Exception as e:
        logger.error(f"Failed to refresh session: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/users")
async def list_users(current_admin: Dict[str, Any] = Depends(get_current_admin_user)) -> Dict[str, Any]:
    """
    List all users (admin only)
    
    Returns a list of all users in the system. Admin access required.
    """
    try:
        # This is a simplified implementation
        # In production, you might want to implement pagination and filtering
        from supabase import create_client
        from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        
        admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        response = admin_supabase.auth.admin.list_users()
        
        users = []
        for user in response.users:
            users.append({
                "id": user.id,
                "email": user.email,
                "name": user.user_metadata.get("full_name"),
                "email_verified": user.email_confirmed_at is not None,
                "created_at": user.created_at,
                "last_sign_in": user.last_sign_in_at
            })
        
        logger.info(f"Admin {current_admin['email']} retrieved user list")
        return {
            "users": users,
            "total": len(users),
            "message": "User list retrieved successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to list users: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def auth_health_check() -> Dict[str, Any]:
    """
    Health check endpoint for authentication service
    
    Returns the status of the authentication service and configuration.
    """
    from app.config import SUPABASE_URL, SUPABASE_ANON_KEY, GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET
    
    config_status = {
        "supabase_url": bool(SUPABASE_URL),
        "supabase_anon_key": bool(SUPABASE_ANON_KEY),
        "GOOGLE_OAUTH_CLIENT_ID": bool(GOOGLE_OAUTH_CLIENT_ID),
        "GOOGLE_OAUTH_CLIENT_SECRET": bool(GOOGLE_OAUTH_CLIENT_SECRET)
    }
    
    all_configured = all(config_status.values())
    
    return {
        "status": "healthy" if all_configured else "configured_with_fallback",
        "service": "authentication",
        "message": "Authentication service is running" + (" (using mock auth)" if not all_configured else ""),
        "configuration": config_status,
        "all_configured": all_configured
    } 