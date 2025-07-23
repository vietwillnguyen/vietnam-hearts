"""
Dynamic admin user management service
Replaces hardcoded ADMIN_EMAILS with database-driven approach
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import logging
import asyncio
from supabase import create_client

logger = logging.getLogger(__name__)


@dataclass
class AdminUser:
    id: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None


class AdminUserService:
    def __init__(self, supabase_client):
        self.supabase = supabase_client
        # Create a single admin client for all operations
        from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
            self.admin_supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        else:
            self.admin_supabase = None
            logger.warning("Supabase admin credentials not configured")
    
    async def is_admin(self, email: str) -> bool:
        """Check if email belongs to an active admin user"""
        try:
            if not self.admin_supabase:
                logger.error("Admin Supabase client not initialized")
                return False
            
            # Add timeout to prevent hanging
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").select("email").eq(
                        "email", email.lower()
                    ).eq("is_active", True).execute()
                ),
                timeout=10.0  # 10 second timeout
            )

            logger.info(f"Admin check result: {result.data}")
            
            is_admin = len(result.data) > 0
            
            if is_admin:
                # Update last login timestamp
                await self._update_last_login(email.lower())
                logger.info(f"Admin access granted for: {email}")
            else:
                logger.warning(f"Access denied for non-admin user: {email}")
            
            return is_admin
        except asyncio.TimeoutError:
            logger.error(f"Timeout checking admin status for {email}")
            return False
        except Exception as e:
            logger.error(f"Failed to check admin status for {email}: {e}")
            return False
    
    async def _update_last_login(self, email: str):
        """Update last login timestamp for admin user"""
        try:
            if not self.admin_supabase:
                return
                
            await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.rpc("update_admin_last_login", {"user_email": email}).execute()
                ),
                timeout=5.0  # 5 second timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"Timeout updating last login for {email}")
        except Exception as e:
            logger.error(f"Failed to update last login for {email}: {e}")
    
    async def get_admin_users(self, requester_email: str) -> List[AdminUser]:
        """Get list of active admin users (only for admins)"""
        try:
            # First check if requester is admin
            if not await self.is_admin(requester_email):
                raise PermissionError("Only admins can view admin users")
            
            if not self.admin_supabase:
                logger.error("Admin Supabase client not initialized")
                return []
            
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").select("*").eq("is_active", True).order("created_at", desc=True).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            admin_users = []
            for row in result.data:
                admin_users.append(AdminUser(
                    id=row["id"],
                    email=row["email"],
                    role=row["role"],
                    is_active=row["is_active"],
                    created_at=datetime.fromisoformat(row["created_at"].replace('Z', '+00:00')),
                    last_login=datetime.fromisoformat(row["last_login"].replace('Z', '+00:00')) if row["last_login"] else None
                ))
            
            return admin_users
        except asyncio.TimeoutError:
            logger.error("Timeout getting admin users")
            return []
        except Exception as e:
            logger.error(f"Failed to get admin users: {e}")
            return []
    
    async def get_all_admin_users(self, requester_email: str) -> List[AdminUser]:
        """Get list of all admin users including inactive ones (only for super admins)"""
        try:
            # First check if requester is admin
            if not await self.is_admin(requester_email):
                raise PermissionError("Only admins can view admin users")
            
            if not self.admin_supabase:
                logger.error("Admin Supabase client not initialized")
                return []
            
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").select("*").order("created_at", desc=True).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            admin_users = []
            for row in result.data:
                admin_users.append(AdminUser(
                    id=row["id"],
                    email=row["email"],
                    role=row["role"],
                    is_active=row["is_active"],
                    created_at=datetime.fromisoformat(row["created_at"].replace('Z', '+00:00')),
                    last_login=datetime.fromisoformat(row["last_login"].replace('Z', '+00:00')) if row["last_login"] else None
                ))
            
            return admin_users
        except asyncio.TimeoutError:
            logger.error("Timeout getting all admin users")
            return []
        except Exception as e:
            logger.error(f"Failed to get all admin users: {e}")
            return []
    
    async def add_admin_user(self, email: str, role: str = "admin", added_by_email: str = None) -> bool:
        """Add new admin user (only super admins can do this)"""
        try:
            if not self.admin_supabase:
                logger.error("Admin Supabase client not initialized")
                return False
            
            # Check if requester is super admin
            if added_by_email:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        lambda: self.admin_supabase.table("admin_users").select("role").eq(
                            "email", added_by_email.lower()
                        ).eq("is_active", True).execute()
                    ),
                    timeout=10.0  # 10 second timeout
                )
                
                if not result.data or result.data[0]["role"] != "super_admin":
                    raise PermissionError("Only super admins can add admin users")
            
            # Check if user already exists
            existing = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").select("email").eq(
                        "email", email.lower()
                    ).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            if existing.data:
                logger.warning(f"Admin user {email} already exists")
                return False
            
            # Add new admin user
            await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").insert({
                        "email": email.lower(),
                        "role": role,
                        "is_active": True
                    }).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            logger.info(f"Added new admin user: {email} with role: {role}")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout adding admin user {email}")
            return False
        except Exception as e:
            logger.error(f"Failed to add admin user {email}: {e}")
            return False
    
    async def remove_admin_user(self, email: str, removed_by_email: str) -> bool:
        """Remove admin user (deactivate)"""
        try:
            if not self.admin_supabase:
                logger.error("Admin Supabase client not initialized")
                return False
            
            # Check if requester is super admin
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").select("role").eq(
                        "email", removed_by_email.lower()
                    ).eq("is_active", True).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            if not result.data or result.data[0]["role"] != "super_admin":
                raise PermissionError("Only super admins can remove admin users")
            
            # Deactivate user instead of deleting
            await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").update({
                        "is_active": False,
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("email", email.lower()).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            logger.info(f"Deactivated admin user: {email}")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout removing admin user {email}")
            return False
        except Exception as e:
            logger.error(f"Failed to remove admin user {email}: {e}")
            return False
    
    async def delete_admin_user(self, email: str, deleted_by_email: str) -> bool:
        """Permanently delete admin user from database (only super admins)"""
        try:
            if not self.admin_supabase:
                logger.error("Admin Supabase client not initialized")
                return False
            
            # Check if requester is super admin
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").select("role").eq(
                        "email", deleted_by_email.lower()
                    ).eq("is_active", True).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            if not result.data or result.data[0]["role"] != "super_admin":
                raise PermissionError("Only super admins can delete admin users")
            
            # Permanently delete user
            await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").delete().eq("email", email.lower()).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            logger.info(f"Permanently deleted admin user: {email}")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout deleting admin user {email}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete admin user {email}: {e}")
            return False
    
    async def update_admin_role(self, email: str, new_role: str, updated_by_email: str) -> bool:
        """Update admin user role"""
        try:
            if not self.admin_supabase:
                logger.error("Admin Supabase client not initialized")
                return False
            
            # Check if requester is super admin
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").select("role").eq(
                        "email", updated_by_email.lower()
                    ).eq("is_active", True).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            if not result.data or result.data[0]["role"] != "super_admin":
                raise PermissionError("Only super admins can update admin roles")
            
            # Update role
            await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.admin_supabase.table("admin_users").update({
                        "role": new_role,
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("email", email.lower()).execute()
                ),
                timeout=10.0  # 10 second timeout
            )
            
            logger.info(f"Updated admin user {email} role to: {new_role}")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout updating admin role for {email}")
            return False
        except Exception as e:
            logger.error(f"Failed to update admin role for {email}: {e}")
            return False