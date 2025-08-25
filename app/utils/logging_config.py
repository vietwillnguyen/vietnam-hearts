import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Environment toggle
SEPARATE_LOG_FILES = os.getenv("SEPARATE_LOG_FILES", "false").lower() == "true"

# Log directory and format
LOGS_DIR = Path("app/logs")  # Logs are written to: ./logs/ (relative to project root)
LOGS_DIR.mkdir(exist_ok=True)

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 10 * 1024 * 1024  # 10MB
BACKUP_COUNT = 5

# Shared formatter
formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)


def setup_logger(name: str, log_file: str | None = None, level=logging.INFO):
    """
    Set up a logger with optional file output.
    If log_file is None, only outputs to stdout (used for unified logging).
    """
    logger = logging.getLogger(name)

    # Clear existing handlers to avoid duplicates
    logger.handlers.clear()
    
    logger.setLevel(level)
    logger.propagate = False  # Prevent duplicate logs from parent loggers

    # Add rotating file handler only if a log file is specified
    if log_file:
        file_handler = RotatingFileHandler(
            LOGS_DIR / log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Always add console handler for stdout output
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# === Logger Factories ===

def get_logger(component: str):
    """
    Get a logger for a specific component.
    Uses shared file if SEPARATE_LOG_FILES is False.
    """
    if SEPARATE_LOG_FILES:
        return setup_logger(component, f"{component}.log")
    else:
        # Use the component name for the logger, but write to app.log
        return setup_logger(component, "app.log")


# === Shorthand Getters ===

get_app_logger = lambda: get_logger("app")
get_api_logger = lambda: get_logger("api")
get_database_logger = lambda: get_logger("database")
get_scheduler_logger = lambda: get_logger("scheduler")


def get_log_file_path(component: str = "app") -> str:
    """
    Get the absolute file path where logs for a component are written.
    
    Args:
        component: The component name (e.g., 'app', 'api', 'database')
        
    Returns:
        Absolute path to the log file
    """
    if SEPARATE_LOG_FILES:
        log_file = f"{component}.log"
    else:
        log_file = "app.log"
    
    return str(LOGS_DIR.absolute() / log_file)


def print_log_paths():
    """Print the paths where logs are being written."""
    print(f"Logs directory: {LOGS_DIR.absolute()}")
    if SEPARATE_LOG_FILES:
        print("Separate log files enabled:")
        for component in ["app", "api", "database", "scheduler"]:
            print(f"  {component}: {get_log_file_path(component)}")
    else:
        print("Unified logging enabled:")
        print(f"  All logs: {get_log_file_path()}")


# Example usage:
# logger = get_app_logger()
# logger.info("Application started")
# logger.error("An error occurred", exc_info=True)
