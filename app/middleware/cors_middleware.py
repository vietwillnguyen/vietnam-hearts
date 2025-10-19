"""
CORS Middleware Configuration

Handles Cross-Origin Resource Sharing (CORS) for web frontend integration.
Configures allowed origins, methods, and headers based on environment.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import ENVIRONMENT, API_URL
from app.utils.logging_config import get_logger

logger = get_logger("cors_middleware")


def setup_cors(app: FastAPI) -> None:
    """
    Setup CORS middleware for the application
    
    Configures CORS based on the current environment:
    - Development/Test: Allows all origins for easier development and testing
    - Production: Restricts to specific allowed origins
    """
    
    # Determine allowed origins based on environment
    if ENVIRONMENT in ["development", "test"]:
        allowed_origins = [
            "http://localhost:3000",      # React dev server
            "http://localhost:8080",      # FastAPI dev server
            "http://127.0.0.1:3000",      # Alternative localhost
            "http://127.0.0.1:8080",      # Alternative localhost
            "http://localhost:8080",      # Alternative port
            "http://127.0.0.1:8080",      # Alternative port
            API_URL,
        ]
        logger.info(f"🔓 CORS configured for {ENVIRONMENT} - allowing localhost origins")
    else:
        # Production origins - restrict to your actual domains
        allowed_origins = [
            API_URL,                            # Your API URL
        ]
        logger.info("🔒 CORS configured for production - restricting to allowed origins")
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=[
            "GET",
            "POST", 
            "PUT",
            "DELETE",
            "PATCH",
            "OPTIONS"
        ],
        allow_headers=[
            "Accept",
            "Accept-Language",
            "Content-Language",
            "Content-Type",
            "Authorization",
            "X-Requested-With",
            "X-Auth-Token",
            "X-API-Key",
            "Cache-Control",
            "Pragma",
            "Expires",
        ],
        expose_headers=[
            "Content-Length",
            "Content-Type",
            "X-Request-ID",
            "X-Response-Time",
        ],
        max_age=86400,  # Cache preflight requests for 24 hours
    )
    
    logger.info(f"✅ CORS middleware configured with {len(allowed_origins)} allowed origins")
    
    # Log allowed origins for debugging
    if ENVIRONMENT in ["development", "test"]:
        logger.debug(f"Allowed origins: {allowed_origins}")
