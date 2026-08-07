"""The bot service must be constructed once per process, not per message."""

from unittest.mock import MagicMock, patch

from app.dependencies.services import (
    _build_supabase_client,
    get_bot_service,
    reset_bot_service,
)


class TestGetBotService:
    def setup_method(self):
        reset_bot_service()

    def teardown_method(self):
        reset_bot_service()

    def test_constructs_the_service_only_once_across_many_calls(self):
        with patch("app.dependencies.services.BotService") as mock_cls:
            mock_cls.return_value = MagicMock()
            first = get_bot_service()
            for _ in range(10):
                get_bot_service()
            assert mock_cls.call_count == 1
            assert get_bot_service() is first

    def test_reset_forces_a_fresh_construction(self):
        with patch("app.dependencies.services.BotService") as mock_cls:
            mock_cls.side_effect = [MagicMock(), MagicMock()]
            first = get_bot_service()
            reset_bot_service()
            second = get_bot_service()
            assert mock_cls.call_count == 2
            assert first is not second

    def test_passes_the_supabase_client_through_to_the_bot_service(self):
        # A BotService built with None has no vector store, so fail-closed
        # retrieval raises on every question. This is the regression test.
        fake_client = object()
        with (
            patch("app.dependencies.services.BotService") as mock_cls,
            patch(
                "app.dependencies.services._build_supabase_client",
                return_value=fake_client,
            ),
        ):
            get_bot_service()
            mock_cls.assert_called_once_with(fake_client)


class TestBuildSupabaseClient:
    def test_returns_none_when_credentials_are_absent(self):
        with (
            patch("app.dependencies.services.SUPABASE_URL", ""),
            patch("app.dependencies.services.SUPABASE_SECRET_KEY", ""),
        ):
            assert _build_supabase_client() is None

    def test_returns_none_when_client_construction_raises(self):
        with (
            patch(
                "app.dependencies.services.SUPABASE_URL", "https://example.supabase.co"
            ),
            patch("app.dependencies.services.SUPABASE_SECRET_KEY", "secret"),
            patch(
                "supabase.create_client",
                side_effect=RuntimeError("boom"),
            ),
        ):
            assert _build_supabase_client() is None
