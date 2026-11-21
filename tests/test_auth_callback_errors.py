"""Tests for what a failed sign-in shows the person signing in.

When the identity provider cannot complete a sign-in it sends the browser back
to /auth/callback with ``error`` and ``error_description`` and no ``code``.
The callback used to declare ``code`` as required, so FastAPI answered 422 with
a JSON validation body before the handler ran and the reason never reached the
page. That hid a misconfigured client secret behind "Field required".
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth_service import auth_service

client = TestClient(app)


def test_a_provider_error_renders_the_error_page_with_its_reason():
    response = client.get(
        "/auth/callback",
        params={
            "error": "server_error",
            "error_description": "Unable to exchange external code",
        },
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "server_error" in response.text
    assert "Unable to exchange external code" in response.text


def test_a_provider_error_description_is_escaped():
    response = client.get(
        "/auth/callback",
        params={"error": "x", "error_description": "<script>alert(1)</script>"},
    )

    assert response.status_code == 400
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_a_callback_with_no_code_and_no_error_renders_a_page_not_json():
    response = client.get("/auth/callback")

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "Authentication Failed" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "api_url",
    ["https://app.example", "https://app.example/", "https://app.example//"],
)
async def test_the_default_callback_address_has_one_slash(monkeypatch, api_url):
    import app.config as config

    supabase = MagicMock()
    supabase.auth.sign_in_with_oauth.return_value = SimpleNamespace(url="https://x")
    monkeypatch.setattr(config, "API_URL", api_url)
    monkeypatch.setattr(auth_service, "supabase", supabase)

    await auth_service.sign_in_with_google()

    options = supabase.auth.sign_in_with_oauth.call_args.args[0]["options"]
    assert options["redirect_to"] == "https://app.example/auth/callback"
