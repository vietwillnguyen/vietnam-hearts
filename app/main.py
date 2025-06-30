"""
Vietnam Hearts Class Scheduler API

RESTful API service for managing volunteer scheduling and communications.
API documentation available at /docs and /redoc
"""

from fastapi import FastAPI
from .utils.logging_config import get_app_logger
from .database import init_database
from .config import (
    API_URL,
    DRY_RUN,
    ENVIRONMENT,
    PORT,
)
import os
from .routers.admin import admin_router  # Keep import but control usage
from .routers.api import api_router
from .routers.public import public_router
from app.routers.oauth import oauth_router

# Configure logging
logger = get_app_logger()

# Initialize FastAPI app
app = FastAPI(
    title="Vietnam Hearts Scheduler API",
    description="RESTful API for volunteer management and automated scheduling",
    version="1.1.2",
    docs_url=None if os.getenv("ENVIRONMENT") == "production" else "/docs",
    redoc_url=None if os.getenv("ENVIRONMENT") == "production" else "/redoc",
)

# Routers
app.include_router(oauth_router)

# Conditionally include admin router
if ENVIRONMENT == "development":
    app.include_router(admin_router)
    logger.info("Admin endpoints enabled")

app.include_router(api_router)
app.include_router(public_router)


@app.on_event("startup")
async def startup_event():
    """Initialize application dependencies and background tasks"""
    try:
        logger.info(
            f"🚀 Starting API server (ENVIRONMENT={ENVIRONMENT}, DRY_RUN={DRY_RUN}, API_URL={API_URL}).."
        )
        logger.info(
            "📖 API Documentation will be available at: %s/docs", API_URL
        )
        logger.info(
            "🔍 Health check available at: %s/public/health", API_URL
        )
        logger.info("⏹️  Press Ctrl+C to stop the server")
        logger.info("-" * 50)

        # Validate Google Sheets configuration
        from app.config import validate_config

        try:
            validate_config()
            logger.info("Configuration validated successfully")
        except Exception as e:
            logger.error(f"Configuration validation failed: {str(e)}")
            # Don't fail startup in development, but log the error
            if os.getenv("ENVIRONMENT", "development") == "production":
                raise

        # First initialize database schema
        init_database()
        logger.info("✅ API server started successfully")

    except Exception as e:
        logger.error(f"Failed to start application: {str(e)}", exc_info=True)
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    logger.info("Shutting down API server...")
