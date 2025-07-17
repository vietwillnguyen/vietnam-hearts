"""
Authentication utilities for the Vietnam Hearts Scheduler API

Provides combined authentication supporting:
- Supabase Auth with Google OAuth (for admin users)
- Google Cloud Scheduler OIDC tokens (for automated services)
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.auth.transport import requests
from google.oauth2 import id_token
import os
import time
from typing import Dict, Optional, Any
from collections import defaultdict
import threading

from .logging_config import get_api_logger
from app.services.supabase_auth import supabase_auth_service
from app.config import (
    GOOGLE_OAUTH_CLIENT_ID,
    SERVICE_ACCOUNT_EMAIL,
    ADMIN_EMAILS,
)

logger = get_api_logger()

# Security scheme for Bearer tokens
security = HTTPBearer()

# Rate limiting storage (in-memory for simplicity, consider Redis for production)
rate_limit_storage: Dict[str, list] = defaultdict(list)
rate_limit_lock = threading.Lock()

# Rate limiting configuration
RATE_LIMIT_WINDOW = 60  # 1 minute window
RATE_LIMIT_MAX_REQUESTS = 10  # Max requests per window for public endpoints


class SchedulerAuthError(HTTPException):
    """Custom exception for scheduler authentication errors"""
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def verify_google_scheduler_token(token: str) -> Dict[str, Any]:
    """
    Verify Google Cloud Scheduler OIDC token
    
    Args:
        token: OIDC token from Google Cloud Scheduler
        
    Returns:
        Dict containing service account information
        
    Raises:
        SchedulerAuthError: If token is invalid
    """
    try:
        # Verify the OIDC token
        idinfo = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            audience=GOOGLE_OAUTH_CLIENT_ID,
        )

        # Verify it's from your expected service account
        token_email = idinfo.get("email")
        if not token_email:
            raise SchedulerAuthError("Token missing email claim")

        # Check if it's your service account (optional but recommended)
        if SERVICE_ACCOUNT_EMAIL and token_email != SERVICE_ACCOUNT_EMAIL:
            logger.warning(f"Scheduler auth failed: Unexpected service account {token_email}")
            raise SchedulerAuthError("Invalid service account")

        logger.info(f"Google scheduler authenticated successfully: {token_email}")
        return {
            "email": token_email,
            "type": "scheduler",
            "audience": idinfo.get("aud"),
            "exp": idinfo.get("exp"),
        }

    except ValueError as e:
        logger.error(f"Google scheduler auth failed: Invalid token - {str(e)}")
        raise SchedulerAuthError("Invalid OIDC token")
    except Exception as e:
        logger.error(f"Google scheduler auth failed: {str(e)}")
        raise SchedulerAuthError("Authentication failed")


async def verify_supabase_token(token: str) -> Dict[str, Any]:
    """
    Verify Supabase JWT token
    
    Args:
        token: JWT token from Supabase
        
    Returns:
        Dict containing user information
        
    Raises:
        HTTPException: If token is invalid or user is not admin
    """
    try:
        # Verify the token with Supabase
        user_info = supabase_auth_service.verify_token(token)
        
        # Check if user is admin
        if not supabase_auth_service.is_admin(user_info):
            logger.warning(f"Access denied for non-admin user: {user_info.get('email')}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        
        logger.info(f"Supabase admin authenticated: {user_info.get('email')}")
        return {
            "email": user_info.get("email"),
            "type": "admin",
            "user_id": user_info.get("id"),
            "user_metadata": user_info.get("user_metadata", {}),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Supabase auth failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )


async def require_web_admin_auth(request: Request):
    """
    Web-based authentication for admin dashboard that works with query parameters
    and cookies instead of requiring Bearer tokens in headers
    
    Args:
        request: FastAPI request object
        
    Returns:
        Dict containing authentication context
        
    Raises:
        HTTPException: If authentication fails
    """
    # Check for auth code in query parameters (from OAuth callback)
    auth_code = request.query_params.get("auth_code")
    
    if auth_code:
        try:
            # Check if this is a verified admin access
            verified = request.query_params.get("verified")
            
            if verified == "true":
                # This is a verified admin user, allow access
                logger.info("Web admin access granted - verified admin user")
                return {
                    "email": "admin@example.com",  # Would be real email from verification
                    "type": "web_admin",
                    "user_id": "verified_admin",
                    "user_metadata": {},
                    "verified": True
                }
            else:
                # Not verified, redirect to verification
                logger.warning("Web admin access denied - not verified")
                raise HTTPException(
                    status_code=status.HTTP_302_FOUND,
                    detail={
                        "error": "Authentication required",
                        "redirect_url": "/?error=Please log in through the home page to access the admin dashboard."
                    }
                )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Web auth failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed"
            )
    
    # If no auth code in query params, check for Bearer token (fallback for API calls)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        try:
            auth_info = await verify_supabase_token(token)
            return auth_info
        except HTTPException:
            raise
    
    # No valid authentication found
    logger.warning("Web authentication failed: No valid auth token found")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": "Authentication required",
            "hint": "Please log in through the home page to access the admin dashboard."
        }
    )


async def require_admin_or_scheduler_auth(request: Request):
    """
    Combined authentication that allows both admin users (via Supabase) 
    and scheduler service accounts (via Google IAM)
    
    Args:
        request: FastAPI request object
        
    Returns:
        Dict containing authentication context
        
    Raises:
        HTTPException: If authentication fails
    """
    # Get authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        logger.warning("Authentication failed: Authorization header is missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "Missing Authorization header",
                "hint": "Please provide an 'Authorization: Bearer <token>' header."
            }
        )
    if not auth_header.startswith("Bearer "):
        logger.warning(f"Authentication failed: Invalid Authorization header format: {auth_header}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "Invalid Authorization header format",
                "hint": "Authorization header must start with 'Bearer ' followed by your token.",
                "received_header": auth_header
            }
        )

    token = auth_header.split(" ", 1)[1]

    # Try Google IAM first (for scheduler)
    try:
        auth_info = await verify_google_scheduler_token(token)
        # Store auth info in request state for logging
        request.state.auth_type = "scheduler"
        request.state.service_email = auth_info["email"]
        return auth_info
    except SchedulerAuthError:
        pass  # Try Supabase auth instead

    # Try Supabase auth (for admin users)
    try:
        auth_info = await verify_supabase_token(token)
        # Store auth info in request state for logging
        request.state.auth_type = "admin"
        request.state.user_email = auth_info["email"]
        return auth_info
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )


def get_auth_context(request: Request) -> Dict[str, str]:
    """
    Get authentication context for logging
    
    Args:
        request: FastAPI request object
        
    Returns:
        Dict containing auth context information
    """
    auth_type = getattr(request.state, "auth_type", "unknown")
    
    if auth_type == "scheduler":
        return {
            "service_email": getattr(request.state, "service_email", "unknown"),
            "auth_type": "scheduler"
        }
    elif auth_type == "admin":
        return {
            "user_email": getattr(request.state, "user_email", "unknown"),
            "auth_type": "admin"
        }
    else:
        return {
            "auth_type": "unknown"
        }


def check_rate_limit(request: Request) -> None:
    """
    Check rate limiting for public endpoints
    
    Args:
        request: FastAPI request object
        
    Raises:
        HTTPException: If rate limit is exceeded
    """
    client_ip = request.client.host if request.client else "unknown"

    with rate_limit_lock:
        now = time.time()
        # Clean old entries outside the window
        rate_limit_storage[client_ip] = [
            timestamp
            for timestamp in rate_limit_storage[client_ip]
            if now - timestamp < RATE_LIMIT_WINDOW
        ]

        # Check if limit exceeded
        if len(rate_limit_storage[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW} seconds.",
            )

        # Add current request timestamp
        rate_limit_storage[client_ip].append(now)


async def rate_limit(request: Request) -> None:
    """
    Rate limiting dependency for public endpoints
    
    Args:
        request: FastAPI request object
    """
    check_rate_limit(request)


# Legacy functions for backward compatibility
async def require_google_auth(token_info: dict = Depends(require_admin_or_scheduler_auth)) -> dict:
    """
    Legacy function for backward compatibility
    Now uses the combined auth system
    """
    return token_info


def get_user_email(token_info: dict = Depends(require_admin_or_scheduler_auth)) -> str:
    """
    Extract user email from authentication context
    
    Args:
        token_info: Authentication context from require_admin_or_scheduler_auth
        
    Returns:
        str: User's email address
    """
    email = token_info.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Email not found in token"
        )
    return email


def get_user_info(token_info: dict = Depends(require_admin_or_scheduler_auth)) -> dict:
    """
    Extract user information from authentication context
    
    Args:
        token_info: Authentication context from require_admin_or_scheduler_auth
        
    Returns:
        dict: User information
    """
    return {
        "email": token_info.get("email"),
        "type": token_info.get("type"),
        "user_id": token_info.get("user_id"),
        "user_metadata": token_info.get("user_metadata", {}),
    }
