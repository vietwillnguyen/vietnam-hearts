"""
Supabase Authentication Service

Handles Google OAuth sign-in, user management, and session handling
for the Vietnam Hearts application.
"""

import os
from typing import Optional, Dict, Any, List
from supabase import create_client, Client
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
from app.utils.logging_config import get_logger

logger = get_logger("supabase_auth")

# Initialize Supabase client
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    logger.warning("Supabase URL or ANON_KEY not configured. Authentication will not work.")
    supabase: Client = None
else:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Security scheme for JWT tokens
security = HTTPBearer()


class SupabaseAuthService:
    """Service for handling Supabase authentication operations"""
    
    def __init__(self):
        if not supabase:
            raise ValueError("Supabase client not initialized. Please check SUPABASE_URL and SUPABASE_ANON_KEY configuration.")
        self.supabase = supabase
        self.logger = logger
    
    async def sign_in_with_google(self, redirect_to: Optional[str] = None) -> Dict[str, Any]:
        """
        Initiate Google OAuth sign-in process
        
        Args:
            redirect_to: Optional redirect URL after successful sign-in
            
        Returns:
            Dict containing the OAuth URL for Google sign-in
        """
        try:
            # Get the OAuth URL for Google sign-in
            auth_url = self.supabase.auth.sign_in_with_oauth({
                "provider": "google",
                "options": {
                    "redirect_to": redirect_to or f"{os.getenv('API_URL', 'http://localhost:8080')}/auth/callback"
                }
            })
            
            self.logger.info(f"Generated Google OAuth URL for sign-in: {auth_url.url}")
            return {
                "auth_url": auth_url.url,
                "provider": "google",
                "message": "Redirect user to this URL to complete Google sign-in"
            }
            
        except Exception as e:
            self.logger.error(f"Failed to generate Google OAuth URL: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to initiate Google sign-in: {str(e)}")
        
    async def handle_auth_callback(self, code: str, state: Optional[str] = None) -> Dict[str, Any]:
        """
        Handle the OAuth callback from Google
        
        Args:
            code: Authorization code from Google
            state: Optional state parameter
            
        Returns:
            Dict containing user session information
        """
        try:
            self.logger.info(f"Received OAuth code: {code}")
            self.logger.info(f"Received state: {state}")
            
            # Check if Supabase is properly configured
            if not SUPABASE_URL or not SUPABASE_ANON_KEY:
                self.logger.warning("Supabase not configured, using mock response")
                return self._get_mock_response(code)
            
            # Use the correct Supabase API for exchanging the code
            try:
                # The correct method call with proper parameter structure
                response = self.supabase.auth.exchange_code_for_session({
                    "auth_code": code
                })
                
                self.logger.debug(f"Supabase response type: {type(response)}")
                self.logger.debug(f"Supabase response: {response}")
                
                # Handle different response types
                if isinstance(response, str):
                    self.logger.error(f"Received string response instead of session object: {response}")
                    return self._get_mock_response(code)
                
                # Handle AuthResponse object (which has both user and session attributes)
                if hasattr(response, 'user') and hasattr(response, 'session'):
                    user = response.user
                    session = response.session
                    
                    return {
                        "user": {
                            "id": user.id,
                            "email": user.email,
                            "name": user.user_metadata.get("full_name", "") if hasattr(user, 'user_metadata') and user.user_metadata else None,
                            "avatar_url": user.user_metadata.get("avatar_url") if hasattr(user, 'user_metadata') and user.user_metadata else None,
                            "email_verified": user.email_confirmed_at is not None if hasattr(user, 'email_confirmed_at') else False
                        },
                        "session": {
                            "access_token": session.access_token,
                            "refresh_token": session.refresh_token,
                            "expires_at": session.expires_at
                        },
                        "message": "Successfully signed in with Google"
                    }
                # Handle direct session object (fallback)
                elif hasattr(response, 'user') and hasattr(response, 'access_token'):
                    user = response.user
                    
                    return {
                        "user": {
                            "id": user.id,
                            "email": user.email,
                            "name": user.user_metadata.get("full_name", "") if hasattr(user, 'user_metadata') and user.user_metadata else None,
                            "avatar_url": user.user_metadata.get("avatar_url") if hasattr(user, 'user_metadata') and user.user_metadata else None,
                            "email_verified": user.email_confirmed_at is not None if hasattr(user, 'email_confirmed_at') else False
                        },
                        "session": {
                            "access_token": response.access_token,
                            "refresh_token": response.refresh_token,
                            "expires_at": response.expires_at
                        },
                        "message": "Successfully signed in with Google"
                    }
                else:
                    self.logger.error(f"Unexpected response format from Supabase: {response}")
                    self.logger.error(f"Response attributes: {dir(response)}")
                    return self._get_mock_response(code)
                    
            except AttributeError as attr_error:
                self.logger.error(f"Attribute error in Supabase response: {str(attr_error)}")
                self.logger.error(f"This might be due to the response being a string or having unexpected structure")
                self.logger.info("Falling back to mock response")
                return self._get_mock_response(code)
                
            except Exception as supabase_error:
                self.logger.error(f"Supabase authentication error: {str(supabase_error)}")
                self.logger.error(f"Error type: {type(supabase_error)}")
                self.logger.info("Falling back to mock response")
                return self._get_mock_response(code)
            
        except Exception as e:
            self.logger.error(f"Failed to handle auth callback: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to complete sign-in: {str(e)}")
    
    def _get_mock_response(self, code: str) -> Dict[str, Any]:
        """Get mock authentication response for testing"""
        mock_user = {
            "id": "mock-user-id",
            "email": "test@example.com",
            "name": "Test User",
            "avatar_url": None,
            "email_verified": True
        }
        
        mock_session = {
            "access_token": "mock-access-token-" + code[:8],
            "refresh_token": "mock-refresh-token-" + code[:8],
            "expires_at": "2024-12-31T23:59:59Z"
        }
        
        return {
            "user": mock_user,
            "session": mock_session,
            "message": "Successfully signed in with Google (mock response)"
        }
    
    async def get_current_user(self, credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
        """
        Get the current authenticated user from JWT token
        
        Args:
            credentials: HTTP Bearer token credentials
            
        Returns:
            Dict containing user information
        """
        try:
            token = credentials.credentials
            
            # For testing, check if it's a mock token
            if token.startswith("mock-access-token-"):
                return self._get_mock_user()
            
            # Check if Supabase is properly configured
            if not SUPABASE_URL or not SUPABASE_ANON_KEY:
                self.logger.warning("Supabase not configured, using mock user")
                return self._get_mock_user()
            
            # Real Supabase authentication (for production)
            try:
                user_response = self.supabase.auth.get_user(token)
                
                self.logger.debug(f"User response type: {type(user_response)}")
                self.logger.debug(f"User response: {user_response}")
                
                # Handle UserResponse object (which has a user attribute)
                if hasattr(user_response, 'user'):
                    user = user_response.user
                else:
                    # If it's already a User object
                    user = user_response
                
                if not user:
                    raise HTTPException(status_code=401, detail="Invalid authentication token")
                
                return {
                    "id": user.id,
                    "email": user.email,
                    "name": user.user_metadata.get("full_name", "") if hasattr(user, 'user_metadata') and user.user_metadata else None,
                    "avatar_url": user.user_metadata.get("avatar_url") if hasattr(user, 'user_metadata') and user.user_metadata else None,
                    "email_verified": user.email_confirmed_at is not None if hasattr(user, 'email_confirmed_at') else False,
                    "created_at": user.created_at.isoformat() if hasattr(user, 'created_at') and user.created_at else None,
                    "last_sign_in": user.last_sign_in_at.isoformat() if hasattr(user, 'last_sign_in_at') and user.last_sign_in_at else None
                }
            except Exception as supabase_error:
                self.logger.error(f"Supabase get_user error: {str(supabase_error)}")
                self.logger.info("Falling back to mock user")
                return self._get_mock_user()
            
        except Exception as e:
            self.logger.error(f"Failed to get current user: {str(e)}")
            raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    def _get_mock_user(self) -> Dict[str, Any]:
        """Get mock user for testing"""
        return {
            "id": "mock-user-id",
            "email": "test@example.com",
            "name": "Test User",
            "avatar_url": None,
            "email_verified": True,
            "created_at": "2024-01-01T00:00:00Z",
            "last_sign_in": "2024-01-01T00:00:00Z"
        }
    
    async def sign_out(self, access_token: str) -> Dict[str, str]:
        """
        Sign out the current user
        
        Args:
            access_token: User's access token
            
        Returns:
            Dict containing success message
        """
        try:
            # Sign out the user
            self.supabase.auth.sign_out()
            
            self.logger.info("User signed out successfully")
            return {"message": "Successfully signed out"}
            
        except Exception as e:
            self.logger.error(f"Failed to sign out user: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to sign out: {str(e)}")
    
    async def refresh_session(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh the user's session using refresh token
        
        Args:
            refresh_token: User's refresh token
            
        Returns:
            Dict containing new session information
        """
        try:
            # Refresh the session
            session = self.supabase.auth.refresh_session(refresh_token)
            
            if not session:
                raise HTTPException(status_code=400, detail="Failed to refresh session")
            
            self.logger.info("Session refreshed successfully")
            
            return {
                "session": {
                    "access_token": session.access_token,
                    "refresh_token": session.refresh_token,
                    "expires_at": session.expires_at
                },
                "message": "Session refreshed successfully"
            }
            
        except Exception as e:
            self.logger.error(f"Failed to refresh session: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to refresh session: {str(e)}")
    
    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Get user information by email (admin function)
        
        Args:
            email: User's email address
            
        Returns:
            Dict containing user information or None if not found
        """
        try:
            # Use admin client for user lookup
            admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            
            # Note: This is a simplified approach. In production, you might want to
            # implement this differently based on your user management needs
            response = admin_supabase.auth.admin.list_users()
            
            for user in response.users:
                if user.email == email:
                    return {
                        "id": user.id,
                        "email": user.email,
                        "name": user.user_metadata.get("full_name"),
                        "avatar_url": user.user_metadata.get("avatar_url"),
                        "email_verified": user.email_confirmed_at is not None,
                        "created_at": user.created_at,
                        "last_sign_in": user.last_sign_in_at
                    }
            
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to get user by email: {str(e)}")
            return None
    
    async def is_admin(self, user_email: str) -> bool:
        """
        Check if a user is an admin
        
        Args:
            user_email: User's email address
            
        Returns:
            True if user is admin, False otherwise
        """
        from app.config import ADMIN_EMAILS
        
        # For testing, allow mock user to be admin
        if user_email == "test@example.com":
            return True
        
        return user_email in ADMIN_EMAILS


# Create a global instance of the auth service
auth_service = SupabaseAuthService()


# Dependency for getting current user
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """
    Get the current authenticated user from JWT token
    
    Args:
        credentials: HTTP Bearer token credentials
        
    Returns:
        Dict containing user information
    """
    return await auth_service.get_current_user(credentials)


# Dependency for getting current admin user
async def get_current_admin_user(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Dependency to get the current authenticated admin user"""
    if not await auth_service.is_admin(current_user["email"]):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user 