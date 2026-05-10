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
from app.utils.request_helpers import get_client_ip

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
                "client_ip": get_client_ip(request)
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
    
    async def _get_request_body(self, request: Request) -> Dict[str, Any]:
        """
        Extract request body content.

        Reads the raw body bytes and installs a replay receive so the route
        handler's Request can still read the body (consuming the ASGI receive
        stream once here would otherwise leave the handler with a null body).
        """
        try:
            content_type = request.headers.get("content-type", "")
            body = await request.body()

            if body:
                # Replace _receive so the next handler in the chain can still
                # read the same bytes (Starlette's BaseHTTPMiddleware passes
                # request._receive down to the inner app, so we must replay).
                captured = body

                async def _replay_receive() -> dict:
                    return {"type": "http.request", "body": captured, "more_body": False}

                request._receive = _replay_receive  # type: ignore[attr-defined]

            if "application/json" in content_type:
                try:
                    return json.loads(body) if body else {}
                except Exception:
                    return {"raw_body": body.decode("utf-8", errors="replace")}

            if "application/x-www-form-urlencoded" in content_type:
                from urllib.parse import parse_qs
                parsed = parse_qs(body.decode("utf-8", errors="replace"))
                return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

            return {"raw_body": body.decode("utf-8", errors="replace")} if body else {}

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
