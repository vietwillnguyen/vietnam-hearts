"""
Vietnam Hearts Class Scheduler API

RESTful API service for managing volunteer scheduling and communications.
API documentation available at /docs and /redoc
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import make_url

from app.routers.admin import admin_router
from app.routers.auth import router as auth_router
from app.routers.public import public_router
from app.routers.settings import router as settings_router
from app.routers.webhooks import webhooks_router
from app.utils.config_helper import ConfigHelper

from .config import (
    API_URL,
    APPLICATION_VERSION,
    DATABASE_URL,
    ENVIRONMENT,
    FACEBOOK_APP_SECRET,
    FACEBOOK_VERIFY_TOKEN,
)
from .database import get_db, init_db
from .middleware import setup_middleware
from .utils.logging_config import get_log_file_path, get_logger, print_log_paths
from .utils.sentry_config import init_sentry

# Configure logging
logger = get_logger("main")

# Error tracking (no-op if SENTRY_DSN is unset)
init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    # Startup
    try:
        logger.info("🚀 Starting API server")
        logger.info("-" * 50)
        logger.info(f"- ENVIRONMENT={ENVIRONMENT}")
        logger.info(f"- TESTING={os.getenv('TESTING')}")
        logger.info(f"- API_URL={API_URL}")
        # Rendered by SQLAlchemy with the password hidden, and redacted again
        # by the logging filter: the raw URL carries the database password and
        # used to reach Cloud Logging on every cold start.
        logger.info(
            f"- DATABASE_URL={make_url(DATABASE_URL).render_as_string(hide_password=True)}"
        )
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

        logger.info("📖 API Documentation will be available at: %s/docs", API_URL)
        logger.info("🔍 Health check available at: %s/health", API_URL)
        logger.info("⏹️  Press Ctrl+C to stop the server")

        # Validate Google Sheets configuration
        from app.config import validate_config

        validate_config()
        logger.info("Configuration validated successfully")

        if not FACEBOOK_APP_SECRET:
            logger.warning(
                "FACEBOOK_APP_SECRET is not set - every Meta webhook delivery will "
                "be rejected with 403 and Meta will disable the subscription"
            )

        if not FACEBOOK_VERIFY_TOKEN:
            logger.warning(
                "FACEBOOK_VERIFY_TOKEN is not set - the Meta subscription handshake "
                "will be refused with 403 and the webhook can never be verified"
            )

        # Initialize database
        init_db()
        logger.info("✅ Database initialized")

        # After init_db, because whether the inbox bot could run is partly a
        # database setting. Raises, and so stops startup, only in production.
        check_conversation_storage_key()

        # Now we can safely access database settings
        try:
            db = next(get_db())
            logger.info(f"- DRY_RUN={ConfigHelper.get_dry_run(db)}")
            logger.info(
                "- DRY_RUN_EMAIL_RECIPIENT="
                f"{'set' if ConfigHelper.get_dry_run_email_recipient(db) else 'unset'}"
            )
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


def check_conversation_storage_key() -> str:
    """Refuse to start when the inbox bot would have no key to store mail with.

    The rule itself is ``field_crypto.check_storage_key_at_startup``; this reads
    the three inputs it needs. Only the key's status is logged, never the key.
    """
    from app import config
    from app.database import SessionLocal
    from app.services.email_bot.settings import EmailBotSettings
    from app.utils.field_crypto import KEYS_SETTING, check_storage_key_at_startup

    mode: str | None = None
    try:
        db = SessionLocal()
        try:
            mode = EmailBotSettings.load(db).mode
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "Could not read EMAIL_BOT_MODE at startup: %s", type(exc).__name__
        )

    status = check_storage_key_at_startup(
        environment=config.ENVIRONMENT,
        bot_enabled=config.EMAIL_BOT_ENABLED,
        mode=mode,
        raw_keys=os.environ.get(KEYS_SETTING),
    )
    if status == "ok":
        logger.info("- conversation encryption key: ok")
    else:
        logger.warning(
            "- conversation encryption key: %s. %s is not set or not usable, so "
            "the inbox bot will refuse to poll until it is.",
            status,
            KEYS_SETTING,
        )
    return status


# Initialize FastAPI app with lifespan
app = FastAPI(
    title="Vietnam Hearts Scheduler API",
    description="RESTful API for volunteer management and automated scheduling",
    version=APPLICATION_VERSION,
    docs_url=None if os.getenv("ENVIRONMENT") == "production" else "/docs",
    redoc_url=None if os.getenv("ENVIRONMENT") == "production" else "/redoc",
    lifespan=lifespan,
)

# Setup all middleware
setup_middleware(app)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
logger.info("Static files mounted at /static")

# Routers
app.include_router(auth_router)
logger.info("Authentication endpoints enabled.")
app.include_router(admin_router)
logger.info("Admin endpoints enabled.")
app.include_router(public_router)
logger.info("Public endpoints enabled.")
app.include_router(webhooks_router)
app.include_router(settings_router)
logger.info("Settings endpoints enabled.")
# app.include_router(public_bot_router)
# logger.info("Public bot endpoints enabled.")
# app.include_router(bot_admin_router)
# logger.info("Admin bot endpoints enabled.")
