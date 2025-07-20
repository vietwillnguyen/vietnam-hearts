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
    Serve the login page
    
    Returns the login page template for Google OAuth sign-in.
    """
    return templates.TemplateResponse("auth/login.html", {"request": request})


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
            return HTMLResponse(
                content=f"""
                <html>
                    <body>
                        <h1>Authentication Error</h1>
                        <p>Error: {error}</p>
                        <p>Description: {error_description}</p>
                        <a href="/">Return to Home</a>
                    </body>
                </html>
                """,
                status_code=400
            )
        
        # Process the authorization code
        result = await auth_service.handle_auth_callback(code, state)
        
        # Create a success page that stores tokens and redirects
        html_content = f"""
        <html>
            <head>
                <title>Sign In Successful - Vietnam Hearts</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }}
                    .success {{ color: #28a745; }}
                    .loading {{ color: #007bff; }}
                    .spinner {{ border: 4px solid #f3f3f3; border-top: 4px solid #007bff; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 20px auto; }}
                    @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
                </style>
            </head>
            <body>
                <h1 class="success">✅ Sign In Successful!</h1>
                <p>Welcome to Vietnam Hearts, <strong>{result['user']['name'] or result['user']['email']}</strong>!</p>
                
                <div class="loading">
                    <div class="spinner"></div>
                    <p>Setting up your session...</p>
                </div>
                
                <script>
                    // Store tokens in localStorage
                    localStorage.setItem('access_token', '{result['session']['access_token']}');
                    localStorage.setItem('refresh_token', '{result['session']['refresh_token']}');
                    localStorage.setItem('user_email', '{result['user']['email']}');
                    localStorage.setItem('user_name', '{result['user']['name'] or ''}');
                    
                    // Redirect to dashboard after a short delay
                    setTimeout(() => {{
                        window.location.href = '/admin/dashboard';
                    }}, 2000);
                </script>
            </body>
        </html>
        """
        
        return HTMLResponse(content=html_content)
        
    except Exception as e:
        logger.error(f"Failed to handle auth callback: {str(e)}")
        return HTMLResponse(
            content=f"""
            <html>
                <body>
                    <h1>Authentication Failed</h1>
                    <p>Error: {str(e)}</p>
                    <a href="/">Return to Home</a>
                </body>
            </html>
            """,
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