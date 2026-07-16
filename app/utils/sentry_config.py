"""
Sentry error tracking setup.

Initialization is a no-op when SENTRY_DSN is unset or TESTING=true, so this
is safe to call in every environment (local dev, CI, production, tests).
"""

import os

import sentry_sdk

from app.config import (
    APPLICATION_VERSION,
    ENVIRONMENT,
    SENTRY_DSN,
    SENTRY_TRACES_SAMPLE_RATE,
)
from app.utils.logging_config import get_logger

logger = get_logger("sentry")


def init_sentry() -> None:
    """Initialize Sentry error tracking if SENTRY_DSN is configured and not under test."""
    if not SENTRY_DSN:
        logger.info("SENTRY_DSN not set, Sentry error tracking disabled")
        return

    if os.getenv("TESTING", "").lower() == "true":
        logger.info("TESTING=true, Sentry error tracking disabled")
        return

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=ENVIRONMENT,
        release=APPLICATION_VERSION,
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
        # Attaches request IP, headers, and any explicitly-set user info to
        # every event. Deliberate choice for easier "which request/user hit
        # this" debugging - means that data is sent to Sentry on every error.
        send_default_pii=True,
    )
    logger.info(f"Sentry error tracking initialized for environment={ENVIRONMENT}")
