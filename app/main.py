"""
Vietnam Hearts Class Scheduler API

RESTful API service for managing volunteer scheduling and communications.
API documentation available at /docs and /redoc
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .utils.logging_config import get_logger, get_log_file_path, print_log_paths
from .database import create_tables, get_db
from .config import (
    APPLICATION_VERSION,
    API_URL,
    ENVIRONMENT,
    DATABASE_URL,
)
from app.utils.config_helper import ConfigHelper
import os
from app.routers.admin import admin_router
from app.routers.public import public_router
from app.routers.auth import router as auth_router
from app.routers.settings import router as settings_router

# Configure logging
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    # Startup
    try:
        logger.info(f"🚀 Starting API server")
        logger.info("-" * 50)
        logger.info(f"- ENVIRONMENT={ENVIRONMENT}")
        logger.info(f"- TESTING={os.getenv('TESTING')}")
        logger.info(f"- API_URL={API_URL}")
        logger.info(f"- DATABASE_URL={DATABASE_URL}")
        logger.info(f"- LOGS_DIR={get_log_file_path()}")
        logger.info("-" * 50)

        # Log the actual log file paths
        logger.info("📁 Log file locations:")
        print_log_paths()
        if os.getenv("SEPARATE_LOG_FILES", "false").lower() == "true":
            logger.info("  Separate log files enabled:")
            for component in ["app", "api", "database", "scheduler"]:
                logger.info(f"    {component}: {get_log_file_path(component)}")
        else:
            logger.info("  Unified logging enabled:")
            logger.info(f"    All logs: {get_log_file_path()}")
        logger.info("-" * 50)

        logger.info(
            "📖 API Documentation will be available at: %s/docs", API_URL
        )
        logger.info(
            "🔍 Health check available at: %s/public/health", API_URL
        )
        logger.info("⏹️  Press Ctrl+C to stop the server")

        # Validate Google Sheets configuration
        from app.config import validate_config
        validate_config()
        logger.info("Configuration validated successfully")

        # Initialize database
        create_tables()
        logger.info("✅ Database initialized")

        # Now we can safely access database settings
        try:
            db = next(get_db())
            logger.info(f"- DRY_RUN={ConfigHelper.get_dry_run(db)}")
            logger.info(f"- DRY_RUN_EMAIL_RECIPIENT={ConfigHelper.get_dry_run_email_recipient(db)}")
        except Exception as e:
            logger.warning(f"Could not read dry run settings from database: {e}")
            logger.info("- DRY_RUN=Unknown (database not accessible)")
            logger.info("- DRY_RUN_EMAIL_RECIPIENT=Unknown (database not accessible)")

        # Start API server
        logger.info("✅ API server started successfully")

    except Exception as e:
        logger.error(f"Failed to start application: {str(e)}", exc_info=True)
        raise
    
    # Yield control to FastAPI
    yield
    
    # Shutdown
    logger.info("Shutting down API server...")


# Initialize FastAPI app with lifespan
app = FastAPI(
    title="Vietnam Hearts Scheduler API",
    description="RESTful API for volunteer management and automated scheduling",
    version=APPLICATION_VERSION,
    docs_url=None if os.getenv("ENVIRONMENT") == "production" else "/docs",
    redoc_url=None if os.getenv("ENVIRONMENT") == "production" else "/redoc",
    lifespan=lifespan,
)

# Mount static files
app.mount("/public", StaticFiles(directory="public"), name="public")
logger.info("Static files mounted at /public")

# Routers
app.include_router(auth_router)
logger.info("Authentication endpoints enabled.")

app.include_router(admin_router)
app.include_router(public_router)
app.include_router(settings_router)

# Root route redirects to home page
@app.get("/")
async def root():
    """Redirect root to home page"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=302)
