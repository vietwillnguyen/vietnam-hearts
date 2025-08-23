"""
Tests for Facebook Messenger webhook functionality

Tests cover:
- Webhook verification
- Message processing
- Echo functionality (Phase 1)
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app


class TestMessengerWebhook:
    """Test the Facebook Messenger webhook endpoints"""

    def test_verify_webhook_success(self, client: TestClient):
        """Test successful webhook verification"""
        with patch("app.routers.public.FACEBOOK_VERIFY_TOKEN", "test_token"):
            response = client.get(
                "/webhook/messenger",
                params={
                    "mode": "subscribe",
                    "verify_token": "test_token",
                    "challenge": "1234567890"
                }
            )
            
            assert response.status_code == 200
            assert response.json() == 1234567890

    def test_verify_webhook_failure(self, client: TestClient):
        """Test failed webhook verification"""
        with patch("app.routers.public.FACEBOOK_VERIFY_TOKEN", "test_token"):
            response = client.get(
                "/webhook/messenger",
                params={
                    "mode": "subscribe",
                    "verify_token": "wrong_token",
                    "challenge": "1234567890"
                }
            )
            
            assert response.status_code == 200
            assert "error" in response.json()

    def test_verify_webhook_no_token(self, client: TestClient):
        """Test webhook verification without token"""
        with patch("app.routers.public.FACEBOOK_VERIFY_TOKEN", None):
            response = client.get(
                "/webhook/messenger",
                params={
                    "mode": "subscribe",
                    "verify_token": "test_token",
                    "challenge": "1234567890"
                }
            )
            
            assert response.status_code == 200
            assert "error" in response.json()

    def test_handle_webhook_valid_page_event(self, client: TestClient):
        """Test handling valid page event webhook"""
        with patch("app.routers.public.MessageSender") as mock_sender_class:
            mock_sender = MagicMock()
            mock_sender.send_text_message.return_value = True
            mock_sender_class.return_value = mock_sender
            
            webhook_payload = {
                "object": "page",
                "entry": [
                    {
                        "id": "page_id",
                        "time": 1234567890,
                        "messaging": [
                            {
                                "sender": {"id": "user_id"},
                                "recipient": {"id": "page_id"},
                                "timestamp": 1234567890,
                                "message": {
                                    "mid": "message_id",
                                    "text": "Hello, bot!"
                                }
                            }
                        ]
                    }
                ]
            }
            
            response = client.post("/webhook/messenger", json=webhook_payload)
            
            assert response.status_code == 200
            assert response.json()["status"] == "success"
            
            # Verify message sender was called
            mock_sender.send_text_message.assert_called_once_with(
                "user_id", "Echo: Hello, bot!"
            )

    def test_handle_webhook_invalid_object(self, client: TestClient):
        """Test handling webhook with invalid object type"""
        webhook_payload = {
            "object": "user",
            "entry": []
        }
        
        response = client.post("/webhook/messenger", json=webhook_payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "error"

    def test_handle_webhook_postback_event(self, client: TestClient):
        """Test handling postback event webhook"""
        with patch("app.routers.public.MessageSender") as mock_sender_class:
            mock_sender = MagicMock()
            mock_sender.send_text_message.return_value = True
            mock_sender_class.return_value = mock_sender
            
            webhook_payload = {
                "object": "page",
                "entry": [
                    {
                        "id": "page_id",
                        "time": 1234567890,
                        "messaging": [
                            {
                                "sender": {"id": "user_id"},
                                "recipient": {"id": "page_id"},
                                "timestamp": 1234567890,
                                "postback": {
                                    "mid": "postback_id",
                                    "payload": "GET_STARTED"
                                }
                            }
                        ]
                    }
                ]
            }
            
            response = client.post("/webhook/messenger", json=webhook_payload)
            
            assert response.status_code == 200
            assert response.json()["status"] == "success"
            
            # Verify message sender was called
            mock_sender.send_text_message.assert_called_once_with(
                "user_id", "Postback received: GET_STARTED"
            )

    def test_handle_webhook_no_sender_id(self, client: TestClient):
        """Test handling webhook event without sender ID"""
        webhook_payload = {
            "object": "page",
            "entry": [
                {
                    "id": "page_id",
                    "time": 1234567890,
                    "messaging": [
                        {
                            "recipient": {"id": "page_id"},
                            "timestamp": 1234567890,
                            "message": {
                                "mid": "message_id",
                                "text": "Hello, bot!"
                            }
                        }
                    ]
                }
            ]
        }
        
        response = client.post("/webhook/messenger", json=webhook_payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_messenger_configuration_endpoint(self, client: TestClient):
        """Test the messenger configuration test endpoint"""
        with patch("app.routers.public.FACEBOOK_VERIFY_TOKEN", "test_token"), \
             patch("app.routers.public.FACEBOOK_ACCESS_TOKEN", "test_access_token"), \
             patch("app.routers.public.MessageSender") as mock_sender_class:
            
            mock_sender = MagicMock()
            mock_sender.get_page_info.return_value = {"name": "Test Page", "id": "123"}
            mock_sender_class.return_value = mock_sender
            
            response = client.get("/test-messenger")
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["config"]["FACEBOOK_VERIFY_TOKEN"] is True
            assert data["config"]["FACEBOOK_ACCESS_TOKEN"] is True
            assert data["webhook_url"] == "/webhook/messenger"

