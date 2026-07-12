"""
Sentry error tracking setup.

Initialization is a no-op when SENTRY_DSN is unset, so this is safe to call
in every environment (local dev, CI, production).
"""

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
    """Initialize Sentry error tracking if SENTRY_DSN is configured."""
    if not SENTRY_DSN:
        logger.info("SENTRY_DSN not set, Sentry error tracking disabled")
        return

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=ENVIRONMENT,
        release=APPLICATION_VERSION,
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
    )
    logger.info(f"Sentry error tracking initialized for environment={ENVIRONMENT}")
