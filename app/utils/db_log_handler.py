"""
Buffered logging handler that persists log records to the database.

Cloud Run containers have an ephemeral filesystem, so file logs vanish on
every restart. This handler batches records and bulk-inserts them into the
system_logs table so the admin dashboard can show persistent log history.

Design constraints:
- Must NEVER raise into application code paths (logging must be safe).
- Must not recurse: sqlalchemy loggers are skipped and a reentrancy guard
  protects the flush path.
- Batches writes (buffer_size records or flush_interval seconds) so a log
  line does not cost a synchronous DB round-trip.
"""

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

_SKIPPED_LOGGER_PREFIXES = ("sqlalchemy",)


class DatabaseLogHandler(logging.Handler):
    def __init__(
        self,
        session_factory: Callable | None = None,
        buffer_size: int = 20,
        flush_interval: float = 5.0,
        retention_days: int | None = None,
    ):
        super().__init__()
        self._session_factory = session_factory
        self._buffer = []
        self._buffer_lock = threading.Lock()
        self._flushing = False
        self._last_flush = time.monotonic()
        self._retention_done = False
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        self.retention_days = (
            retention_days
            if retention_days is not None
            else int(os.getenv("LOG_RETENTION_DAYS", "30"))
        )

    def _get_session_factory(self) -> Callable | None:
        if self._session_factory is None:
            # Lazy import to avoid a circular import at module load time
            # (database.py loggers are created before SessionLocal exists).
            try:
                from app.database import SessionLocal

                self._session_factory = SessionLocal
            except Exception:
                return None
        return self._session_factory

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(_SKIPPED_LOGGER_PREFIXES):
                return
            entry = {
                "created_at": datetime.fromtimestamp(
                    record.created, tz=timezone.utc
                ).replace(tzinfo=None),
                "level": record.levelname,
                "logger_name": record.name,
                "message": self.format(record)
                if self.formatter
                else record.getMessage(),
            }
            with self._buffer_lock:
                self._buffer.append(entry)
                should_flush = (
                    len(self._buffer) >= self.buffer_size
                    or (time.monotonic() - self._last_flush) >= self.flush_interval
                )
            if should_flush:
                self.flush()
        except Exception:
            # Logging must never break the application.
            pass

    def flush(self) -> None:
        try:
            if self._flushing:
                return
            with self._buffer_lock:
                if not self._buffer:
                    return
                pending, self._buffer = self._buffer, []
                self._last_flush = time.monotonic()

            self._flushing = True
            try:
                factory = self._get_session_factory()
                if factory is None:
                    return
                from app.models import SystemLog

                session = factory()
                try:
                    session.bulk_insert_mappings(SystemLog, pending)
                    if not self._retention_done:
                        cutoff = datetime.utcnow() - timedelta(days=self.retention_days)
                        session.query(SystemLog).filter(
                            SystemLog.created_at < cutoff
                        ).delete(synchronize_session=False)
                        self._retention_done = True
                    session.commit()
                finally:
                    session.close()
            finally:
                self._flushing = False
        except Exception:
            # Drop the batch rather than raise; logs also go to stdout.
            pass

    def close(self) -> None:
        try:
            self.flush()
        finally:
            super().close()
