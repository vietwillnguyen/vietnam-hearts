"""
Supabase Auth service for Vietnam Hearts application

Provides authentication using Supabase with Google OAuth as a provider.
Handles admin role checking and session management.
"""

import os
from typing import Optional, Dict, Any
from fastapi import HTTPException, status, Request
from supabase import create_client, Client
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not supabase_url or not supabase_anon_key:
    raise ValueError(
        "Missing Supabase configuration. Please set SUPABASE_URL and SUPABASE_ANON_KEY environment variables."
    )

# Create Supabase client
supabase: Client = create_client(supabase_url, supabase_anon_key)

# Admin service client (for admin operations)
admin_supabase: Optional[Client] = None
if supabase_service_role_key:
    admin_supabase = create_client(supabase_url, supabase_service_role_key)


class SupabaseAuthService:
    """Service for handling Supabase authentication with Google OAuth"""
    
    @staticmethod
    def get_auth_url() -> str:
        """
        Get the Google OAuth sign-in URL for Supabase
        
        Returns:
            str: The sign-in URL
        """
        try:
            response = supabase.auth.sign_in_with_oauth({
                "provider": "google",
                "options": {
                    "redirect_to": f"{os.getenv('API_URL', 'http://localhost:8080')}/auth/supabase-callback"
                }
            })
            return response.url
        except Exception as e:
            logger.error(f"Failed to get auth URL: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Failed to generate authentication URL"
            )
    
    @staticmethod
    def verify_token(token: str) -> Dict[str, Any]:
        """
        Verify a Supabase JWT token
        
        Args:
            token: The JWT token to verify
            
        Returns:
            Dict containing user information
            
        Raises:
            HTTPException: If token is invalid
        """
        try:
            # Verify the token with Supabase
            response = supabase.auth.get_user(token)
            
            if not response.user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token"
                )
            
            user = response.user
            
            # Extract user information
            user_info = {
                "id": user.id,
                "email": user.email,
                "email_confirmed_at": user.email_confirmed_at,
                "created_at": user.created_at,
                "updated_at": user.updated_at,
                "user_metadata": user.user_metadata or {},
                "app_metadata": user.app_metadata or {},
            }
            
            logger.info(f"Token verified for user: {user.email}")
            return user_info
            
        except Exception as e:
            logger.error(f"Token verification failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token"
            )
    
    @staticmethod
    def is_admin(user_info: Dict[str, Any]) -> bool:
        """
        Check if user has admin privileges
        
        Args:
            user_info: User information from verify_token
            
        Returns:
            bool: True if user is admin
        """
        user_email = user_info.get("email", "")
        logger.info(f"Checking admin status for user: {user_email}")
        
        # Check user metadata for admin flag
        user_metadata = user_info.get("user_metadata", {})
        if user_metadata.get("is_admin"):
            logger.info(f"User {user_email} is admin via user_metadata")
            return True
        
        # Check app metadata for admin role
        app_metadata = user_info.get("app_metadata", {})
        if app_metadata.get("role") == "admin":
            logger.info(f"User {user_email} is admin via app_metadata")
            return True
        
        # Check allowed admin emails from environment
        allowed_admin_emails = os.getenv("ADMIN_EMAILS", "").split(",")
        # Clean up empty strings from split
        allowed_admin_emails = [email.strip() for email in allowed_admin_emails if email.strip()]
        
        logger.info(f"Allowed admin emails: {allowed_admin_emails}")
        
        if user_email in allowed_admin_emails:
            logger.info(f"User {user_email} is admin via ADMIN_EMAILS environment variable")
            return True
        
        logger.warning(f"User {user_email} is NOT an admin")
        return False
    
    @staticmethod
    def refresh_session(refresh_token: str) -> Dict[str, Any]:
        """
        Refresh a user session using refresh token
        
        Args:
            refresh_token: The refresh token
            
        Returns:
            Dict containing new session information
        """
        try:
            response = supabase.auth.refresh_session(refresh_token)
            return {
                "access_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "expires_at": response.session.expires_at,
                "user": response.user
            }
        except Exception as e:
            logger.error(f"Session refresh failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to refresh session"
            )
    
    @staticmethod
    def sign_out(token: str) -> bool:
        """
        Sign out a user (invalidate their session)
        
        Args:
            token: The user's access token
            
        Returns:
            bool: True if successful
        """
        try:
            supabase.auth.sign_out(token)
            return True
        except Exception as e:
            logger.error(f"Sign out failed: {str(e)}")
            return False


# Create service instance
supabase_auth_service = SupabaseAuthService() 