import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Create logs directory if it doesn't exist
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

# Log format
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# File sizes and backup counts
MAX_BYTES = 10 * 1024 * 1024  # 10MB
BACKUP_COUNT = 5


def setup_logger(name: str, log_file: str, level=logging.INFO):
    """
    Set up a logger with the specified name and log file.

    Args:
        name (str): Name of the logger
        log_file (str): Name of the log file
        level (int): Logging level

    Returns:
        logging.Logger: Configured logger instance
    """
    # Create logger
    logger = logging.getLogger(name)

    # If logger already has handlers, return it
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Create rotating file handler
    file_handler = RotatingFileHandler(
        LOGS_DIR / log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    file_handler.setFormatter(formatter)

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# Create specific loggers for different components
def get_app_logger():
    """Get the main application logger"""
    return setup_logger("app", "app.log")


def get_database_logger():
    """Get the database operations logger"""
    return setup_logger("database", "database.log")


def get_api_logger():
    """Get the API endpoints logger"""
    return setup_logger("api", "api.log")


def get_scheduler_logger():
    """Get the scheduler operations logger"""
    return setup_logger("scheduler", "scheduler.log")


# Example usage:
# logger = get_app_logger()
# logger.info("Application started")
# logger.error("An error occurred", exc_info=True)
