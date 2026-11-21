"""Capturing log output from loggers that deliberately do not propagate.

``app/utils/logging_config.py`` sets ``propagate = False`` on every logger it
builds, so that a line is not emitted once per ancestor. pytest's ``caplog``
installs its handler on the **root** logger, which means a test written as

    with caplog.at_level("WARNING"):
        ...
    assert "something" in caplog.text

captures nothing at all from an app logger, and - far worse for a test whose job
is to prove an address never reaches a log line - passes vacuously.

``attached_caplog`` fixes that by hanging ``caplog``'s own handler on the named
loggers for the duration of the block, so ``caplog.records`` and ``caplog.text``
work as they read.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

# Every logger the email bot path writes through. Named explicitly rather than
# discovered, so a new module that starts logging has to be added here and the
# privacy test's coverage is a deliberate list rather than an accident.
EMAIL_BOT_LOGGERS = (
    "email_bot_pipeline",
    "email_bot_delivery",
    "email_bot_settings",
    "email_bot_summaries",
    "email_bot_factory",
    "gmail_transport",
    "gmail_adapter",
    "conversation_service",
    "notifier",
    "triage_jev",
    "triage_litellm",
    "triage_shadow",
    "api",
)


@contextmanager
def attached_caplog(
    caplog, *names: str, level: int | str = logging.DEBUG
) -> Iterator[None]:
    """Attach ``caplog``'s handler to ``names`` (default: the email bot loggers)."""
    targets = [logging.getLogger(name) for name in (names or EMAIL_BOT_LOGGERS)]
    previous = [(logger, logger.level) for logger in targets]

    numeric = logging.getLevelName(level) if isinstance(level, str) else level
    caplog.set_level(numeric)
    for logger in targets:
        logger.addHandler(caplog.handler)
        logger.setLevel(numeric)
    try:
        yield
    finally:
        for logger, original in previous:
            logger.removeHandler(caplog.handler)
            logger.setLevel(original)
