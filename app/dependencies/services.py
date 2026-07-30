"""Process-lifetime service providers.

BotService construction reaches out to Gemini to verify the embedding model, so
building one per request wastes a call from a 15 RPM free tier on every inbound
message. lru_cache makes construction happen once.
"""

from functools import lru_cache

from app.config import SUPABASE_SECRET_KEY, SUPABASE_URL
from app.services.bot_service import BotService
from app.utils.logging_config import get_api_logger

logger = get_api_logger()


def _build_supabase_client():
    """Return a Supabase client, or None when one cannot be built.

    Without this client KnowledgeService has no vector store, so fail-closed
    retrieval raises on every question and the bot escalates everything.
    Passing None here silently disables the knowledge base, so the None paths
    below log loudly.
    """
    if not (SUPABASE_URL and SUPABASE_SECRET_KEY):
        logger.warning("Supabase credentials missing; knowledge base unavailable")
        return None
    try:
        from supabase import create_client

        client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
        logger.info("Bot service initialized with Supabase client")
        return client
    except Exception as exc:
        logger.error(f"Supabase client init failed; knowledge base unavailable: {exc}")
        return None


@lru_cache(maxsize=1)
def get_bot_service() -> BotService:
    """Return the shared BotService, constructing it on first use."""
    return BotService(_build_supabase_client())


def reset_bot_service() -> None:
    """Drop the cached instance. Tests use this to isolate from each other."""
    get_bot_service.cache_clear()
