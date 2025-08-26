"""
Middleware Package

Centralized middleware setup for the Vietnam Hearts application.
This package provides logging, CORS, error handling, and rate limiting middleware.

Note: Authentication is handled by FastAPI dependencies (router-level) to avoid
conflicts with the dependency injection system.
"""

from fastapi import FastAPI
from .logging_middleware import LoggingMiddleware
from .cors_middleware import setup_cors
from .error_handling import ErrorHandlingMiddleware
from .rate_limit_middleware import RateLimitMiddleware

def setup_middleware(app: FastAPI) -> None:
    """
    Setup all middleware for the application
    
    Note: Authentication is handled by FastAPI dependencies, not middleware
    to avoid conflicts with the dependency injection system.
    
    Order matters - middleware is executed in the order it's added:
    1. Error handling (outermost)
    2. Rate limiting
    3. Logging
    4. CORS (innermost)
    """
    
    # Add custom middleware classes (excluding auth to avoid conflicts)
    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(LoggingMiddleware)
    
    # Setup CORS (FastAPI built-in middleware)
    setup_cors(app)
    
    # Log middleware setup
    from app.utils.logging_config import get_logger
    logger = get_logger("middleware")
    logger.info("✅ All middleware configured successfully (auth handled by dependencies)")
