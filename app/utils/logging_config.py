import json
import logging
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.utils.db_log_handler import DatabaseLogHandler


# Environment configuration with validation
def get_env_bool(key: str, default: str = "false") -> bool:
    """Get boolean environment variable with validation."""
    value = os.getenv(key, default).lower()
    if value not in ("true", "false", "1", "0"):
        # Log warning and use default
        print(
            f"Warning: Invalid value '{os.getenv(key)}' for {key}, using default '{default}'"
        )
        return default.lower() == "true"
    return value in ("true", "1")


def get_env_int(key: str, default: int, min_val: int = 1, max_val: int = 100) -> int:
    """Get integer environment variable with validation."""
    try:
        value = int(os.getenv(key, default))
        if min_val <= value <= max_val:
            return value
        else:
            print(
                f"Warning: {key}={value} out of range [{min_val}, {max_val}], using default {default}"
            )
            return default
    except (ValueError, TypeError):
        print(
            f"Warning: Invalid value '{os.getenv(key)}' for {key}, using default {default}"
        )
        return default


# Environment toggles with validation
SEPARATE_LOG_FILES = get_env_bool("SEPARATE_LOG_FILES", "false")
LOG_LEVEL = get_env_int("LOG_LEVEL", 20, 0, 50)  # Default to INFO (20)

# Log directory and format - Fixed path inconsistency
LOGS_DIR = Path("logs")  # Logs are written to: ./logs/ (relative to project root)

# Create logs directory with error handling
try:
    LOGS_DIR.mkdir(exist_ok=True)
except PermissionError:
    print(
        f"Warning: Cannot create logs directory {LOGS_DIR.absolute()}. Logs will only go to stdout."
    )
    LOGS_DIR = None
except OSError as e:
    print(f"Warning: Error creating logs directory: {e}. Logs will only go to stdout.")
    LOGS_DIR = None

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = get_env_int(
    "LOG_MAX_BYTES", 10 * 1024 * 1024, 1024 * 1024, 100 * 1024 * 1024
)  # 10MB default
BACKUP_COUNT = get_env_int("LOG_BACKUP_COUNT", 5, 1, 20)

# One vocabulary for "this name carries a credential", shared with the request
# logging middleware so the two cannot drift apart. Matched as a substring of
# the lowercased name: over-redacting a harmless name costs nothing in a log,
# under-redacting a credential is a leak.
SENSITIVE_PARAM_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "signature",
    "api_key",
    "apikey",
    "key",
    "credential",
    "auth",
)

REDACTED = "***"

# scheme://user:password@host -> the password only. Like SQLAlchemy's URL
# parser, the password runs to the "@", so an unencoded / ? # [ or ] inside it
# is still covered. A URL with no password (scheme://user@host) has no colon
# before the "@" and is left alone.
_URL_CREDENTIALS_RE = re.compile(
    r"(?P<prefix>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/?#\[\]@\s]+:)[^@\s]+(?=@)"
)

# ?access_token=..., &hub.verify_token=... - the value of any query parameter
# whose name looks like a credential.
_SENSITIVE_QUERY_RE = re.compile(
    r"(?P<prefix>[?&][^?&=\s]*(?:"
    + "|".join(SENSITIVE_PARAM_MARKERS)
    + r")[^?&=\s]*=)[^?&\s]+",
    re.IGNORECASE,
)

_BEARER_RE = re.compile(r"(?P<prefix>\bBearer\s+)[^\s'\",]+", re.IGNORECASE)

# 'authorization': '...' or "Authorization": "..." in a dict repr or JSON.
_QUOTED_AUTHORIZATION_RE = re.compile(
    r"(?P<prefix>(?P<kq>['\"])authorization(?P=kq)\s*:\s*(?P<vq>['\"])).*?(?=(?P=vq))",
    re.IGNORECASE,
)

# Authorization: ... as a raw header line, to the end of the line.
_HEADER_AUTHORIZATION_RE = re.compile(
    r"(?P<prefix>\bauthorization:[ \t]*)[^\r\n]+", re.IGNORECASE
)

_REDACTIONS = (
    _URL_CREDENTIALS_RE,
    _SENSITIVE_QUERY_RE,
    _BEARER_RE,
    _QUOTED_AUTHORIZATION_RE,
    _HEADER_AUTHORIZATION_RE,
)


def redact_credentials(text: str) -> str:
    """Strip credentials out of a string bound for a log sink.

    Covers a password embedded in a connection URL, a credential passed as a
    query parameter, a Bearer token, and an Authorization header value.
    Idempotent, so it is safe to apply at a call site and again in the
    logging filter.
    """
    for pattern in _REDACTIONS:
        text = pattern.sub(lambda m: f"{m.group('prefix')}{REDACTED}", text)
    return text


def _redact_args(args):
    """Redact the str values of a record's args, keeping their shape."""
    if isinstance(args, Mapping):
        return {
            k: redact_credentials(v) if isinstance(v, str) else v
            for k, v in args.items()
        }
    if isinstance(args, tuple | list):
        return tuple(redact_credentials(a) if isinstance(a, str) else a for a in args)
    return args


def _is_clean(record: logging.LogRecord) -> bool:
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a msg/args mismatch is not clean
        return False
    return redact_credentials(message) == message


class CredentialRedactingFilter(logging.Filter):
    """Redact credentials from a record before a sink formats it.

    Rewrites the record in place and never drops it, so it changes what a sink
    writes but never which sinks a record reaches. Redacts msg and each str
    arg separately so a formatter that reads record.args itself - uvicorn's
    AccessFormatter unpacks five of them - still gets the shape it expects.
    Only when a credential survives that (it sits in a non-str arg, or spans
    msg and an arg) is the record collapsed to its redacted message with no
    args, as a last resort. Mirrors the db_log_handler rule that logging must
    never raise into application code.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if _is_clean(record):
                return True
            redacted = redact_credentials(record.getMessage())
            if isinstance(record.msg, str):
                record.msg = redact_credentials(record.msg)
            record.args = _redact_args(record.args)
            if not _is_clean(record):
                record.msg = redacted
                record.args = None
        except Exception:  # noqa: BLE001 - logging must never raise
            pass
        return True


# Stateless, so one instance is shared by every logger and handler it guards.
_credential_redacting_filter = CredentialRedactingFilter()


def install_credential_redaction() -> None:
    """Guard every log sink in the process, not just the factory's loggers.

    Modules that use a bare logging.getLogger(__name__) propagate to a root
    logger with no handlers in production, so their records are written to
    stderr - and from there to Cloud Logging - by logging.lastResort. The
    filter therefore goes on lastResort, on the root logger, on the handlers
    already attached to the root or any other logger (uvicorn configures its
    uvicorn.error and uvicorn.access handlers before it imports the app), and,
    through a one-time wrap of Logger.addHandler, on every handler attached
    later. Handler filters run per sink even for propagated records, which
    logger filters do not. Idempotent: addFilter skips an instance already
    present and the wrap is installed at most once.
    """
    if logging.lastResort is not None:
        logging.lastResort.addFilter(_credential_redacting_filter)
    logging.root.addFilter(_credential_redacting_filter)
    loggers = [logging.root] + [
        logger
        for logger in list(logging.Logger.manager.loggerDict.values())
        if isinstance(logger, logging.Logger)
    ]
    for logger in loggers:
        for handler in logger.handlers:
            handler.addFilter(_credential_redacting_filter)

    if getattr(logging.Logger.addHandler, "_redacts_credentials", False):
        return
    original_add_handler = logging.Logger.addHandler

    def add_handler(self: logging.Logger, hdlr: logging.Handler) -> None:
        hdlr.addFilter(_credential_redacting_filter)
        original_add_handler(self, hdlr)

    add_handler._redacts_credentials = True  # type: ignore[attr-defined]
    logging.Logger.addHandler = add_handler  # type: ignore[method-assign]


install_credential_redaction()


# Shared formatter
formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

# Cloud Run sets K_SERVICE; there we emit one JSON object per line so Cloud
# Logging parses severity and the Logs Explorer can filter by level.
IS_CLOUD_RUN = bool(os.getenv("K_SERVICE"))


class CloudRunJSONFormatter(logging.Formatter):
    """Structured log formatter for Google Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return json.dumps(
            {
                "severity": record.levelname,
                "message": message,
                "logger": record.name,
                "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            },
            ensure_ascii=False,
        )


# Single shared DB handler so all component loggers batch into one buffer.
_db_log_handler: DatabaseLogHandler | None = None


def _persist_logs_enabled() -> bool:
    """Read at setup time (not import time) so tests can toggle via env."""
    return os.getenv("PERSIST_LOGS_TO_DB", "true").lower() in ("true", "1")


def _get_db_log_handler() -> DatabaseLogHandler:
    global _db_log_handler
    if _db_log_handler is None:
        _db_log_handler = DatabaseLogHandler()
    return _db_log_handler


def setup_logger(
    name: str, log_file: str | None = None, level: int = logging.INFO
) -> logging.Logger:
    """
    Set up a logger with optional file output.

    Args:
        name: Logger name
        log_file: Optional log file name (if None, only outputs to stdout)
        level: Logging level

    Returns:
        Configured logger instance

    Raises:
        OSError: If file operations fail
    """
    logger = logging.getLogger(name)

    # Check if logger already has its own handlers to avoid duplicate setup.
    # (hasHandlers() would also match root handlers via propagation and skip
    # configuration entirely when e.g. logging.basicConfig was called.)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False  # Prevent duplicate logs from parent loggers

    # Guard every sink: a credential-bearing URL logged anywhere from here on
    # is redacted before it is formatted or shipped.
    logger.addFilter(_credential_redacting_filter)

    # Add rotating file handler only if logs directory exists and log file is specified
    if LOGS_DIR and log_file:
        try:
            file_handler = RotatingFileHandler(
                LOGS_DIR / log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as e:
            print(f"Warning: Cannot create log file {log_file}: {e}")

    # Always add console handler for stdout output
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(CloudRunJSONFormatter() if IS_CLOUD_RUN else formatter)
    logger.addHandler(console_handler)

    # Persist logs to the database so history survives container restarts
    if _persist_logs_enabled():
        logger.addHandler(_get_db_log_handler())

    return logger


# === Logger Factories ===


def get_logger(component: str) -> logging.Logger:
    """
    Get a logger for a specific component.
    Uses shared file if SEPARATE_LOG_FILES is False.

    Args:
        component: Component name for the logger

    Returns:
        Configured logger instance
    """
    if SEPARATE_LOG_FILES:
        return setup_logger(component, f"{component}.log", LOG_LEVEL)
    else:
        # Use the component name for the logger, but write to app.log
        return setup_logger(component, "app.log", LOG_LEVEL)


# === Shorthand Getters - Replaced lambdas with proper functions ===


def get_app_logger() -> logging.Logger:
    """Get the main application logger."""
    return get_logger("app")


def get_api_logger() -> logging.Logger:
    """Get the API logger."""
    return get_logger("api")


def get_database_logger() -> logging.Logger:
    """Get the database logger."""
    return get_logger("database")


def get_scheduler_logger() -> logging.Logger:
    """Get the scheduler logger."""
    return get_logger("scheduler")


def get_log_file_path(component: str = "app") -> str | None:
    """
    Get the absolute file path where logs for a component are written.

    Args:
        component: The component name (e.g., 'app', 'api', 'database')

    Returns:
        Absolute path to the log file, or None if logs directory doesn't exist
    """
    if not LOGS_DIR:
        return None

    if SEPARATE_LOG_FILES:
        log_file = f"{component}.log"
    else:
        log_file = "app.log"

    return str(LOGS_DIR.absolute() / log_file)


def print_log_paths() -> None:
    """Print the paths where logs are being written."""
    if not LOGS_DIR:
        print("Warning: Logs directory not available. All logs will go to stdout.")
        return

    print(f"Logs directory: {LOGS_DIR.absolute()}")
    if SEPARATE_LOG_FILES:
        print("Separate log files enabled:")
        for component in ["app", "api", "database", "scheduler", "bot"]:
            path = get_log_file_path(component)
            if path:
                print(f"  {component}: {path}")
    else:
        print("Unified logging enabled:")
        path = get_log_file_path()
        if path:
            print(f"  All logs: {path}")


def get_logging_config_summary() -> dict:
    """
    Get a summary of the current logging configuration.

    Returns:
        Dictionary with logging configuration details
    """
    return {
        "separate_log_files": SEPARATE_LOG_FILES,
        "log_level": logging.getLevelName(LOG_LEVEL),
        "logs_directory": str(LOGS_DIR.absolute()) if LOGS_DIR else None,
        "max_bytes": MAX_BYTES,
        "backup_count": BACKUP_COUNT,
        "log_format": LOG_FORMAT,
        "date_format": DATE_FORMAT,
    }
