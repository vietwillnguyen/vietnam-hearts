"""
Sentry error tracking setup.

Initialization is a no-op when SENTRY_DSN is unset or TESTING=true, so this
is safe to call in every environment (local dev, CI, production, tests).
"""

import os
from typing import Any

import sentry_sdk

from app.config import (
    APPLICATION_VERSION,
    ENVIRONMENT,
    SENTRY_DSN,
    SENTRY_TRACES_SAMPLE_RATE,
)
from app.utils.logging_config import get_logger, redact_credentials

logger = get_logger("sentry")


def _redact(value: Any) -> Any:
    """Apply the log redaction to every string in a Sentry payload."""
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def scrub_event(event: dict, hint: dict | None = None) -> dict:
    """before_send: mask personal data and credentials before an event leaves.

    Covers exception messages, the log message, breadcrumbs (Sentry records
    log lines as breadcrumbs before any logging filter has run), request data,
    extras and captured frame variables - the whole event, since a volunteer
    address can surface in any of them.
    """
    return _redact(event)


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
        # No request IP, cookies or user info on events; scrub_event masks
        # whatever personal data still rides in messages and breadcrumbs.
        send_default_pii=False,
        before_send=scrub_event,
    )
    logger.info(f"Sentry error tracking initialized for environment={ENVIRONMENT}")
