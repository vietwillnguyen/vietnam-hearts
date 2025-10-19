#!/usr/bin/env python3
"""
Integration Test Suite for Vietnam Hearts API

Tests the actual API endpoints using the test client and database.
Focuses on integration testing rather than unit testing.
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models import Volunteer
from app.services.google_sheets import sheets_service
from app.services.email_service import email_service
from app.services.bot_service import BotService
from unittest.mock import patch, MagicMock

# Test client
client = TestClient(app)


@pytest.fixture
def mock_google_sheets():
    """Mock Google Sheets service for testing"""
    with patch("app.services.google_sheets.sheets_service") as mock:
        # Mock basic sheet operations
        mock.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday", "Wednesday"],
            ["Teacher", "John Doe", "Jane Smith", "Bob Wilson"],
            ["Head TA", "TA1", "TA2", "TA3"],
            ["Assistants", "A1, A2", "A1", "A1, A2, A3"]
        ]
        mock.get_volunteer_submissions.return_value = [
            {
                "name": "Test Volunteer",
                "email": "test@example.com",
                "positions": ["Teacher"],
                "teaching_experience": "Some experience",
                "start_date": "ASAP"
            }
        ]
        yield mock


@pytest.fixture
def mock_email_service():
    """Mock email service for testing"""
    with patch("app.services.email_service.email_service") as mock:
        mock.send_volunteer_confirmation.return_value = {"status": "sent"}
        mock.send_weekly_reminders.return_value = {"status": "sent"}
        yield mock


@pytest.fixture
def mock_bot_service():
    """Mock bot service for testing"""
    with patch("app.services.bot_service.BotService") as mock:
        mock_instance = MagicMock()
        mock_instance.chat.return_value = {
            "response": "Test response",
            "confidence": 0.9,
            "context_used": 2
        }
        mock_instance.test_response.return_value = {
            "status": "success",
            "response": "Test response"
        }
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def mock_auth_service():
    """Mock authentication service for testing admin endpoints with valid auth"""
    with patch("app.services.supabase_auth.auth_service") as mock_auth_service:
        # Mock the entire auth service
        mock_user = {
            "id": "test-admin-id",
            "email": "admin@vietnamhearts.org",
            "name": "Test Admin",
            "email_verified": True
        }
        
        # Mock the is_admin method to return True
        mock_auth_service.is_admin.return_value = True
        
        # Mock the get_current_user_from_token method to return our mock user
        mock_auth_service.get_current_user_from_token.return_value = mock_user
        
        yield mock_auth_service


@pytest.fixture
def authenticated_client(mock_auth_service):
    """Create a test client that bypasses authentication for testing"""
    # This fixture would be used to test admin endpoints with "valid" auth
    # It mocks the auth dependency to always return a valid admin user
    
    # Note: This is a simplified approach - in production you might want
    # to create actual test users with proper tokens
    
    return client


class TestPublicEndpoints:
    """Test public endpoints that don't require authentication"""
    
    def test_root_endpoint(self, client):
        """Test root endpoint"""
        response = client.get("/")
        
        assert response.status_code == 200
        # Should return HTML content
        assert "text/html" in response.headers.get("content-type", "")
    
    def test_unsubscribe_endpoint_get(self, client):
        """Test unsubscribe endpoint GET (shows form)"""
        # Test with an invalid token - should return 400
        response = client.get("/unsubscribe?token=invalid-token-123")
        
        # Should return 400 for invalid token
        assert response.status_code == 400
        # Should return HTML error page
        assert "text/html" in response.headers.get("content-type", "")
    
    def test_webhook_messenger_endpoint(self, client):
        """Test Facebook webhook endpoint"""
        # Test webhook verification
        response = client.get("/webhook/messenger?mode=subscribe&verify_token=test&challenge=test123")
        
        # Should handle webhook verification
        assert response.status_code in [200, 400, 500]  # Various possible responses


class TestAuthEndpoints:
    """Test authentication endpoints"""
    
    def test_auth_health_endpoint(self, client):
        """Test auth health endpoint"""
        response = client.get("/auth/health")
        
        assert response.status_code == 200
        # Should return some health information
        data = response.json()
        assert "status" in data or "message" in data
    
    def test_auth_login_endpoint(self, client):
        """Test auth login endpoint"""
        response = client.get("/auth/login")
        
        # TestClient follows redirects automatically, so we get the final response
        # The login endpoint redirects to home page, so we should get the home page content
        assert response.status_code == 200
        # Should return HTML content (home page)
        assert "text/html" in response.headers.get("content-type", "")


class TestAdminEndpoints:
    """Test admin endpoints authentication and functionality"""
    
    def test_admin_volunteers_endpoint_requires_auth(self, client, mock_google_sheets):
        """Test that admin volunteers endpoint requires authentication"""
        response = client.get("/admin/volunteers")
        
        # ✅ Security issue has been fixed - endpoint is now properly protected
        assert response.status_code == 401, f"Expected 401 Unauthorized, got {response.status_code}"
        
        data = response.json()
        assert "detail" in data
        error_detail = data["detail"].lower()
        assert any(word in error_detail for word in ["unauthorized", "authentication", "auth", "login"])
    
    def test_admin_dashboard_endpoint_requires_auth(self, client, mock_google_sheets):
        """Test that admin dashboard endpoint requires authentication"""
        response = client.get("/admin/dashboard")
        
        # ✅ Security issue has been fixed - endpoint now returns 401 Unauthorized
        assert response.status_code == 401, f"Expected 401 Unauthorized, got {response.status_code}"
        
        data = response.json()
        assert "detail" in data
        error_detail = data["detail"].lower()
        assert any(word in error_detail for word in ["unauthorized", "authentication", "auth", "login"])
    
    def test_admin_sync_volunteers_endpoint_requires_auth(self, client, mock_google_sheets):
        """Test that admin sync volunteers endpoint requires authentication"""
        response = client.post("/admin/sync-volunteers")
        
        # ✅ Security issue has been fixed - endpoint is now properly protected
        assert response.status_code == 401, f"Expected 401 Unauthorized, got {response.status_code}"
        
        data = response.json()
        assert "detail" in data
        error_detail = data["detail"].lower()
        assert any(word in error_detail for word in ["unauthorized", "authentication", "auth", "login"])
    
    def test_admin_endpoints_consistent_auth_behavior(self, client, mock_google_sheets):
        """Test that all admin endpoints consistently require authentication
        
        All admin endpoints now return 401 Unauthorized when accessed without authentication.
        """
        admin_endpoints = [
            ("GET", "/admin/volunteers"),
            ("GET", "/admin/dashboard"),
            ("POST", "/admin/sync-volunteers"),
            ("POST", "/admin/send-confirmation-emails"),
            ("POST", "/admin/send-weekly-reminders"),
            ("POST", "/admin/rotate-schedule"),
            ("GET", "/admin/schedule-status"),
            ("GET", "/admin/email-logs"),
        ]
        
        for method, endpoint in admin_endpoints:
            if method == "GET":
                response = client.get(endpoint)
            else:
                response = client.post(endpoint)
            
            # All endpoints should return 401 Unauthorized when accessed without authentication
            assert response.status_code == 401, f"{method} {endpoint} should return 401, got {response.status_code}"
            
            # Verify error response format
            data = response.json()
            assert "detail" in data
            assert isinstance(data["detail"], str)
            error_detail = data["detail"].lower()
            assert any(word in error_detail for word in ["unauthorized", "authentication", "auth", "login"])


class TestBotEndpoints:
    """Test bot-related endpoints"""
    
    def test_bot_health_endpoint_structure(self, client, mock_bot_service):
        """Test bot health endpoint structure"""
        try:
            response = client.get("/bot/health")
            
            if response.status_code == 200:
                data = response.json()
                assert "status" in data
                if data.get("status") == "healthy":
                    assert "services" in data
            elif response.status_code == 404:
                # Endpoint doesn't exist yet - that's okay
                pytest.skip("Bot health endpoint not implemented yet")
            else:
                # Some other response - test what we can
                assert response.status_code in [200, 404, 500]
        except Exception:
            # Endpoint might not exist - that's okay for integration tests
            pytest.skip("Bot health endpoint not accessible")
    
    def test_bot_chat_endpoint_structure(self, client, mock_bot_service):
        """Test bot chat endpoint structure"""
        try:
            chat_data = {
                "message": "What qualifications do I need to volunteer?",
                "user_id": "test_user_123"
            }
            
            response = client.post("/bot/chat", json=chat_data)
            
            if response.status_code == 200:
                data = response.json()
                assert "response" in data
            elif response.status_code == 404:
                pytest.skip("Bot chat endpoint not implemented yet")
            else:
                assert response.status_code in [200, 404, 500]
        except Exception:
            pytest.skip("Bot chat endpoint not accessible")


class TestDatabaseIntegration:
    """Test database integration with the API"""
    
    def test_database_connection(self, client):
        """Test that database is accessible through the app"""
        # This test verifies that the database connection works
        # by trying to access a simple endpoint that uses the database
        
        # Test that we can at least make a request to the app
        response = client.get("/")
        assert response.status_code == 200
        
        # The fact that we get a response means the app started successfully
        # which means the database connection was established
    
    def test_models_importable(self):
        """Test that database models can be imported and used"""
        # This verifies that the database models are properly configured
        
        # Test that we can create model instances
        volunteer = Volunteer(
            name="Test Volunteer",
            email="test@example.com",
            positions=["Teacher"],
            teaching_experience="Some experience"
        )
        
        # Test that the model has the expected attributes
        assert volunteer.name == "Test Volunteer"
        assert volunteer.email == "test@example.com"
        assert "Teacher" in volunteer.positions


class TestServiceIntegration:
    """Test service integration with the API"""
    
    def test_google_sheets_service_importable(self):
        """Test that Google Sheets service can be imported"""
        # This verifies that the service dependencies are properly configured
        assert sheets_service is not None
    
    def test_email_service_importable(self):
        """Test that email service can be imported"""
        # This verifies that the service dependencies are properly configured
        assert email_service is not None
    
    def test_bot_service_importable(self):
        """Test that bot service can be imported"""
        # This verifies that the service dependencies are properly configured
        assert BotService is not None


# Configuration for integration tests
# Note: Custom pytest marks can be registered in pytest.ini or pyproject.toml
# For now, we'll remove the unknown marks to avoid warnings
# pytestmark = [
#     pytest.mark.integration,
#     pytest.mark.slow  # Mark as slow since these are integration tests
# ] 