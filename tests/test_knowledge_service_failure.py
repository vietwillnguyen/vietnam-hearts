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
    """Supabase and a Gemini client present, embedding model absent.

    Lets a test reach similarity_search's embedding-model guard, which the
    all-None helper above can never do because the supabase guard fires first,
    and lets a test assert that neither dependency is called once it fires.
    """
    service = KnowledgeService.__new__(KnowledgeService)
    service.supabase = MagicMock()
    service.gemini_client = MagicMock()
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


class TestRefusesBeforeCallingOut:
    """A missing embedding model must short-circuit before any outbound call."""

    @pytest.mark.asyncio
    async def test_create_embeddings_does_not_reach_gemini(self):
        service = _service_with_supabase_but_no_embeddings()
        with pytest.raises(EmbeddingsUnavailable):
            await service.create_embeddings(["some volunteer question"])
        service.gemini_client.models.embed_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_similarity_search_does_not_reach_gemini(self):
        service = _service_with_supabase_but_no_embeddings()
        with pytest.raises(EmbeddingsUnavailable):
            await service.similarity_search("how do I volunteer")
        service.gemini_client.models.embed_content.assert_not_called()


class TestDegradesToNoneNotToAStandIn:
    """A failed embedding probe must yield None, not a stand-in model name.

    The old code returned the string "chat_model" here, which every caller then
    had to special-case back into unavailable. Degrading to None keeps one
    representation of "cannot embed" and skips a wasted Gemini call on a path
    that is already failing.
    """

    def test_failed_probe_returns_none_without_probing_the_chat_model(self):
        service = KnowledgeService.__new__(KnowledgeService)
        service.gemini_client = MagicMock()
        service.gemini_client.models.embed_content.side_effect = RuntimeError(
            "embedding model unavailable"
        )

        assert service._get_embedding_model() is None
        service.gemini_client.models.generate_content.assert_not_called()

    def test_missing_client_returns_none(self):
        service = KnowledgeService.__new__(KnowledgeService)
        service.gemini_client = None

        assert service._get_embedding_model() is None


class TestGuardsAreReachableIndependently:
    @pytest.mark.asyncio
    async def test_similarity_search_refuses_a_missing_model_with_supabase_present(
        self,
    ):
        service = _service_with_supabase_but_no_embeddings()
        with pytest.raises(EmbeddingsUnavailable):
            await service.similarity_search("how do I volunteer")
        service.supabase.rpc.assert_not_called()
