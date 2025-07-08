"""
Supabase Authentication Service

This service handles:
1. User authentication with Supabase
2. JWT token verification
3. User role management
4. Session handling
"""

import os
import jwt
import time
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from supabase import create_client, Client
from app.models import User, UserRole
from app.config import SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
from app.utils.logging_config import get_api_logger

logger = get_api_logger()


class SupabaseAuthService:
    """Service for handling Supabase authentication"""
    
    def __init__(self):
        """Initialize Supabase client"""
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            raise ValueError("Supabase URL and ANON_KEY must be configured")
        
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        self.service_role_key = SUPABASE_SERVICE_ROLE_KEY
        
    def verify_jwt_token(self, token: str) -> Dict[str, Any]:
        """
        Verify a Supabase JWT token
        
        Args:
            token: JWT token from Supabase
            
        Returns:
            dict: Decoded token payload
            
        Raises:
            HTTPException: If token is invalid
        """
        try:
            # Use Supabase client to verify the token
            # This is the recommended approach for server-side token verification
            if not self.service_role_key:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Supabase service role key not configured"
                )
            
            # Create a service role client for token verification
            service_client = create_client(SUPABASE_URL, self.service_role_key)
            
            # Get user from token using Supabase admin API
            user_response = service_client.auth.get_user(token)
            
            if user_response.user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication token"
                )
            
            # Return user data as token payload
            return {
                "sub": user_response.user.id,
                "email": user_response.user.email,
                "exp": user_response.user.created_at.timestamp() + 3600,  # 1 hour expiry
                "iat": user_response.user.created_at.timestamp()
            }
            
        except Exception as e:
            logger.error(f"Error verifying JWT token: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed"
            )
    
    def get_user_by_supabase_id(self, db: Session, supabase_user_id: str) -> Optional[User]:
        """
        Get user by Supabase user ID
        
        Args:
            db: Database session
            supabase_user_id: Supabase user UUID
            
        Returns:
            User: User object if found, None otherwise
        """
        return db.query(User).filter(User.supabase_user_id == supabase_user_id).first()
    
    def get_user_by_email(self, db: Session, email: str) -> Optional[User]:
        """
        Get user by email
        
        Args:
            db: Database session
            email: User email
            
        Returns:
            User: User object if found, None otherwise
        """
        return db.query(User).filter(User.email == email).first()
    
    def create_user_from_supabase(self, db: Session, supabase_user_id: str, email: str, role: UserRole = UserRole.VOLUNTEER) -> User:
        """
        Create a new user from Supabase authentication
        
        Args:
            db: Database session
            supabase_user_id: Supabase user UUID
            email: User email
            role: User role (default: VOLUNTEER)
            
        Returns:
            User: Created user object
        """
        # Check if user already exists
        existing_user = self.get_user_by_supabase_id(db, supabase_user_id)
        if existing_user:
            return existing_user
        
        # Check if user exists by email (for linking to existing volunteers)
        existing_user_by_email = self.get_user_by_email(db, email)
        if existing_user_by_email:
            # Update the existing user with the real Supabase ID
            existing_user_by_email.supabase_user_id = supabase_user_id
            db.commit()
            db.refresh(existing_user_by_email)
            logger.info(f"Updated existing user {email} with Supabase ID")
            return existing_user_by_email
        
        # Create new user
        user = User(
            supabase_user_id=supabase_user_id,
            email=email,
            role=role,
            is_active=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        
        logger.info(f"Created new user: {email} with role {role.value}")
        return user
    
    def require_authentication(self, token: str, db: Session) -> User:
        """
        Require valid authentication and return user
        
        Args:
            token: JWT token from request
            db: Database session
            
        Returns:
            User: Authenticated user
            
        Raises:
            HTTPException: If authentication fails
        """
        # Verify the JWT token
        token_data = self.verify_jwt_token(token)
        
        # Extract user ID from token
        supabase_user_id = token_data.get("sub")
        if not supabase_user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing user ID"
            )
        
        # Get user from database
        user = self.get_user_by_supabase_id(db, supabase_user_id)
        if not user:
            # User doesn't exist in our database, create them
            email = token_data.get("email")
            if not email:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token missing email"
                )
            
            user = self.create_user_from_supabase(db, supabase_user_id, email)
        
        # Check if user is active
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is deactivated"
            )
        
        logger.info(f"User authenticated: {user.email} (role: {user.role.value})")
        return user
    
    def require_admin_role(self, user: User) -> User:
        """
        Require admin role for access
        
        Args:
            user: Authenticated user
            
        Returns:
            User: User if admin role
            
        Raises:
            HTTPException: If user is not admin
        """
        if user.role != UserRole.ADMIN:
            logger.warning(f"Access denied for non-admin user: {user.email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        
        return user
    
    def get_user_info(self, user: User) -> Dict[str, Any]:
        """
        Get user information for API responses
        
        Args:
            user: User object
            
        Returns:
            dict: User information
        """
        return {
            "id": user.id,
            "email": user.email,
            "role": user.role.value,
            "is_active": user.is_active,
            "volunteer_id": user.volunteer_id,
            "created_at": user.created_at.isoformat() if user.created_at else None
        }


# Global instance
supabase_auth = SupabaseAuthService() 