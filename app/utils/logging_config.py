import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# Environment configuration with validation
def get_env_bool(key: str, default: str = "false") -> bool:
    """Get boolean environment variable with validation."""
    value = os.getenv(key, default).lower()
    if value not in ("true", "false", "1", "0"):
        # Log warning and use default
        print(f"Warning: Invalid value '{os.getenv(key)}' for {key}, using default '{default}'")
        return default.lower() == "true"
    return value in ("true", "1")

def get_env_int(key: str, default: int, min_val: int = 1, max_val: int = 100) -> int:
    """Get integer environment variable with validation."""
    try:
        value = int(os.getenv(key, default))
        if min_val <= value <= max_val:
            return value
        else:
            print(f"Warning: {key}={value} out of range [{min_val}, {max_val}], using default {default}")
            return default
    except (ValueError, TypeError):
        print(f"Warning: Invalid value '{os.getenv(key)}' for {key}, using default {default}")
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
    print(f"Warning: Cannot create logs directory {LOGS_DIR.absolute()}. Logs will only go to stdout.")
    LOGS_DIR = None
except OSError as e:
    print(f"Warning: Error creating logs directory: {e}. Logs will only go to stdout.")
    LOGS_DIR = None

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = get_env_int("LOG_MAX_BYTES", 10 * 1024 * 1024, 1024 * 1024, 100 * 1024 * 1024)  # 10MB default
BACKUP_COUNT = get_env_int("LOG_BACKUP_COUNT", 5, 1, 20)

# Shared formatter
formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)


def setup_logger(name: str, log_file: Optional[str] = None, level: int = logging.INFO) -> logging.Logger:
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
    
    # Check if logger already has handlers to avoid unnecessary work
    if logger.hasHandlers():
        return logger

    logger.setLevel(level)
    logger.propagate = False  # Prevent duplicate logs from parent loggers

    # Add rotating file handler only if logs directory exists and log file is specified
    if LOGS_DIR and log_file:
        try:
            file_handler = RotatingFileHandler(
                LOGS_DIR / log_file, 
                maxBytes=MAX_BYTES, 
                backupCount=BACKUP_COUNT
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as e:
            print(f"Warning: Cannot create log file {log_file}: {e}")

    # Always add console handler for stdout output
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

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


def get_log_file_path(component: str = "app") -> Optional[str]:
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
        "date_format": DATE_FORMAT
    }