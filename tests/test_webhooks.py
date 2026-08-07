"""Integration tests for the Meta webhook endpoint."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.meta_payloads import (
    VERIFY_QUERY,
    page_echo,
    page_postback,
    page_text_message,
)

APP_SECRET = "test_app_secret"
VERIFY_TOKEN = "test_verify_token"


def _signed(payload) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {
        "X-Hub-Signature-256": f"sha256={digest}",
        "Content-Type": "application/json",
    }


@pytest.fixture
def meta_config():
    with (
        patch("app.routers.webhooks.FACEBOOK_APP_SECRET", APP_SECRET),
        patch("app.routers.webhooks.FACEBOOK_VERIFY_TOKEN", VERIFY_TOKEN),
    ):
        yield


@pytest.fixture
def sender():
    mock = MagicMock()
    mock.send_text_message.return_value = True
    with patch("app.routers.webhooks.get_message_sender", return_value=mock):
        yield mock


@pytest.fixture
def bot():
    mock = MagicMock()
    mock.chat = AsyncMock(
        return_value={"response": "You can volunteer by signing up.", "confidence": 0.9}
    )
    with patch("app.routers.webhooks.get_bot_service", return_value=mock):
        yield mock


class TestVerification:
    def test_echoes_the_challenge_for_a_correct_token(
        self, client: TestClient, meta_config
    ):
        response = client.get("/webhook/meta", params=VERIFY_QUERY)
        assert response.status_code == 200
        assert response.text == VERIFY_QUERY["hub.challenge"]

    def test_echoes_a_non_numeric_challenge(self, client: TestClient, meta_config):
        query = {**VERIFY_QUERY, "hub.challenge": "a1b2-c3d4"}
        response = client.get("/webhook/meta", params=query)
        assert response.status_code == 200
        assert response.text == "a1b2-c3d4"

    def test_refuses_a_wrong_token(self, client: TestClient, meta_config):
        query = {**VERIFY_QUERY, "hub.verify_token": "wrong"}
        assert client.get("/webhook/meta", params=query).status_code == 403

    def test_refuses_a_non_ascii_token_with_403_not_500(
        self, client: TestClient, meta_config
    ):
        query = {**VERIFY_QUERY, "hub.verify_token": "café"}
        response = client.get("/webhook/meta", params=query)
        assert response.status_code == 403


class TestSignature:
    def test_rejects_an_unsigned_post(
        self, client: TestClient, meta_config, sender, bot
    ):
        response = client.post("/webhook/meta", json=page_text_message())
        assert response.status_code == 403
        sender.send_text_message.assert_not_called()

    def test_rejects_a_tampered_body(
        self, client: TestClient, meta_config, sender, bot
    ):
        _, headers = _signed(page_text_message())
        response = client.post(
            "/webhook/meta",
            content=json.dumps(page_text_message(text="different")).encode(),
            headers=headers,
        )
        assert response.status_code == 403
        sender.send_text_message.assert_not_called()

    def test_verifies_over_raw_bytes_not_reserialized_json(
        self, client: TestClient, meta_config, sender, bot
    ):
        # Signed over pretty-printed bytes. An implementation that hashed
        # json.dumps(await request.json()) would compute a different digest
        # and reject this, so this test pins the raw-bytes requirement.
        body = json.dumps(page_text_message(), indent=2).encode()
        digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        headers = {
            "X-Hub-Signature-256": f"sha256={digest}",
            "Content-Type": "application/json",
        }
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        sender.send_text_message.assert_called_once()


class TestDispatch:
    def test_answers_a_text_message(self, client: TestClient, meta_config, sender, bot):
        body, headers = _signed(page_text_message())
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        sender.send_text_message.assert_called_once_with(
            "USER_PSID", "You can volunteer by signing up."
        )

    def test_ignores_its_own_echo(self, client: TestClient, meta_config, sender, bot):
        body, headers = _signed(page_echo())
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        sender.send_text_message.assert_not_called()

    def test_acknowledges_a_postback_without_calling_the_bot(
        self, client: TestClient, meta_config, sender, bot
    ):
        body, headers = _signed(page_postback())
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        bot.chat.assert_not_called()
        sender.send_text_message.assert_not_called()

    def test_returns_200_for_an_unknown_object_type(
        self, client: TestClient, meta_config, sender, bot
    ):
        body, headers = _signed({"object": "whatsapp_business_account", "entry": []})
        response = client.post("/webhook/meta", content=body, headers=headers)
        # Never 4xx: Meta disables webhooks after repeated failures.
        assert response.status_code == 200
        sender.send_text_message.assert_not_called()

    def test_stays_silent_when_the_bot_raises(
        self, client: TestClient, meta_config, sender, bot
    ):
        from app.services.knowledge_service import EmbeddingsUnavailable

        bot.chat = AsyncMock(side_effect=EmbeddingsUnavailable("down"))
        body, headers = _signed(page_text_message())
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        # Phase 1 replaces silence with a holding message plus an escalation.
        sender.send_text_message.assert_not_called()

    def test_returns_200_for_an_unparseable_body(
        self, client: TestClient, meta_config, sender, bot
    ):
        body = b"{not json at all"
        digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = client.post(
            "/webhook/meta",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={digest}"},
        )
        assert response.status_code == 200
        sender.send_text_message.assert_not_called()

    def test_returns_200_for_structurally_broken_payloads(
        self, client: TestClient, meta_config, sender, bot
    ):
        # Meta disables a subscription after repeated non-200s, so even
        # nonsense that clears the signature check must not 4xx or 5xx.
        for payload in (
            [1, 2, 3],
            "a string",
            5,
            None,
            {"object": "page", "entry": 5},
            {"object": "page", "entry": ["x"]},
            {"object": "page", "entry": [{"messaging": 7}]},
        ):
            body, headers = _signed(payload)
            response = client.post("/webhook/meta", content=body, headers=headers)
            assert response.status_code == 200, f"non-200 for {payload!r}"
        sender.send_text_message.assert_not_called()
