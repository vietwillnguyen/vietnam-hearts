"""The bot service must be constructed once per process, not per message."""

from unittest.mock import MagicMock, patch

from app.dependencies.services import get_bot_service, reset_bot_service


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
