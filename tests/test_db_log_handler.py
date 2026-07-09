"""Tests for the buffered database log handler."""

import logging
from datetime import datetime, timedelta

import pytest

from app.models import SystemLog
from app.utils.db_log_handler import DatabaseLogHandler


@pytest.fixture
def session_factory(test_engine, test_db):
    """Session factory bound to the test engine (tables come from test_db)."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=test_engine)


def make_record(name="app", level=logging.INFO, msg="hello"):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )


class TestDatabaseLogHandler:
    def test_flush_persists_records(self, session_factory, test_db):
        handler = DatabaseLogHandler(session_factory=session_factory)
        handler.emit(make_record(msg="first"))
        handler.emit(make_record(name="api", level=logging.ERROR, msg="second"))
        handler.flush()

        rows = test_db.query(SystemLog).order_by(SystemLog.id).all()
        assert [(r.logger_name, r.level, r.message) for r in rows] == [
            ("app", "INFO", "first"),
            ("api", "ERROR", "second"),
        ]

    def test_buffer_flushes_when_full(self, session_factory, test_db):
        handler = DatabaseLogHandler(session_factory=session_factory, buffer_size=3)
        for i in range(3):
            handler.emit(make_record(msg=f"msg-{i}"))

        assert test_db.query(SystemLog).count() == 3

    def test_skips_sqlalchemy_records(self, session_factory, test_db):
        handler = DatabaseLogHandler(session_factory=session_factory)
        handler.emit(make_record(name="sqlalchemy.engine.Engine", msg="SELECT 1"))
        handler.flush()

        assert test_db.query(SystemLog).count() == 0

    def test_never_raises_when_db_broken(self):
        def broken_factory():
            raise RuntimeError("db down")

        handler = DatabaseLogHandler(session_factory=broken_factory)
        handler.emit(make_record(msg="doomed"))
        handler.flush()  # must not raise; buffer is dropped

    def test_retention_deletes_old_rows(self, session_factory, test_db):
        old = SystemLog(
            created_at=datetime.utcnow() - timedelta(days=60),
            level="INFO", logger_name="app", message="ancient",
        )
        test_db.add(old)
        test_db.commit()

        handler = DatabaseLogHandler(session_factory=session_factory, retention_days=30)
        handler.emit(make_record(msg="fresh"))
        handler.flush()

        messages = [r.message for r in test_db.query(SystemLog).all()]
        assert "fresh" in messages
        assert "ancient" not in messages
