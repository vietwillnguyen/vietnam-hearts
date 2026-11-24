"""The volunteer inbox as an ``inbox-core`` client: its configuration, read once.

``inbox_client.toml`` is the part of the bot that is policy rather than code -
the organisation the classifier is told about, the eleven categories, which of
them may ever be answered, where the rest escalate, and the two languages. The
core validates it on load and derives every tier from it.

The confidence threshold is the one value in it that the dashboard can change
between runs, so ``client_config`` lays ``TRIAGE_CONFIDENCE_THRESHOLD`` over
the file each time a run starts, the same way every other setting is read
fresh rather than remembered.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from inbox_core import ClientConfig, load_config

from app.services.email_bot.settings import EmailBotSettings
from app.utils.logging_config import get_logger

CONFIG_PATH = Path(__file__).with_name("inbox_client.toml")

# inbox-core attaches only a NullHandler, which would swallow its warnings (a
# failed shadow classifier, a failed retrieval) without reaching any sink.
# Configured here like every app logger, so its lines - ids, decisions and
# exception type names - go to stdout, the log file and system_logs, through
# the same credential redaction.
get_logger("inbox_core")


@lru_cache(maxsize=1)
def base_config() -> ClientConfig:
    """The validated file. Raises ``ConfigError`` listing every problem in it."""
    return load_config(CONFIG_PATH)


def client_config(settings: EmailBotSettings | None = None) -> ClientConfig:
    """The configuration one run works under: the file, at this run's threshold."""
    config = base_config()
    if settings is None:
        return config
    triage = config.triage.with_threshold(settings.triage_confidence_threshold)
    return ClientConfig(
        organisation=config.organisation,
        triage=triage,
        templates=config.templates,
        delivery=config.delivery,
    )
