"""When embeddings are unavailable the service must refuse, not improvise."""

import pytest

from app.services.knowledge_service import EmbeddingsUnavailable, KnowledgeService


def _service_without_embeddings() -> KnowledgeService:
    service = KnowledgeService.__new__(KnowledgeService)
    service.supabase = None
    service.gemini_client = None
    service.embedding_model = None
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
