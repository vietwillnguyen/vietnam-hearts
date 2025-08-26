"""
Logging Middleware

Provides comprehensive request/response logging for monitoring and debugging.
Logs request details, response status, timing, and any errors that occur.
"""

import time
import json
from typing import Callable, Dict, Any
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.utils.logging_config import get_logger

logger = get_logger("logging_middleware")


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for comprehensive request/response logging
    
    This middleware logs:
    1. Request details (method, path, headers, body)
    2. Response status and timing
    3. Error details if they occur
    4. Performance metrics
    """
    
    def __init__(self, app, log_request_body: bool = True, log_response_body: bool = False):
        super().__init__(app)
        self.log_request_body = log_request_body
        self.log_response_body = log_response_body
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process the request through logging middleware
        
        Args:
            request: FastAPI request object
            call_next: Next middleware or endpoint handler
            
        Returns:
            Response from the next handler
        """
        start_time = time.time()
        
        # Generate unique request ID
        request_id = self._generate_request_id()
        request.state.request_id = request_id
        
        # Log request details
        await self._log_request(request, request_id)
        
        try:
            # Process the request
            response = await call_next(request)
            
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            
            # Log response details
            duration = time.time() - start_time
            await self._log_response(request, response, duration, request_id)
            
            return response
            
        except Exception as e:
            # Log error details
            duration = time.time() - start_time
            await self._log_error(request, e, duration, request_id)
            raise
    
    async def _log_request(self, request: Request, request_id: str) -> None:
        """
        Log incoming request details
        
        Args:
            request: FastAPI request object
            request_id: Unique request identifier
        """
        # Extract request details
        method = request.method
        path = request.url.path
        query_params = dict(request.query_params)
        headers = dict(request.headers)
        
        # Remove sensitive headers
        sensitive_headers = ['authorization', 'cookie', 'x-auth-token']
        for header in sensitive_headers:
            headers.pop(header.lower(), None)
        
        # Log basic request info
        logger.info(
            f"📥 Request started | ID: {request_id} | {method} {path}",
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,
                "query_params": query_params,
                "headers": headers,
                "client_ip": self._get_client_ip(request)
            }
        )
        
        # Log request body if enabled and available
        if self.log_request_body and method in ['POST', 'PUT', 'PATCH']:
            try:
                body = await self._get_request_body(request)
                if body:
                    logger.debug(
                        f"Request body for {request_id}",
                        extra={
                            "request_id": request_id,
                            "body": body
                        }
                    )
            except Exception as e:
                logger.warning(f"Could not read request body for {request_id}: {e}")
    
    async def _log_response(self, request: Request, response: Response, duration: float, request_id: str) -> None:
        """
        Log response details
        
        Args:
            request: FastAPI request object
            response: Response object
            duration: Request processing time
            request_id: Unique request identifier
        """
        method = request.method
        path = request.url.path
        status_code = response.status_code
        
        # Determine log level based on status code
        if status_code >= 500:
            log_level = logger.error
        elif status_code >= 400:
            log_level = logger.warning
        else:
            log_level = logger.info
        
        # Log response info
        log_level(
            f"📤 Request completed | ID: {request_id} | {method} {path} | {status_code} | {duration:.3f}s",
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration": duration,
                "response_headers": dict(response.headers)
            }
        )
        
        # Log response body if enabled and it's an error
        if self.log_response_body and status_code >= 400:
            try:
                response_body = await self._get_response_body(response)
                if response_body:
                    logger.debug(
                        f"Response body for {request_id}",
                        extra={
                            "request_id": request_id,
                            "response_body": response_body
                        }
                    )
            except Exception as e:
                logger.warning(f"Could not read response body for {request_id}: {e}")
    
    async def _log_error(self, request: Request, error: Exception, duration: float, request_id: str) -> None:
        """
        Log error details
        
        Args:
            request: FastAPI request object
            error: Exception that occurred
            duration: Request processing time
            request_id: Unique request identifier
        """
        method = request.method
        path = request.url.path
        
        logger.error(
            f"❌ Request failed | ID: {request_id} | {method} {path} | {duration:.3f}s | Error: {str(error)}",
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,
                "duration": duration,
                "error": str(error),
                "error_type": type(error).__name__
            },
            exc_info=True
        )
    
    def _generate_request_id(self) -> str:
        """
        Generate a unique request identifier
        
        Returns:
            Unique request ID string
        """
        import uuid
        return str(uuid.uuid4())[:8]
    
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
    
    async def _get_request_body(self, request: Request) -> Dict[str, Any]:
        """
        Extract request body content
        
        Args:
            request: FastAPI request object
            
        Returns:
            Request body as dictionary
        """
        try:
            # Try to get JSON body
            if request.headers.get("content-type") == "application/json":
                body = await request.json()
                return body
            
            # Try to get form data
            if request.headers.get("content-type") == "application/x-www-form-urlencoded":
                form_data = await request.form()
                return dict(form_data)
            
            # Try to get raw body
            body = await request.body()
            if body:
                return {"raw_body": body.decode()}
            
            return {}
            
        except Exception:
            return {"error": "Could not read request body"}
    
    async def _get_response_body(self, response: Response) -> Dict[str, Any]:
        """
        Extract response body content
        
        Args:
            response: Response object
            
        Returns:
            Response body as dictionary
        """
        try:
            # For streaming responses, we can't easily get the body
            if hasattr(response, 'body'):
                return {"body": response.body.decode() if isinstance(response.body, bytes) else str(response.body)}
            
            return {"note": "Response body not available for streaming response"}
            
        except Exception:
            return {"error": "Could not read response body"}
