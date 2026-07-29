"""Process-lifetime service providers.

BotService construction reaches out to Gemini to verify the embedding model, so
building one per request wastes a call from a 15 RPM free tier on every inbound
message. lru_cache makes construction happen once.
"""

from functools import lru_cache

from app.services.bot_service import BotService


@lru_cache(maxsize=1)
def get_bot_service() -> BotService:
    """Return the shared BotService, constructing it on first use."""
    return BotService()


def reset_bot_service() -> None:
    """Drop the cached instance. Tests use this to isolate from each other."""
    get_bot_service.cache_clear()
