"""
Timeout decorator utility

Shared decorator for adding timeout protection to async endpoint handlers.
"""

import asyncio
import functools

from fastapi import HTTPException

from app.utils.logging_config import get_logger

logger = get_logger("timeout")


def timeout_handler(timeout_seconds: float = 30.0):
    """Decorator to add timeout protection to async functions"""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs), timeout=timeout_seconds
                )
            except asyncio.TimeoutError as e:
                logger.error(
                    f"Function {func.__name__} timed out after {timeout_seconds} seconds"
                )
                raise HTTPException(
                    status_code=504,
                    detail=f"Operation timed out after {timeout_seconds} seconds",
                ) from e

        return wrapper

    return decorator
