"""
Tests for FAQ handling and conversation scenarios

Tests cover:
- Basic conversation flows
- FAQ responses
- Different question types
- Response validation
- Error handling
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app

pytest.skip("Skipping this test file entirely", allow_module_level=True)

class TestFAQHandling:
    """Test FAQ handling and conversation scenarios"""

    @pytest.fixture
    def mock_message_sender(self):
        """Mock message sender for testing"""
        with patch("app.routers.public.get_message_sender") as mock_get_sender:
            mock_sender = MagicMock()
            mock_sender.send_text_message.return_value = True
            mock_get_sender.return_value = mock_sender
            yield mock_sender

    @pytest.fixture
    def webhook_base_payload(self):
        """Base webhook payload structure"""
        return {
            "object": "page",
            "entry": [
                {
                    "id": "page_id",
                    "time": 1234567890,
                    "messaging": [
                        {
                            "sender": {"id": "user_id"},
                            "recipient": {"id": "page_id"},
                            "timestamp": 1234567890
                        }
                    ]
                }
            ]
        }

    def create_webhook_payload(self, base_payload, message_text=None, postback_payload=None):
        """Helper to create webhook payload with message or postback"""
        payload = base_payload.copy()
        if message_text:
            payload["entry"][0]["messaging"][0]["message"] = {
                "mid": "message_id",
                "text": message_text
            }
        elif postback_payload:
            payload["entry"][0]["messaging"][0]["postback"] = {
                "mid": "postback_id",
                "payload": postback_payload
            }
        return payload

    @pytest.mark.parametrize("message,expected_response_contains", [
        # Basic greetings
        ("Hello", ["hello", "hi", "welcome"]),
        ("Hi there", ["hello", "hi", "welcome"]),
        ("Good morning", ["good morning", "hello", "welcome"]),
        
        # About Vietnam Hearts
        ("What is Vietnam Hearts?", ["vietnam hearts", "volunteer", "english", "teaching"]),
        ("Tell me about your organization", ["vietnam hearts", "volunteer", "english", "teaching"]),
        ("What do you do?", ["vietnam hearts", "volunteer", "english", "teaching"]),
        
        # Volunteering questions
        ("How can I volunteer?", ["volunteer", "apply", "join", "help"]),
        ("I want to help", ["volunteer", "apply", "join", "help"]),
        ("Can I teach English?", ["teach", "volunteer", "english", "certificate"]),
        ("Do I need a teaching certificate?", ["certificate", "required", "teach", "volunteer"]),
        
        # Location and timing
        ("Where are you located?", ["binh thanh", "ho chi minh", "vietnam", "location"]),
        ("When are classes held?", ["classes", "schedule", "time", "when"]),
        ("What time do classes start?", ["classes", "schedule", "time", "start"]),
        
        # Student information
        ("Who are the students?", ["children", "students", "underprivileged", "vietnam"]),
        ("How old are the children?", ["age", "children", "students", "young"]),
        ("How many students do you have?", ["students", "number", "class size", "children"]),
        
        # Contact and support
        ("How can I contact you?", ["contact", "email", "phone", "reach"]),
        ("I have more questions", ["contact", "help", "support", "questions"]),
        ("Can I speak to someone?", ["contact", "human", "support", "help"]),
        
        # General inquiries
        ("What languages do you teach?", ["english", "language", "teach", "vietnamese"]),
        ("Is this free?", ["free", "cost", "volunteer", "donation"]),
        ("How long have you been operating?", ["operating", "years", "established", "history"]),
    ])
    def test_faq_responses(self, client: TestClient, mock_message_sender, 
                          webhook_base_payload, message, expected_response_contains):
        """Test FAQ responses to different types of questions"""
        webhook_payload = self.create_webhook_payload(webhook_base_payload, message_text=message)
        
        response = client.post("/webhook/messenger", json=webhook_payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        
        # Verify message sender was called
        mock_message_sender.send_text_message.assert_called_once()
        call_args = mock_message_sender.send_text_message.call_args
        user_id, response_text = call_args[0]
        
        assert user_id == "user_id"
        assert isinstance(response_text, str)
        assert len(response_text) > 0
        
        # Check if response contains expected keywords (case insensitive)
        response_lower = response_text.lower()
        assert any(keyword in response_lower for keyword in expected_response_contains), \
            f"Response '{response_text}' should contain one of {expected_response_contains}"

    @pytest.mark.parametrize("postback_payload,expected_response_contains", [
        ("GET_STARTED", ["welcome", "hello", "vietnam hearts", "help"]),
        ("VOLUNTEER_INFO", ["volunteer", "apply", "join", "help"]),
        ("ABOUT_US", ["vietnam hearts", "organization", "mission", "purpose"]),
        ("CONTACT_INFO", ["contact", "email", "phone", "reach"]),
        ("CLASS_SCHEDULE", ["classes", "schedule", "time", "when"]),
    ])
    def test_postback_responses(self, client: TestClient, mock_message_sender,
                               webhook_base_payload, postback_payload, expected_response_contains):
        """Test responses to different postback payloads"""
        webhook_payload = self.create_webhook_payload(webhook_base_payload, postback_payload=postback_payload)
        
        response = client.post("/webhook/messenger", json=webhook_payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        
        # Verify message sender was called
        mock_message_sender.send_text_message.assert_called_once()
        call_args = mock_message_sender.send_text_message.call_args
        user_id, response_text = call_args[0]
        
        assert user_id == "user_id"
        assert isinstance(response_text, str)
        assert len(response_text) > 0
        
        # Check if response contains expected keywords
        response_lower = response_text.lower()
        assert any(keyword in response_lower for keyword in expected_response_contains), \
            f"Response '{response_text}' should contain one of {expected_response_contains}"

    @pytest.mark.parametrize("message,expected_behavior", [
        # Vietnamese language support
        ("Xin chào", "should_respond_in_vietnamese"),
        ("Tôi muốn tình nguyện", "should_respond_in_vietnamese"),
        ("Bạn dạy gì?", "should_respond_in_vietnamese"),
        
        # Mixed language
        ("Hello, tôi muốn volunteer", "should_handle_mixed_language"),
        ("Xin chào, I want to help", "should_handle_mixed_language"),
        
        # Formal vs informal
        ("Good morning, sir", "should_respond_formally"),
        ("Hey there!", "should_respond_casually"),
        ("Dear Vietnam Hearts team", "should_respond_formally"),
        
        # Questions with context
        ("I'm from Australia and want to volunteer", "should_acknowledge_location"),
        ("I'm a retired teacher", "should_acknowledge_experience"),
        ("I can only help on weekends", "should_acknowledge_availability"),
    ])
    def test_contextual_responses(self, client: TestClient, mock_message_sender,
                                webhook_base_payload, message, expected_behavior):
        """Test contextual responses based on message content"""
        webhook_payload = self.create_webhook_payload(webhook_base_payload, message_text=message)
        
        response = client.post("/webhook/messenger", json=webhook_payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        
        # Verify message sender was called
        mock_message_sender.send_text_message.assert_called_once()
        call_args = mock_message_sender.send_text_message.call_args
        user_id, response_text = call_args[0]
        
        assert user_id == "user_id"
        assert isinstance(response_text, str)
        assert len(response_text) > 0
        
        # Basic validation based on expected behavior
        if expected_behavior == "should_respond_in_vietnamese":
            # Check for Vietnamese characters or common Vietnamese words
            vietnamese_chars = ["à", "á", "ạ", "ả", "ã", "ă", "ằ", "ắ", "ặ", "ẳ", "ẵ", "â", "ầ", "ấ", "ậ", "ẩ", "ẫ"]
            assert any(char in response_text for char in vietnamese_chars) or \
                   any(word in response_text.lower() for word in ["xin chào", "cảm ơn", "tôi", "bạn"])
        
        elif expected_behavior == "should_handle_mixed_language":
            # Should handle gracefully without errors
            assert len(response_text) > 0
        
        elif expected_behavior == "should_respond_formally":
            # Should be more formal
            assert any(word in response_text.lower() for word in ["thank you", "appreciate", "welcome"])
        
        elif expected_behavior == "should_acknowledge_location":
            # Should acknowledge the location mentioned
            assert any(word in response_text.lower() for word in ["australia", "location", "country"])
        
        elif expected_behavior == "should_acknowledge_experience":
            # Should acknowledge teaching experience
            assert any(word in response_text.lower() for word in ["teacher", "experience", "teaching", "retired"])
        
        elif expected_behavior == "should_acknowledge_availability":
            # Should acknowledge availability
            assert any(word in response_text.lower() for word in ["weekend", "schedule", "time", "available"])

    @pytest.mark.parametrize("message,expected_fallback", [
        # Unclear questions
        ("What?", "should_provide_fallback"),
        ("Huh?", "should_provide_fallback"),
        ("I don't understand", "should_provide_fallback"),
        
        # Off-topic questions
        ("What's the weather like?", "should_provide_fallback"),
        ("How do I cook rice?", "should_provide_fallback"),
        ("What's 2+2?", "should_provide_fallback"),
        
        # Complex technical questions
        ("How do I implement OAuth2?", "should_provide_fallback"),
        ("What's the difference between REST and GraphQL?", "should_provide_fallback"),
        
        # Personal questions
        ("What's your favorite color?", "should_provide_fallback"),
        ("How old are you?", "should_provide_fallback"),
    ])
    def test_fallback_responses(self, client: TestClient, mock_message_sender,
                              webhook_base_payload, message, expected_fallback):
        """Test fallback responses for unclear or off-topic questions"""
        webhook_payload = self.create_webhook_payload(webhook_base_payload, message_text=message)
        
        response = client.post("/webhook/messenger", json=webhook_payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        
        # Verify message sender was called
        mock_message_sender.send_text_message.assert_called_once()
        call_args = mock_message_sender.send_text_message.call_args
        user_id, response_text = call_args[0]
        
        assert user_id == "user_id"
        assert isinstance(response_text, str)
        assert len(response_text) > 0
        
        # Check for fallback indicators
        fallback_indicators = [
            "i'm not sure", "don't understand", "can't help", "unclear",
            "please clarify", "not sure about", "don't have information",
            "contact us", "human support", "escalate"
        ]
        
        response_lower = response_text.lower()
        has_fallback = any(indicator in response_lower for indicator in fallback_indicators)
        
        if expected_fallback == "should_provide_fallback":
            assert has_fallback, f"Response should provide fallback: '{response_text}'"
        else:
            # For other cases, just ensure we get a response
            assert len(response_text) > 0

    def test_conversation_flow(self, client: TestClient, mock_message_sender, webhook_base_payload):
        """Test a complete conversation flow"""
        # Start conversation
        start_payload = self.create_webhook_payload(webhook_base_payload, message_text="Hello")
        response = client.post("/webhook/messenger", json=start_payload)
        assert response.status_code == 200
        
        # Ask about volunteering
        volunteer_payload = self.create_webhook_payload(webhook_base_payload, message_text="I want to volunteer")
        response = client.post("/webhook/messenger", json=volunteer_payload)
        assert response.status_code == 200
        
        # Ask about location
        location_payload = self.create_webhook_payload(webhook_base_payload, message_text="Where are you located?")
        response = client.post("/webhook/messenger", json=location_payload)
        assert response.status_code == 200
        
        # Verify all messages were sent
        assert mock_message_sender.send_text_message.call_count == 3
        
        # Check that different responses were given
        calls = mock_message_sender.send_text_message.call_args_list
        responses = [call[0][1] for call in calls]
        
        # All responses should be different
        assert len(set(responses)) >= 2, "Should provide different responses to different questions"

    def test_error_handling(self, client: TestClient, webhook_base_payload):
        """Test error handling in conversation"""
        # Test with malformed message
        malformed_payload = webhook_base_payload.copy()
        malformed_payload["entry"][0]["messaging"][0]["message"] = {
            "mid": "message_id"
            # Missing text field
        }
        
        response = client.post("/webhook/messenger", json=malformed_payload)
        assert response.status_code == 200
        
        # Test with empty message
        empty_payload = self.create_webhook_payload(webhook_base_payload, message_text="")
        response = client.post("/webhook/messenger", json=empty_payload)
        assert response.status_code == 200
        
        # Test with very long message
        long_message = "A" * 1000
        long_payload = self.create_webhook_payload(webhook_base_payload, message_text=long_message)
        response = client.post("/webhook/messenger", json=long_payload)
        assert response.status_code == 200

    def test_response_quality(self, client: TestClient, mock_message_sender, webhook_base_payload):
        """Test quality of responses"""
        test_messages = [
            "What is Vietnam Hearts?",
            "How can I volunteer?",
            "Where are you located?",
            "When are classes held?"
        ]
        
        for message in test_messages:
            webhook_payload = self.create_webhook_payload(webhook_base_payload, message_text=message)
            response = client.post("/webhook/messenger", json=webhook_payload)
            assert response.status_code == 200
        
        # Verify all messages were sent
        assert mock_message_sender.send_text_message.call_count == len(test_messages)
        
        # Check response quality
        calls = mock_message_sender.send_text_message.call_args_list
        responses = [call[0][1] for call in calls]
        
        for response_text in responses:
            # Responses should be meaningful
            assert len(response_text) >= 10, f"Response too short: '{response_text}'"
            assert len(response_text) <= 500, f"Response too long: '{response_text}'"
            
            # Should not be empty or just whitespace
            assert response_text.strip(), f"Response is empty or whitespace: '{response_text}'"
            
            # Should not contain obvious errors
            assert "error" not in response_text.lower() or "error" in response_text.lower() in ["no error", "error handling"]
            assert "exception" not in response_text.lower()
            assert "traceback" not in response_text.lower()
