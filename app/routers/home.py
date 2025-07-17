"""
Home router for Vietnam Hearts application

Provides the main landing page and handles authentication redirects.
"""

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from app.utils.logging_config import get_api_logger

logger = get_api_logger()

# Create router
home_router = APIRouter(tags=["home"])

# Initialize templates
templates = Jinja2Templates(directory="templates")


@home_router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """
    Main landing page with Google sign-in
    
    Returns:
        HTMLResponse: Home page with authentication options
    """
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": "Vietnam Hearts - Volunteer Management"
        }
    )


@home_router.get("/auth-success")
async def auth_success(
    request: Request,
    email: Optional[str] = Query(None),
    error: Optional[str] = Query(None)
):
    """
    Handle successful authentication redirect
    
    Args:
        request: FastAPI request object
        email: User email (if available)
        error: Error message (if any)
        
    Returns:
        RedirectResponse: Redirect to appropriate page
    """
    if error:
        logger.warning(f"Authentication error: {error}")
        return RedirectResponse(url="/?error=" + error)
    
    if email:
        logger.info(f"User {email} authenticated successfully, redirecting to dashboard")
        return RedirectResponse(url="/admin/dashboard")
    
    # Default redirect to dashboard
    return RedirectResponse(url="/admin/dashboard")


@home_router.get("/auth-denied")
async def auth_denied(
    request: Request,
    email: Optional[str] = Query(None)
):
    """
    Handle denied authentication redirect
    
    Args:
        request: FastAPI request object
        email: User email that was denied
        
    Returns:
        RedirectResponse: Redirect to home with error message
    """
    error_message = "Access denied. You do not have admin privileges."
    if email:
        logger.warning(f"Access denied for user: {email}")
        error_message = f"Access denied for {email}. You do not have admin privileges."
    
    return RedirectResponse(url=f"/?error={error_message}")


@home_router.get("/logout-success")
async def logout_success(request: Request):
    """
    Handle successful logout redirect
    
    Args:
        request: FastAPI request object
        
    Returns:
        RedirectResponse: Redirect to home with success message
    """
    logger.info("User logged out successfully")
    return RedirectResponse(url="/?success=Successfully logged out") 