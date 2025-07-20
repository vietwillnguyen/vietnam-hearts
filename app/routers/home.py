"""
Home router for Vietnam Hearts application

Provides the main landing page.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
# from typing import Optional  # Removed - no longer needed

from app.utils.logging_config import get_api_logger

logger = get_api_logger()

# Create router
home_router = APIRouter(tags=["home"])

# Initialize templates
templates = Jinja2Templates(directory="templates")


@home_router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """
    Main landing page
    
    Returns:
        HTMLResponse: Home page
    """
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": "Vietnam Hearts - Volunteer Management"
        }
    )


# Auth endpoints removed - authentication system disabled 