"""
Middleware Package

Centralized middleware setup for the Vietnam Hearts application.
This package provides logging, CORS, error handling, and rate limiting middleware.

Note: Authentication is handled by FastAPI dependencies (router-level) to avoid
conflicts with the dependency injection system.
"""

from fastapi import FastAPI

from .cors_middleware import setup_cors
from .error_handling import ErrorHandlingMiddleware
from .logging_middleware import LoggingMiddleware
from .rate_limit_middleware import RateLimitMiddleware


def setup_middleware(app: FastAPI) -> None:
    """
    Setup all middleware for the application

    Note: Authentication is handled by FastAPI dependencies, not middleware
    to avoid conflicts with the dependency injection system.

    Order matters. Starlette's add_middleware inserts at index 0, so the last
    one added ends up outermost. The real request-time order is:
    1. Logging (outermost)
    2. Rate limiting
    3. Error handling
    4. CORS (innermost)

    Because ErrorHandlingMiddleware sits inside the other two, anything raised
    inside LoggingMiddleware or RateLimitMiddleware bypasses it entirely and
    surfaces to the client as a bare 500.
    """

    # Setup CORS first (FastAPI built-in middleware) - should be early to handle preflight requests
    setup_cors(app)

    # Add custom middleware classes (excluding auth to avoid conflicts)
    # add_middleware inserts at index 0, so the last added is outermost. The
    # real request-time order is:
    # 1. Logging (outermost)
    # 2. Rate limiting
    # 3. Error handling
    # 4. CORS (already added above, innermost)
    # Anything raised inside LoggingMiddleware or RateLimitMiddleware bypasses
    # ErrorHandlingMiddleware and surfaces as a bare 500.
    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(LoggingMiddleware)

    # Log middleware setup
    from app.utils.logging_config import get_logger

    logger = get_logger("middleware")
    logger.info(
        "✅ All middleware configured successfully (auth handled by dependencies)"
    )
