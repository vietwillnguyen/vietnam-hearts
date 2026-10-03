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
    redact_credentials,
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


class TestRedactCredentials:
    """Credentials must never survive into a log line.

    Production used to log the raw DATABASE_URL at startup, which put the
    Supabase Postgres password into Cloud Logging on every cold start.
    """

    def test_replaces_the_password_in_a_database_url(self):
        redacted = redact_credentials(
            "postgresql://postgres.abc123:h0rse-b4ttery@"
            "aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
        )
        assert "h0rse-b4ttery" not in redacted
        assert redacted == (
            "postgresql://postgres.abc123:***@"
            "aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
        )

    def test_keeps_a_url_without_credentials_intact(self):
        url = "sqlite:///file::memory:?cache=shared&uri=true"
        assert redact_credentials(url) == url

    def test_keeps_a_url_with_only_a_username_intact(self):
        url = "postgresql://postgres@localhost:5432/postgres"
        assert redact_credentials(url) == url

    def test_redacts_a_token_query_parameter(self):
        redacted = redact_credentials(
            "https://graph.facebook.com/v23.0/me/messages?access_token=EAAG-live-token"
        )
        assert "EAAG-live-token" not in redacted
        assert redacted.endswith("?access_token=***")

    def test_redacts_a_secret_query_parameter_after_an_ampersand(self):
        redacted = redact_credentials(
            "https://example.test/hook?hub.mode=subscribe&hub.verify_token=s3cret"
        )
        assert "s3cret" not in redacted
        assert "hub.mode=subscribe" in redacted
        assert redacted.endswith("&hub.verify_token=***")

    def test_keeps_a_harmless_query_parameter_intact(self):
        url = "https://example.test/sheet?gid=12345&range=A1"
        assert redact_credentials(url) == url

    def test_redacts_every_credential_on_one_line(self):
        redacted = redact_credentials(
            "primary=postgresql://u1:pw-one@h1/db replica=postgresql://u2:pw-two@h2/db"
        )
        assert "pw-one" not in redacted
        assert "pw-two" not in redacted

    def test_leaves_plain_text_alone(self):
        message = "Configuration validated successfully"
        assert redact_credentials(message) == message

    def test_is_idempotent(self):
        once = redact_credentials("postgresql://u:pw@h/db")
        assert redact_credentials(once) == once


class TestCredentialRedactingFilterGuard:
    """A future log of a credential-bearing URL must be redacted too."""

    def _capture(self, logger):
        captured = []

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        handler = _Capture()
        logger.addHandler(handler)
        return captured, handler

    def test_a_credential_url_is_redacted_before_it_reaches_a_handler(self):
        logger = setup_logger("redaction_guard_direct")
        captured, handler = self._capture(logger)
        try:
            logger.info("connecting to postgresql://app:n3ver-log-me@db.test/main")
        finally:
            logger.removeHandler(handler)

        assert captured, "the record never reached the handler"
        assert "n3ver-log-me" not in captured[0]
        assert "postgresql://app:***@db.test/main" in captured[0]

    def test_redaction_also_covers_lazy_percent_args(self):
        logger = setup_logger("redaction_guard_lazy")
        captured, handler = self._capture(logger)
        try:
            logger.info("dsn=%s", "postgresql://app:n3ver-log-me@db.test/main")
        finally:
            logger.removeHandler(handler)

        assert captured, "the record never reached the handler"
        assert "n3ver-log-me" not in captured[0]
        assert "postgresql://app:***@db.test/main" in captured[0]

    def test_a_clean_message_is_left_untouched(self):
        logger = setup_logger("redaction_guard_clean")
        captured, handler = self._capture(logger)
        try:
            logger.info("Database initialized")
        finally:
            logger.removeHandler(handler)

        assert captured == ["Database initialized"]


class TestStartupBannerRedaction:
    """The startup banner is the path that leaked the password to Cloud Logging."""

    PASSWORD = "n3ver-log-this-pw"
    LEAKY_URL = (
        f"postgresql://postgres.abc123:{PASSWORD}@"
        "aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
    )

    def test_startup_logs_the_database_url_without_its_password(
        self, monkeypatch, test_engine, test_db
    ):
        from unittest.mock import patch

        from fastapi.testclient import TestClient
        from sqlalchemy.orm import sessionmaker

        import app.database as app_db

        monkeypatch.setattr("app.main.DATABASE_URL", self.LEAKY_URL)
        monkeypatch.setattr(app_db, "engine", test_engine)
        monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=test_engine))

        captured = []

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        main_logger = get_logger("main")
        handler = _Capture()
        main_logger.addHandler(handler)
        try:
            with patch("app.config.validate_config"):
                from app.main import app

                app.dependency_overrides[app_db.get_db] = lambda: iter([test_db])
                try:
                    with TestClient(app):
                        pass
                finally:
                    app.dependency_overrides.clear()
        finally:
            main_logger.removeHandler(handler)

        banner = [line for line in captured if "DATABASE_URL=" in line]
        assert banner, f"startup never logged the DATABASE_URL banner: {captured}"
        assert not any(
            self.PASSWORD in line for line in captured
        ), "the DATABASE_URL password reached a log record"
        assert banner[0] == (
            "- DATABASE_URL=postgresql://postgres.abc123:***@"
            "aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
        )
