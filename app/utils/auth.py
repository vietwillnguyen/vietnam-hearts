"""
Authentication utilities for the Vietnam Hearts Scheduler API

Provides Google OAuth authentication dependencies and rate limiting for public endpoints.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.auth.transport import requests
from google.oauth2 import id_token
import os
import time
from typing import Dict
from collections import defaultdict
import threading

from .logging_config import get_api_logger

logger = get_api_logger()

# Security scheme for Google OAuth
security = HTTPBearer()

# Rate limiting storage (in-memory for simplicity, consider Redis for production)
rate_limit_storage: Dict[str, list] = defaultdict(list)
rate_limit_lock = threading.Lock()

# Rate limiting configuration
RATE_LIMIT_WINDOW = 60  # 1 minute window
RATE_LIMIT_MAX_REQUESTS = 10  # Max requests per window for public endpoints


def get_google_oauth_client_id() -> str:
    """Get Google OAuth client ID from environment"""
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth client ID not configured",
        )
    return client_id


async def verify_google_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Verify Google OAuth ID token

    Args:
        credentials: HTTP Bearer token from request

    Returns:
        dict: Decoded token payload with user information

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        token = credentials.credentials
        client_id = get_google_oauth_client_id()

        # Verify the token
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), client_id)

        # Check if token is expired
        if idinfo["exp"] < time.time():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired"
            )

        # Check if token was issued in the future (clock skew tolerance)
        if idinfo["iat"] > time.time() + 300:  # 5 minutes tolerance
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token issued in the future",
            )

        logger.info(
            f"Google OAuth token verified for user: {idinfo.get('email', 'unknown')}"
        )
        return idinfo

    except ValueError as e:
        logger.warning(f"Invalid Google OAuth token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    except Exception as e:
        logger.error(f"Error verifying Google OAuth token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed"
        )


async def require_google_auth(token_info: dict = Depends(verify_google_token)) -> dict:
    """
    Dependency that requires valid Google OAuth authentication

    Args:
        token_info: Verified token information from verify_google_token

    Returns:
        dict: Token information for use in route handlers
    """
    # Optional: Restrict to specific domains
    allowed_domains = os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",")
    if allowed_domains and allowed_domains[0]:  # Only check if domains are configured
        user_email = token_info.get("email", "")
        user_domain = user_email.split("@")[-1] if "@" in user_email else ""
        
        if user_domain not in allowed_domains:
            logger.warning(f"Access denied for domain: {user_domain}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Only users from {', '.join(allowed_domains)} are allowed.",
            )
    
    # Optional: Restrict to specific email addresses
    allowed_emails = os.getenv("ALLOWED_EMAILS", "").split(",")
    if allowed_emails and allowed_emails[0]:  # Only check if emails are configured
        user_email = token_info.get("email", "")
        
        if user_email not in allowed_emails:
            logger.warning(f"Access denied for email: {user_email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Your email is not authorized.",
            )
    
    return token_info


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


def get_user_email(token_info: dict = Depends(require_google_auth)) -> str:
    """
    Extract user email from Google OAuth token

    Args:
        token_info: Verified token information

    Returns:
        str: User's email address
    """
    email = token_info.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Email not found in token"
        )
    return email


def get_user_info(token_info: dict = Depends(require_google_auth)) -> dict:
    """
    Extract user information from Google OAuth token

    Args:
        token_info: Verified token information

    Returns:
        dict: User information including email, name, picture, etc.
    """
    return {
        "email": token_info.get("email"),
        "name": token_info.get("name"),
        "picture": token_info.get("picture"),
        "sub": token_info.get("sub"),  # Google user ID
        "hd": token_info.get("hd"),  # Hosted domain (for G Suite)
    }
