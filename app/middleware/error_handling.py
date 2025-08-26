"""
Error Handling Middleware

Provides consistent error handling and response formatting across the application.
Catches unhandled exceptions and returns standardized error responses.
"""

import traceback
from typing import Callable, Dict, Any
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from app.utils.logging_config import get_logger

logger = get_logger("error_handling")


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for handling unhandled exceptions and providing consistent error responses
    
    This middleware:
    1. Catches all unhandled exceptions
    2. Logs detailed error information
    3. Returns consistent error response format
    4. Handles different types of errors appropriately
    """
    
    def __init__(self, app, include_traceback: bool = False):
        super().__init__(app)
        self.include_traceback = include_traceback
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process the request through error handling middleware
        
        Args:
            request: FastAPI request object
            call_next: Next middleware or endpoint handler
            
        Returns:
            Response from the next handler or error response
        """
        try:
            # Process the request
            response = await call_next(request)
            return response
            
        except HTTPException as e:
            # Re-raise HTTP exceptions (they're already properly formatted)
            raise
            
        except Exception as e:
            # Handle all other unhandled exceptions
            return await self._handle_unexpected_error(request, e)
    
    async def _handle_unexpected_error(self, request: Request, error: Exception) -> JSONResponse:
        """
        Handle unexpected errors and return consistent error response
        
        Args:
            request: FastAPI request object
            error: Exception that occurred
            
        Returns:
            JSONResponse with error details
        """
        # Get request details for logging
        method = request.method
        path = request.url.path
        request_id = getattr(request.state, 'request_id', 'unknown')
        
        # Log the error with full details
        logger.error(
            f"❌ Unhandled exception | ID: {request_id} | {method} {path}",
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,
                "error": str(error),
                "error_type": type(error).__name__,
                "traceback": traceback.format_exc() if self.include_traceback else None
            },
            exc_info=True
        )
        
        # Determine appropriate status code
        status_code = self._get_status_code_for_error(error)
        
        # Build error response
        error_response = {
            "error": {
                "type": "internal_server_error",
                "message": "An unexpected error occurred",
                "request_id": request_id,
                "path": path,
                "method": method
            }
        }
        
        # Include additional details in development
        if self.include_traceback:
            error_response["error"]["details"] = {
                "error_type": type(error).__name__,
                "error_message": str(error),
                "traceback": traceback.format_exc()
            }
        
        return JSONResponse(
            status_code=status_code,
            content=error_response
        )
    
    def _get_status_code_for_error(self, error: Exception) -> int:
        """
        Determine appropriate HTTP status code for different error types
        
        Args:
            error: Exception that occurred
            
        Returns:
            HTTP status code
        """
        error_type = type(error).__name__
        
        # Map common error types to status codes
        error_status_map = {
            "ValidationError": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "ValueError": status.HTTP_400_BAD_REQUEST,
            "TypeError": status.HTTP_400_BAD_REQUEST,
            "KeyError": status.HTTP_400_BAD_REQUEST,
            "AttributeError": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "ImportError": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "ModuleNotFoundError": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "ConnectionError": status.HTTP_503_SERVICE_UNAVAILABLE,
            "TimeoutError": status.HTTP_504_GATEWAY_TIMEOUT,
            "PermissionError": status.HTTP_403_FORBIDDEN,
            "FileNotFoundError": status.HTTP_404_NOT_FOUND,
            "OSError": status.HTTP_500_INTERNAL_SERVER_ERROR,
        }
        
        return error_status_map.get(error_type, status.HTTP_500_INTERNAL_SERVER_ERROR)


def create_error_response(
    error_type: str,
    message: str,
    status_code: int = 500,
    details: Dict[str, Any] = None,
    request_id: str = None
) -> JSONResponse:
    """
    Create a standardized error response
    
    Args:
        error_type: Type of error (e.g., 'validation_error', 'not_found')
        message: Human-readable error message
        status_code: HTTP status code
        details: Additional error details
        request_id: Request identifier for tracking
        
    Returns:
        JSONResponse with standardized error format
    """
    error_response = {
        "error": {
            "type": error_type,
            "message": message,
            "status_code": status_code
        }
    }
    
    if details:
        error_response["error"]["details"] = details
    
    if request_id:
        error_response["error"]["request_id"] = request_id
    
    return JSONResponse(
        status_code=status_code,
        content=error_response
    )


def handle_validation_error(error: Exception, request: Request = None) -> JSONResponse:
    """
    Handle validation errors with consistent formatting
    
    Args:
        error: Validation error exception
        request: FastAPI request object (optional)
        
    Returns:
        JSONResponse with validation error details
    """
    request_id = getattr(request.state, 'request_id', None) if request else None
    
    # Extract validation details
    if hasattr(error, 'errors'):
        details = {
            "validation_errors": error.errors(),
            "model": getattr(error, 'model', None)
        }
    else:
        details = {"error_message": str(error)}
    
    return create_error_response(
        error_type="validation_error",
        message="Data validation failed",
        status_code=422,
        details=details,
        request_id=request_id
    )
