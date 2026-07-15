#!/usr/bin/env python3
"""
Tests for logging configuration: console/file output, Cloud Run JSON
formatting, and database persistence wiring.
"""

import json
import logging

from app.utils.db_log_handler import DatabaseLogHandler
from app.utils.logging_config import (
    CloudRunJSONFormatter,
    get_logger,
    print_log_paths,
    setup_logger,
)


def test_logs_appear_console_and_file():
    """Test that logs appear in both console and file."""
    print_log_paths()
    logger = get_logger("test")

    logger.debug("This is a DEBUG message")
    logger.info("This is an INFO message")
    logger.warning("This is a WARNING message")
    logger.error("This is an ERROR message")


class TestCloudRunJSONFormatter:
    def _record(self, level=logging.WARNING, msg="something %s", args=("happened",)):
        return logging.LogRecord(
            name="api",
            level=level,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_emits_parseable_json_with_severity(self):
        line = CloudRunJSONFormatter().format(self._record())
        payload = json.loads(line)
        assert payload["severity"] == "WARNING"
        assert payload["message"] == "something happened"
        assert payload["logger"] == "api"
        assert "time" in payload

    def test_includes_exception_text(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="api",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        payload = json.loads(CloudRunJSONFormatter().format(record))
        assert "ValueError: boom" in payload["message"]


class TestDatabasePersistenceWiring:
    def test_db_handler_attached_by_default(self, monkeypatch):
        monkeypatch.setenv("PERSIST_LOGS_TO_DB", "true")
        logger = setup_logger("test-db-wiring-on")
        db_handlers = [h for h in logger.handlers if isinstance(h, DatabaseLogHandler)]
        assert len(db_handlers) == 1

    def test_db_handler_attached_only_once(self, monkeypatch):
        monkeypatch.setenv("PERSIST_LOGS_TO_DB", "true")
        setup_logger("test-db-wiring-once")
        logger = setup_logger("test-db-wiring-once")
        db_handlers = [h for h in logger.handlers if isinstance(h, DatabaseLogHandler)]
        assert len(db_handlers) == 1

    def test_db_handler_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("PERSIST_LOGS_TO_DB", "false")
        logger = setup_logger("test-db-wiring-off")
        assert not any(isinstance(h, DatabaseLogHandler) for h in logger.handlers)


if __name__ == "__main__":
    test_logs_appear_console_and_file()
