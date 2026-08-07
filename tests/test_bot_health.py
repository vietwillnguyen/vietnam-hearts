"""
Tests for BotService.health_check and the /health reporting built on it.

The property under test: the bot_service entry in /health must be capable of
reporting a failure. It previously could not - BotService had no health_check
and get_bot_service never returns None, so the check was structurally always
"healthy".
"""

from unittest.mock import MagicMock, patch

from app.services.bot_service import BotService
from app.services.knowledge_service import KnowledgeService


def _bot_service(
    gemini_client: object = "client",
    embedding_model: str | None = "gemini-embedding-001",
    supabase: object = "supabase",
    documents: bool | Exception = True,
) -> BotService:
    """Build a BotService with a stubbed knowledge service."""
    knowledge = MagicMock(spec=KnowledgeService)
    knowledge.gemini_client = gemini_client
    knowledge.embedding_model = embedding_model
    knowledge.supabase = supabase
    if isinstance(documents, Exception):
        knowledge.has_indexed_documents.side_effect = documents
    else:
        knowledge.has_indexed_documents.return_value = documents
    knowledge.revalidate.return_value = None

    with patch("app.services.bot_service.KnowledgeService", return_value=knowledge):
        return BotService(supabase)


class TestHealthCheckExists:
    """The dead branch in public.py assumed a method that was never written."""

    def test_bot_service_has_health_check(self):
        assert callable(getattr(BotService, "health_check", None))


class TestHealthCheckVerdicts:
    """health_check reports on the dependencies the bot actually needs."""

    def test_healthy_when_every_dependency_is_present(self):
        result = _bot_service().health_check()

        assert result["status"] == "healthy"
        assert result["error"] is None

    def test_unhealthy_without_a_gemini_client(self):
        result = _bot_service(gemini_client=None).health_check()

        assert result["status"] == "unhealthy"
        assert "gemini" in result["error"].lower()
        assert result["checks"]["gemini"] != "ok"

    def test_unhealthy_when_the_embedding_probe_failed(self):
        result = _bot_service(embedding_model=None).health_check()

        assert result["status"] == "unhealthy"
        assert "embedding" in result["error"].lower()

    def test_unhealthy_without_a_vector_store(self):
        result = _bot_service(supabase=None).health_check()

        assert result["status"] == "unhealthy"
        assert result["checks"]["vector_store"] != "ok"

    def test_degraded_when_no_document_is_indexed(self):
        """
        Dependencies are fine but there is nothing to retrieve, so the bot would
        answer ungrounded. Real, reportable, but not a broken dependency.
        """
        result = _bot_service(documents=False).health_check()

        assert result["status"] == "degraded"
        assert result["checks"]["documents"] == "empty"

    def test_reports_each_dependency_separately(self):
        checks = _bot_service().health_check()["checks"]

        assert set(checks) == {"gemini", "embeddings", "vector_store", "documents"}


class TestHealthCheckNeverThrows:
    """A health endpoint that 500s is worse than one that lies."""

    def test_probe_failure_is_reported_not_raised(self):
        result = _bot_service(
            documents=RuntimeError("supabase unreachable")
        ).health_check()

        assert result["status"] == "unhealthy"
        assert "supabase unreachable" in result["error"]

    def test_survives_a_missing_knowledge_service(self):
        service = _bot_service()
        service.knowledge_service = None

        result = service.health_check()

        assert result["status"] == "unhealthy"
        assert result["error"]

    def test_survives_revalidate_blowing_up(self):
        service = _bot_service(embedding_model=None)
        service.knowledge_service.revalidate.side_effect = RuntimeError("gemini down")

        result = service.health_check()

        assert result["status"] == "unhealthy"


class TestStickyDegradationRecovers:
    """
    A transient outage at construction must not pin the instance degraded for
    the process lifetime.
    """

    def test_failed_construction_probe_recovers_on_a_later_check(self):
        """Construction probe fails, a later revalidate succeeds."""
        probes = [None, "gemini-embedding-001"]
        with (
            patch.object(KnowledgeService, "_get_gemini_client", return_value="client"),
            patch.object(KnowledgeService, "_get_embedding_model", side_effect=probes),
        ):
            service = KnowledgeService(supabase_client="supabase")
            assert service.embedding_model is None

            service._last_probe = float("-inf")
            service.revalidate()

            assert service.embedding_model == "gemini-embedding-001"

    def test_revalidate_respects_a_cooldown(self):
        """A frequently polled health endpoint must not hammer a 15 RPM quota."""
        with (
            patch.object(KnowledgeService, "_get_gemini_client", return_value="client"),
            patch.object(
                KnowledgeService, "_get_embedding_model", return_value=None
            ) as probe,
        ):
            service = KnowledgeService(supabase_client="supabase")
            probe.reset_mock()

            for _ in range(5):
                service.revalidate()

            assert probe.call_count == 0, "re-probed inside the cooldown window"

    def test_revalidate_does_nothing_when_already_healthy(self):
        with (
            patch.object(KnowledgeService, "_get_gemini_client", return_value="client"),
            patch.object(
                KnowledgeService, "_get_embedding_model", return_value="model"
            ) as probe,
        ):
            service = KnowledgeService(supabase_client="supabase")
            probe.reset_mock()
            service._last_probe = float("-inf")

            service.revalidate()

            assert probe.call_count == 0


class TestHealthEndpointReportsBotFailure:
    """/health must surface a genuine bot failure, and still return 200."""

    def _patched(self, health):
        stub = MagicMock()
        stub.health_check.return_value = health
        return patch("app.routers.bot.get_bot_service", return_value=stub)

    def test_unhealthy_bot_is_reported(self, client):
        health = {
            "status": "unhealthy",
            "error": "Gemini client unavailable",
            "checks": {"gemini": "unconfigured"},
        }
        with self._patched(health):
            response = client.get("/health")

        assert response.status_code == 200
        bot = response.json()["services"]["bot_service"]
        assert bot["status"] == "unhealthy"
        assert bot["error"] == "Gemini client unavailable"
        assert response.json()["status"] == "unhealthy"

    def test_degraded_bot_does_not_flip_the_overall_light(self, client):
        """
        An unindexed knowledge base is a known open question, not a broken
        dependency, and the bot routers are not even mounted. Report it without
        turning the top-level status red.
        """
        health = {
            "status": "degraded",
            "error": "no indexed documents",
            "checks": {"documents": "empty"},
        }
        with self._patched(health):
            response = client.get("/health")

        body = response.json()
        assert body["services"]["bot_service"]["status"] == "degraded"
        assert body["status"] != "unhealthy"

    def test_checks_are_surfaced(self, client):
        health = {
            "status": "healthy",
            "error": None,
            "checks": {"gemini": "ok", "documents": "ok"},
        }
        with self._patched(health):
            response = client.get("/health")

        assert response.json()["services"]["bot_service"]["checks"] == health["checks"]

    def test_a_throwing_provider_does_not_500_the_route(self, client):
        with patch("app.routers.bot.get_bot_service", side_effect=RuntimeError("boom")):
            response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["services"]["bot_service"]["status"] == "unhealthy"
