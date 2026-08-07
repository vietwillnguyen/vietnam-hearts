"""BotService must refuse rather than answer without grounding."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.bot_service import (
    BotService,
    GenerationUnavailable,
    NoRelevantContext,
)
from app.services.knowledge_service import EmbeddingsUnavailable


def _bot(similarity_search) -> BotService:
    bot = BotService.__new__(BotService)
    bot.knowledge_service = MagicMock()
    bot.knowledge_service.similarity_search = similarity_search
    bot.document_service = MagicMock()
    bot.supabase = MagicMock()
    return bot


def _grounded_bot(gemini_client) -> BotService:
    bot = _bot(AsyncMock(return_value=[{"content": "some text", "similarity": 0.8}]))
    bot.knowledge_service.gemini_client = gemini_client
    return bot


class TestChatRefusesInsteadOfGuessing:
    @pytest.mark.asyncio
    async def test_propagates_embeddings_unavailable(self):
        bot = _bot(AsyncMock(side_effect=EmbeddingsUnavailable("down")))
        with pytest.raises(EmbeddingsUnavailable):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    async def test_raises_when_no_relevant_chunks_are_found(self):
        bot = _bot(AsyncMock(return_value=[]))
        with pytest.raises(NoRelevantContext):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    async def test_raises_when_the_gemini_client_is_missing(self):
        bot = _grounded_bot(None)
        with pytest.raises(GenerationUnavailable):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    async def test_raises_when_the_generation_call_fails(self):
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED"
        )
        bot = _grounded_bot(client)
        with pytest.raises(GenerationUnavailable):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("generated", [None, "", "   \n  "])
    async def test_raises_when_the_generated_text_is_empty(self, generated):
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(text=generated)
        bot = _grounded_bot(client)
        with pytest.raises(GenerationUnavailable):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    async def test_never_returns_a_keyword_matched_canned_answer(self):
        # The historical failure: an unreachable knowledge base still produced
        # "you don't need a formal teaching certificate" as a confident claim.
        bot = _bot(AsyncMock(side_effect=EmbeddingsUnavailable("down")))
        with pytest.raises(EmbeddingsUnavailable):
            await bot.chat("do I need a teaching certificate")

    def test_the_canned_response_helpers_are_gone(self):
        assert not hasattr(BotService, "_generate_simple_response")
        assert not hasattr(BotService, "_generate_fallback_response")


class TestFailureModesStaySeparable:
    # Phase 1 dispatches on exception type, so a generation outage, a
    # retrieval outage and a knowledge base gap have to stay catchable
    # independently. Making any of these a subclass of another would silently
    # re-merge them at every existing call site.
    def test_no_failure_mode_subclasses_another(self):
        modes = (EmbeddingsUnavailable, NoRelevantContext, GenerationUnavailable)
        for raised in modes:
            for caught in modes:
                if raised is not caught:
                    assert not issubclass(raised, caught)


class TestChatSucceeds:
    @pytest.mark.asyncio
    async def test_returns_the_top_similarity_as_confidence(self):
        chunks = [
            {"content": "a", "similarity": 0.42, "source_document_id": "faq"},
            {"content": "b", "similarity": 0.81, "source_document_id": "faq"},
        ]
        bot = _bot(AsyncMock(return_value=chunks))
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text="  Grounded answer.  "
        )
        bot.knowledge_service.gemini_client = client

        result = await bot.chat("how do I volunteer")

        assert result["response"] == "Grounded answer."
        assert result["confidence"] == 0.81
        assert result["context_used"] == 2
        assert result["sources"] == ["faq", "faq"]
