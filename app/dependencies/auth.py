"""
Authentication Dependencies

Clean, single-purpose FastAPI dependencies for authentication.
"""

from typing import Dict, Any
from fastapi import Depends, Request
from app.services.auth_service import auth_service
from app.utils.logging_config import get_logger

logger = get_logger("auth_dependencies")


async def get_current_user(request: Request) -> Dict[str, Any]:
    """
    FastAPI dependency to get the current authenticated user.
    
    Handles multiple authentication sources:
    - Authorization header (Bearer token)
    - apikey header (service role key)
    - token query parameter
    - access_token cookie
    
    Args:
        request: FastAPI request object
        
    Returns:
        Dict containing user information
        
    Raises:
        HTTPException: If authentication fails
    """
    return await auth_service.get_current_user(request)


async def get_current_admin_user(request: Request) -> Dict[str, Any]:
    """
    FastAPI dependency to get the current authenticated admin user.
    
    This is the main dependency for admin endpoints. It combines:
    - User authentication
    - Admin status checking (with caching)
    - Performance optimization
    
    Args:
        request: FastAPI request object
        
    Returns:
        Dict containing admin user information
        
    Raises:
        HTTPException: If authentication fails or user is not admin
    """
    return await auth_service.get_current_admin_user(request)


# Legacy aliases for backward compatibility
get_current_admin_user_for_dashboard = get_current_admin_user
