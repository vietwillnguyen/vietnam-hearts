"""When embeddings are unavailable the service must refuse, not improvise."""

from unittest.mock import MagicMock

import pytest

from app.services.knowledge_service import EmbeddingsUnavailable, KnowledgeService


def _service_without_embeddings() -> KnowledgeService:
    service = KnowledgeService.__new__(KnowledgeService)
    service.supabase = None
    service.gemini_client = None
    service.embedding_model = None
    return service


def _service_with_supabase_but_no_embeddings() -> KnowledgeService:
    """Supabase present, embedding model absent.

    Lets a test reach similarity_search's embedding-model guard, which the
    all-None helper above can never do because the supabase guard fires first.
    """
    service = KnowledgeService.__new__(KnowledgeService)
    service.supabase = MagicMock()
    service.gemini_client = None
    service.embedding_model = None
    return service


def _service_with_chat_model_sentinel() -> KnowledgeService:
    """The historical defect: "chat_model" stood in for a real embedding model
    and the code then produced MD5 hash vectors from it."""
    service = KnowledgeService.__new__(KnowledgeService)
    service.supabase = MagicMock()
    service.gemini_client = MagicMock()
    service.embedding_model = "chat_model"
    return service


class TestFailsClosed:
    @pytest.mark.asyncio
    async def test_create_embeddings_raises_when_the_model_is_unavailable(self):
        service = _service_without_embeddings()
        with pytest.raises(EmbeddingsUnavailable):
            await service.create_embeddings(["some volunteer question"])

    @pytest.mark.asyncio
    async def test_similarity_search_raises_when_the_model_is_unavailable(self):
        service = _service_without_embeddings()
        with pytest.raises(EmbeddingsUnavailable):
            await service.similarity_search("how do I volunteer")

    def test_no_hash_based_fallback_remains(self):
        assert not hasattr(KnowledgeService, "_create_fallback_embeddings")

    def test_is_available_is_false_without_embeddings(self):
        assert _service_without_embeddings().is_available() is False


class TestChatModelSentinelIsRefused:
    """The "chat_model" sentinel must raise, not produce hash vectors."""

    @pytest.mark.asyncio
    async def test_create_embeddings_refuses_the_chat_model_sentinel(self):
        service = _service_with_chat_model_sentinel()
        with pytest.raises(EmbeddingsUnavailable):
            await service.create_embeddings(["some volunteer question"])
        service.gemini_client.models.embed_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_similarity_search_refuses_the_chat_model_sentinel(self):
        service = _service_with_chat_model_sentinel()
        with pytest.raises(EmbeddingsUnavailable):
            await service.similarity_search("how do I volunteer")
        service.supabase.rpc.assert_not_called()


class TestGuardsAreReachableIndependently:
    @pytest.mark.asyncio
    async def test_similarity_search_refuses_a_missing_model_with_supabase_present(
        self,
    ):
        service = _service_with_supabase_but_no_embeddings()
        with pytest.raises(EmbeddingsUnavailable):
            await service.similarity_search("how do I volunteer")
        service.supabase.rpc.assert_not_called()
