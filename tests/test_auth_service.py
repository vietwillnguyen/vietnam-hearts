"""Regression test for the apikey admin-auth path in AuthService.

Pins down the production bug (Sentry VIETNAM-HEARTS-6, 2026-07-12):
_is_secret_key() required tokens longer than 50 chars for the "sb_" prefix,
but Supabase's current sb_secret_/sb_publishable_ keys are ~41-46 chars.
That rejected the app's own correctly-configured SUPABASE_SECRET_KEY before
it ever reached the equality check, so every apikey-authenticated admin
request (e.g. the review-and-sync cron) 401'd regardless of the caller.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.services.auth_service import AuthService

# Synthetic key matching the current Supabase format/length, not a real
# credential: "sb_secret_" + 31 chars = 41 total.
CURRENT_FORMAT_SECRET_KEY = "sb_secret_" + "x" * 31


@pytest.fixture
def auth_service():
    with patch("app.services.auth_service.create_client", return_value=MagicMock()):
        return AuthService()


class TestIsSecretKey:
    def test_accepts_current_format_sb_secret_key(self, auth_service):
        assert auth_service._is_secret_key(CURRENT_FORMAT_SECRET_KEY) is True

    def test_rejects_empty_token(self, auth_service):
        assert auth_service._is_secret_key("") is False

    def test_rejects_short_garbage_token(self, auth_service):
        assert auth_service._is_secret_key("sb_abc") is False


class TestGetUserFromApikey:
    def test_accepts_configured_current_format_secret_key(self, auth_service):
        with patch(
            "app.services.auth_service.SUPABASE_SECRET_KEY", CURRENT_FORMAT_SECRET_KEY
        ):
            user = asyncio.run(
                auth_service._get_user_from_apikey(CURRENT_FORMAT_SECRET_KEY)
            )
        assert user["id"] == "service-account-auto-scheduler"

    def test_rejects_mismatched_key(self, auth_service):
        with (
            patch(
                "app.services.auth_service.SUPABASE_SECRET_KEY",
                CURRENT_FORMAT_SECRET_KEY,
            ),
            pytest.raises(Exception),
        ):
            asyncio.run(
                auth_service._get_user_from_apikey("sb_secret_wrong_key_value_here")
            )
