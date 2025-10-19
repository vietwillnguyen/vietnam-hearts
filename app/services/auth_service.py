"""
Unified Authentication Service

Combines Supabase authentication and admin management into a single,
high-performance service with caching and optimized database calls.
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime
import asyncio
import time
from supabase import create_client, Client
from fastapi import HTTPException, Request
from app.config import SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, ADMIN_EMAILS
from app.utils.logging_config import get_logger

logger = get_logger("auth_service")


@dataclass
class AdminUser:
    """Admin user data structure"""
    id: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None


class AuthService:
    """
    Unified authentication service that handles:
    - User authentication via Supabase
    - Admin status checking with caching
    - Session management
    - Performance optimization
    """
    
    def __init__(self):
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            raise ValueError("Supabase configuration missing")
        
        # Initialize Supabase clients
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        self.admin_supabase: Optional[Client] = None
        
        if SUPABASE_SERVICE_ROLE_KEY:
            self.admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        
        # Admin status cache
        self._admin_cache: Dict[str, Dict[str, Any]] = {email: {"is_admin": True, "timestamp": time.time(), "source": "environment"} for email in ADMIN_EMAILS}
        self._cache_ttl = 300  # 5 minutes
        self._last_cache_cleanup = time.time()
        
        logger.info("AuthService initialized with caching enabled")
    
    def _cleanup_cache(self):
        """Clean up expired cache entries"""
        current_time = time.time()
        if current_time - self._last_cache_cleanup > 60:  # Cleanup every minute
            expired_keys = [
                key for key, data in self._admin_cache.items()
                if current_time - data["timestamp"] > self._cache_ttl
            ]
            for key in expired_keys:
                del self._admin_cache[key]
            self._last_cache_cleanup = current_time
    
    async def sign_in_with_google(self, redirect_to: Optional[str] = None) -> Dict[str, Any]:
        """Initiate Google OAuth sign-in process"""
        try:
            from app.config import API_URL
            final_redirect_to = redirect_to or f"{API_URL}/auth/callback"
            
            auth_url = self.supabase.auth.sign_in_with_oauth({
                "provider": "google",
                "options": {
                    "redirect_to": final_redirect_to
                }
            })
            
            logger.info(f"Generated Google OAuth URL for sign-in")
            return {
                "auth_url": auth_url.url,
                "provider": "google",
                "message": "Redirect user to this URL to complete Google sign-in"
            }
            
        except Exception as e:
            logger.error(f"Failed to generate Google OAuth URL: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to initiate Google sign-in: {str(e)}")
    
    async def handle_auth_callback(self, code: str, state: Optional[str] = None) -> Dict[str, Any]:
        """Handle OAuth callback from Google"""
        try:
            logger.info(f"Processing OAuth callback with code: {code[:10]}...")
            
            response = self.supabase.auth.exchange_code_for_session({
                "auth_code": code
            })
            
            # Handle AuthResponse object
            if hasattr(response, 'user') and hasattr(response, 'session'):
                user = response.user
                session = response.session
                
                return {
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "name": user.user_metadata.get("full_name", "") if user.user_metadata else None,
                        "avatar_url": user.user_metadata.get("avatar_url") if user.user_metadata else None,
                        "email_verified": user.email_confirmed_at is not None
                    },
                    "session": {
                        "access_token": session.access_token,
                        "refresh_token": session.refresh_token,
                        "expires_at": session.expires_at
                    },
                    "message": "Successfully signed in with Google"
                }
            else:
                logger.error(f"Unexpected response format from Supabase: {response}")
                raise HTTPException(status_code=500, detail="Invalid response from authentication service")
                
        except Exception as e:
            logger.error(f"Failed to handle auth callback: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to complete sign-in: {str(e)}")
    
    async def get_current_user(self, request: Request) -> Dict[str, Any]:
        """
        Get current authenticated user from various token sources
        
        Handles:
        - Authorization header (Bearer token)
        - apikey header (service role key)
        - token query parameter
        - access_token cookie
        """
        token = None
        
        # Try to get token from various sources
        if "authorization" in request.headers:
            auth_header = request.headers["authorization"]
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        elif "apikey" in request.headers:
            return await self._get_user_from_apikey(request.headers["apikey"])
        elif "token" in request.query_params:
            token = request.query_params["token"]
        elif request.cookies.get("access_token"):
            token = request.cookies.get("access_token")
        
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        return await self._get_user_from_token(token)
    
    async def get_current_admin_user(self, request: Request) -> Dict[str, Any]:
        """
        Get current authenticated admin user
        
        This is the main method that should be used for admin endpoints.
        It combines user authentication and admin checking in a single call.
        """
        # Get authenticated user
        user = await self.get_current_user(request)
        
        # Check admin status (with caching)
        is_admin = await self._is_admin_cached(user["email"])
        
        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        
        logger.info(f"Admin access granted for user: {user.get('email', 'No email')}")
        return user
    
    async def _get_user_from_token(self, token: str) -> Dict[str, Any]:
        """Get user information from JWT token"""
        try:
            user_response = self.supabase.auth.get_user(token)
            
            # Handle UserResponse object
            if hasattr(user_response, 'user'):
                user = user_response.user
            else:
                user = user_response
            
            if not user:
                raise HTTPException(status_code=401, detail="Invalid authentication token")
            
            return {
                "id": user.id,
                "email": user.email,
                "name": user.user_metadata.get("full_name", "") if hasattr(user, 'user_metadata') and user.user_metadata else None,
                "avatar_url": user.user_metadata.get("avatar_url") if hasattr(user, 'user_metadata') and user.user_metadata else None,
                "email_verified": user.email_confirmed_at is not None if hasattr(user, 'email_confirmed_at') and user.email_confirmed_at else False,
                "created_at": user.created_at.isoformat() if hasattr(user, 'created_at') and user.created_at else None,
                "last_sign_in": user.last_sign_in_at.isoformat() if hasattr(user, 'last_sign_in_at') and user.last_sign_in_at else None
            }
        except Exception as e:
            logger.error(f"Failed to get user from token: {str(e)}")
            raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    async def _get_user_from_apikey(self, apikey: str) -> Dict[str, Any]:
        """Get user information from service role key"""
        try:
            # Check if it's a valid service role key
            if not self._is_service_role_key(apikey):
                raise HTTPException(status_code=401, detail="Invalid service role key")
            
            # Validate against configured service role key
            if apikey != SUPABASE_SERVICE_ROLE_KEY:
                raise HTTPException(status_code=401, detail="Invalid service role key")
            
            # Return service account user info
            return {
                "id": "service-account-auto-scheduler",
                "email": "auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com",
                "name": "Auto Scheduler Service Account",
                "avatar_url": None,
                "email_verified": True,
                "created_at": "2024-01-01T00:00:00Z",
                "last_sign_in": "2024-01-01T00:00:00Z"
            }
        except Exception as e:
            logger.error(f"Failed to get user from apikey: {str(e)}")
            raise HTTPException(status_code=401, detail="Invalid service role key")
    
    def _is_service_role_key(self, token: str) -> bool:
        """Check if the token is a valid service role key"""
        try:
            return (token.startswith("eyJ") and len(token) > 100) or (token.startswith("sb_") and len(token) > 50)
        except Exception:
            return False
    
    async def _is_admin_cached(self, email: str) -> bool:
        """
        Check if user is admin with caching for performance
        
        This method implements the caching strategy:
        1. Check cache first (fastest)
        2. Check environment variables (fast)
        3. Check database (slowest, but cached)
        """
        # Clean up expired cache entries
        self._cleanup_cache()
        
        # Check cache first
        if email in self._admin_cache:
            cache_data = self._admin_cache[email]
            if time.time() - cache_data["timestamp"] < self._cache_ttl:
                logger.debug(f"Admin status from cache: {email} = {cache_data['is_admin']}")
                return cache_data["is_admin"]
        
        # Check environment variables (fastest)
        if email in ADMIN_EMAILS:
            self._admin_cache[email] = {
                "is_admin": True,
                "timestamp": time.time(),
                "source": "environment"
            }
            logger.info(f"Admin access granted via environment: {email}")
            return True
        
        # Check database (slowest, but cached)
        is_admin = await self._check_admin_db(email)
        self._admin_cache[email] = {
            "is_admin": is_admin,
            "timestamp": time.time(),
            "source": "database"
        }
        
        if is_admin:
            logger.info(f"Admin access granted via database: {email}")
            # Update last login asynchronously (don't wait for it)
            asyncio.create_task(self._update_last_login_async(email))
        else:
            logger.warning(f"Access denied for non-admin user: {email}")
        
        return is_admin
    
    async def _check_admin_db(self, email: str) -> bool:
        """Check admin status in database with timeout"""
        try:
            if not self.admin_supabase:
                logger.error("Admin Supabase client not initialized")
                return False
            
            # Use timeout to prevent hanging
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").select("email").eq(
                        "email", email.lower()
                    ).eq("is_active", True).execute()
                ),
                timeout=5.0  # Reduced timeout for better performance
            )
            
            return len(result.data) > 0
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout checking admin status for {email}")
            return False
        except Exception as e:
            logger.error(f"Failed to check admin status for {email}: {e}")
            return False
    
    async def _update_last_login_async(self, email: str):
        """Update last login timestamp asynchronously (non-blocking)"""
        try:
            if not self.admin_supabase:
                return
            
            await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.rpc("update_admin_last_login", {"user_email": email}).execute()
                ),
                timeout=3.0  # Short timeout for non-critical operation
            )
        except Exception as e:
            # Don't log as error since this is non-critical
            logger.debug(f"Failed to update last login for {email}: {e}")
    
    async def sign_out(self, access_token: Optional[str] = None) -> Dict[str, str]:
        """Sign out the current user"""
        try:
            self.supabase.auth.sign_out()
            logger.info("User signed out successfully")
            return {"message": "Successfully signed out"}
        except Exception as e:
            logger.error(f"Failed to sign out user: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to sign out: {str(e)}")
    
    async def refresh_session(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh the user's session using refresh token"""
        try:
            session = self.supabase.auth.refresh_session(refresh_token)
            
            if not session:
                raise HTTPException(status_code=400, detail="Failed to refresh session")
            
            logger.info("Session refreshed successfully")
            
            return {
                "session": {
                    "access_token": session.access_token,
                    "refresh_token": session.refresh_token,
                    "expires_at": session.expires_at
                },
                "message": "Session refreshed successfully"
            }
        except Exception as e:
            logger.error(f"Failed to refresh session: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to refresh session: {str(e)}")
    
    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user information by email (admin function)"""
        try:
            if not self.admin_supabase:
                return None
            
            response = self.admin_supabase.auth.admin.list_users()
            
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
            logger.error(f"Failed to get user by email: {str(e)}")
            return None
    
    def clear_admin_cache(self, email: Optional[str] = None):
        """Clear admin cache for specific user or all users"""
        if email:
            self._admin_cache.pop(email, None)
            logger.info(f"Cleared admin cache for {email}")
        else:
            self._admin_cache.clear()
            logger.info("Cleared all admin cache")


# Create global instance
auth_service = AuthService()
