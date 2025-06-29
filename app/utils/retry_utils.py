"""
Retry utilities for handling transient failures in external API calls.
Provides exponential backoff with jitter for Google Sheets API and other external services.
"""

import logging
import ssl
import time
import random
from typing import Any, Callable, Type, Union, List
from functools import wraps
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    after_log,
    RetryError
)
from tenacity.wait import wait_exponential_jitter

logger = logging.getLogger(__name__)

# Define retryable exceptions for Google Sheets API
RETRYABLE_EXCEPTIONS = (
    ssl.SSLEOFError,
    ssl.SSLError,
    ConnectionError,
    TimeoutError,
    OSError,  # Network errors
)

# Define non-retryable exceptions
NON_RETRYABLE_EXCEPTIONS = (
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
)


def create_retry_decorator(
    max_attempts: int = 3,
    base_wait: float = 1.0,
    max_wait: float = 10.0,
    retry_exceptions: Union[Type[Exception], tuple] = RETRYABLE_EXCEPTIONS,
    jitter: bool = True
) -> Callable:
    """
    Create a retry decorator with exponential backoff and jitter.
    
    Args:
        max_attempts: Maximum number of retry attempts
        base_wait: Base wait time in seconds
        max_wait: Maximum wait time in seconds
        retry_exceptions: Exceptions that should trigger a retry
        jitter: Whether to add jitter to the wait time
    
    Returns:
        Decorator function
    """
    wait_strategy = wait_exponential_jitter(
        initial=base_wait,
        max=max_wait,
        exp_base=2,
        jitter=random.uniform(0, 0.1) if jitter else 0
    )
    
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_strategy,
        retry=retry_if_exception_type(retry_exceptions),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO),
        reraise=True
    )


def retry_google_sheets_api(
    max_attempts: int = 3,
    base_wait: float = 2.0,
    max_wait: float = 15.0
) -> Callable:
    """
    Retry decorator specifically for Google Sheets API calls.
    Handles SSL errors and connection issues with appropriate backoff.
    """
    return create_retry_decorator(
        max_attempts=max_attempts,
        base_wait=base_wait,
        max_wait=max_wait,
        retry_exceptions=RETRYABLE_EXCEPTIONS,
        jitter=True
    )


def safe_api_call(
    func: Callable,
    *args,
    max_attempts: int = 3,
    context: str = "API call",
    **kwargs
) -> Any:
    """
    Safely execute an API call with retry logic and graceful error handling.
    
    Args:
        func: Function to execute
        *args: Arguments for the function
        max_attempts: Maximum number of retry attempts
        context: Context string for logging
        **kwargs: Keyword arguments for the function
    
    Returns:
        Function result or None if all attempts fail
    
    Raises:
        Exception: If the function fails after all retry attempts
    """
    retry_decorator = retry_google_sheets_api(max_attempts=max_attempts)
    
    @retry_decorator
    def _execute_with_retry():
        return func(*args, **kwargs)
    
    try:
        logger.info(f"Executing {context} with retry logic")
        return _execute_with_retry()
    except RetryError as e:
        logger.error(f"All retry attempts failed for {context}: {str(e)}")
        raise e.last_attempt._exception
    except Exception as e:
        logger.error(f"Unexpected error in {context}: {str(e)}")
        raise


def log_ssl_error(error: Exception, context: str, attempt: int = 1) -> None:
    """
    Log SSL errors with structured information for monitoring.
    
    Args:
        error: The SSL error that occurred
        context: Context where the error occurred
        attempt: Current attempt number
    """
    logger.error(
        "SSL Error occurred",
        extra={
            "error_type": "ssl_eof_error",
            "error_message": str(error),
            "context": context,
            "attempt": attempt,
            "timestamp": time.time(),
        }
    )


def is_retryable_error(error: Exception) -> bool:
    """
    Check if an error is retryable.
    
    Args:
        error: The exception to check
    
    Returns:
        True if the error is retryable, False otherwise
    """
    return isinstance(error, RETRYABLE_EXCEPTIONS) and not isinstance(error, NON_RETRYABLE_EXCEPTIONS)


def get_retry_delay(attempt: int, base_delay: float = 1.0, max_delay: float = 30.0) -> float:
    """
    Calculate retry delay with exponential backoff and jitter.
    
    Args:
        attempt: Current attempt number (1-based)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
    
    Returns:
        Delay in seconds
    """
    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter 