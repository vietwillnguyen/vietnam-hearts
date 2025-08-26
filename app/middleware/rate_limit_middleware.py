"""
Rate Limiting Middleware

Protects the API from abuse by limiting request frequency per client.
Provides different rate limits for different types of endpoints.
"""

import time
from typing import Callable, Dict, Any, Optional
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from app.utils.logging_config import get_logger

logger = get_logger("rate_limit_middleware")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware for rate limiting API requests
    
    This middleware:
    1. Tracks request frequency per client
    2. Applies different limits for different endpoint types
    3. Provides rate limit headers in responses
    4. Logs rate limit violations
    """
    
    def __init__(self, app):
        super().__init__(app)
        # In-memory storage for rate limiting (consider Redis for production)
        self.request_counts: Dict[str, Dict[str, Any]] = {}
        
        # Rate limit configurations
        self.rate_limits = {
            "default": {
                "requests": 100,      # 100 requests
                "window": 3600        # per hour
            },
            "auth": {
                "requests": 10,       # 10 auth attempts
                "window": 3600        # per hour
            },
            "admin": {
                "requests": 1000,     # 1000 admin requests
                "window": 3600        # per hour
            },
            "public": {
                "requests": 500,      # 500 public requests
                "window": 3600        # per hour
            },
            "bot": {
                "requests": 200,      # 200 bot requests
                "window": 3600        # per hour
            }
        }
        
        # Cleanup old entries every hour
        self.last_cleanup = time.time()
        self.cleanup_interval = 3600  # 1 hour
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process the request through rate limiting middleware
        
        Args:
            request: FastAPI request object
            call_next: Next middleware or endpoint handler
            
        Returns:
            Response from the next handler or rate limit error
        """
        # Clean up old entries periodically
        await self._cleanup_old_entries()
        
        # Get client identifier
        client_id = self._get_client_id(request)
        
        # Determine rate limit category
        rate_limit_category = self._get_rate_limit_category(request.url.path)
        
        # Check rate limit
        rate_limit_check = await self._check_rate_limit(
            client_id, 
            rate_limit_category
        )
        
        if not rate_limit_check["allowed"]:
            return await self._create_rate_limit_response(
                request, 
                rate_limit_check, 
                client_id
            )
        
        # Process the request
        response = await call_next(request)
        
        # Add rate limit headers
        await self._add_rate_limit_headers(
            response, 
            rate_limit_check, 
            rate_limit_category
        )
        
        return response
    
    def _get_client_id(self, request: Request) -> str:
        """
        Generate a unique client identifier
        
        Args:
            request: FastAPI request object
            
        Returns:
            Client identifier string
        """
        # Try to get user ID from authentication
        user_id = getattr(request.state, 'user', {}).get('id')
        if user_id:
            return f"user_{user_id}"
        
        # Fall back to IP address
        client_ip = self._get_client_ip(request)
        return f"ip_{client_ip}"
    
    def _get_client_ip(self, request: Request) -> str:
        """
        Get the client IP address from request headers
        
        Args:
            request: FastAPI request object
            
        Returns:
            Client IP address
        """
        # Check for forwarded headers (common in proxy setups)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        
        # Check for real IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        
        # Fall back to client host
        return request.client.host if request.client else "unknown"
    
    def _get_rate_limit_category(self, path: str) -> str:
        """
        Determine rate limit category based on endpoint path
        
        Args:
            path: Request path
            
        Returns:
            Rate limit category
        """
        if path.startswith("/auth"):
            return "auth"
        elif path.startswith("/admin"):
            return "admin"
        elif path.startswith("/public"):
            return "public"
        elif path.startswith("/bot"):
            return "bot"
        else:
            return "default"
    
    async def _check_rate_limit(self, client_id: str, category: str) -> Dict[str, Any]:
        """
        Check if the client is within rate limits
        
        Args:
            client_id: Client identifier
            category: Rate limit category
            
        Returns:
            Dictionary with rate limit check results
        """
        current_time = time.time()
        limits = self.rate_limits[category]
        
        # Initialize client tracking if needed
        if client_id not in self.request_counts:
            self.request_counts[client_id] = {}
        
        if category not in self.request_counts[client_id]:
            self.request_counts[client_id][category] = {
                "count": 0,
                "reset_time": current_time + limits["window"]
            }
        
        client_data = self.request_counts[client_id][category]
        
        # Check if window has reset
        if current_time >= client_data["reset_time"]:
            client_data["count"] = 0
            client_data["reset_time"] = current_time + limits["window"]
        
        # Check if limit exceeded
        if client_data["count"] >= limits["requests"]:
            return {
                "allowed": False,
                "limit": limits["requests"],
                "remaining": 0,
                "reset_time": client_data["reset_time"],
                "retry_after": int(client_data["reset_time"] - current_time)
            }
        
        # Increment count and allow request
        client_data["count"] += 1
        
        return {
            "allowed": True,
            "limit": limits["requests"],
            "remaining": limits["requests"] - client_data["count"],
            "reset_time": client_data["reset_time"]
        }
    
    async def _create_rate_limit_response(
        self, 
        request: Request, 
        rate_limit_check: Dict[str, Any], 
        client_id: str
    ) -> JSONResponse:
        """
        Create rate limit exceeded response
        
        Args:
            request: FastAPI request object
            rate_limit_check: Rate limit check results
            client_id: Client identifier
            
        Returns:
            JSONResponse with rate limit error
        """
        # Log rate limit violation
        logger.warning(
            f"Rate limit exceeded for {client_id} on {request.url.path}",
            extra={
                "client_id": client_id,
                "path": request.url.path,
                "method": request.method,
                "limit": rate_limit_check["limit"],
                "retry_after": rate_limit_check["retry_after"]
            }
        )
        
        # Create error response
        error_response = {
            "error": {
                "type": "rate_limit_exceeded",
                "message": "Too many requests. Please try again later.",
                "details": {
                    "limit": rate_limit_check["limit"],
                    "retry_after": rate_limit_check["retry_after"],
                    "reset_time": rate_limit_check["reset_time"]
                }
            }
        }
        
        response = JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=error_response
        )
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(rate_limit_check["limit"])
        response.headers["X-RateLimit-Remaining"] = "0"
        response.headers["X-RateLimit-Reset"] = str(int(rate_limit_check["reset_time"]))
        response.headers["Retry-After"] = str(rate_limit_check["retry_after"])
        
        return response
    
    async def _add_rate_limit_headers(
        self, 
        response: Response, 
        rate_limit_check: Dict[str, Any], 
        category: str
    ) -> None:
        """
        Add rate limit headers to successful responses
        
        Args:
            response: Response object
            rate_limit_check: Rate limit check results
            category: Rate limit category
        """
        if hasattr(response, 'headers'):
            response.headers["X-RateLimit-Limit"] = str(rate_limit_check["limit"])
            response.headers["X-RateLimit-Remaining"] = str(rate_limit_check["remaining"])
            response.headers["X-RateLimit-Reset"] = str(int(rate_limit_check["reset_time"]))
            response.headers["X-RateLimit-Category"] = category
    
    async def _cleanup_old_entries(self) -> None:
        """
        Clean up old rate limit entries to prevent memory leaks
        """
        current_time = time.time()
        
        # Only cleanup every hour
        if current_time - self.last_cleanup < self.cleanup_interval:
            return
        
        self.last_cleanup = current_time
        
        # Remove expired entries
        expired_clients = []
        for client_id, client_data in self.request_counts.items():
            expired_categories = []
            for category, category_data in client_data.items():
                if current_time >= category_data["reset_time"]:
                    expired_categories.append(category)
            
            # Remove expired categories
            for category in expired_categories:
                del client_data[category]
            
            # Remove client if no categories left
            if not client_data:
                expired_clients.append(client_id)
        
        # Remove expired clients
        for client_id in expired_clients:
            del self.request_counts[client_id]
        
        if expired_clients:
            logger.debug(f"Cleaned up {len(expired_clients)} expired rate limit entries")
