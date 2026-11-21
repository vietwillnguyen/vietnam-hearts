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
from fastapi import HTTPException

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
            pytest.raises(HTTPException),
        ):
            asyncio.run(
                auth_service._get_user_from_apikey("sb_secret_wrong_key_value_here")
            )


class TestServiceRoleAdminAccess:
    """The scheduler authenticates with the service-role key, not a user account.

    Pins down the 403s that stopped the Sunday weekly-reminder job for four
    weeks (2026-09-27 through 2026-10-18). A valid service-role key already
    carries the highest privilege the app has, but get_current_admin_user put
    the synthetic service-account email through the admin_users lookup anyway.
    That lookup fails closed, so the scheduler was denied whenever the row was
    missing or Supabase was slow.
    """

    @pytest.fixture
    def request_with_apikey(self):
        request = MagicMock()
        request.headers = {"apikey": CURRENT_FORMAT_SECRET_KEY}
        request.query_params = {}
        request.cookies = {}
        return request

    def test_grants_admin_without_consulting_admin_users(
        self, auth_service, request_with_apikey
    ):
        auth_service.admin_supabase = MagicMock()
        with (
            patch(
                "app.services.auth_service.SUPABASE_SECRET_KEY",
                CURRENT_FORMAT_SECRET_KEY,
            ),
            patch("app.services.auth_service.ADMIN_EMAILS", []),
        ):
            user = asyncio.run(auth_service.get_current_admin_user(request_with_apikey))

        assert user["id"] == "service-account-auto-scheduler"
        auth_service.admin_supabase.table.assert_not_called()

    def test_grants_admin_when_supabase_is_unreachable(
        self, auth_service, request_with_apikey
    ):
        """A Supabase outage must not take the scheduler down with it."""
        auth_service.admin_supabase = MagicMock()
        auth_service.admin_supabase.table.side_effect = OSError("supabase unreachable")
        with (
            patch(
                "app.services.auth_service.SUPABASE_SECRET_KEY",
                CURRENT_FORMAT_SECRET_KEY,
            ),
            patch("app.services.auth_service.ADMIN_EMAILS", []),
        ):
            user = asyncio.run(auth_service.get_current_admin_user(request_with_apikey))

        assert user["id"] == "service-account-auto-scheduler"
