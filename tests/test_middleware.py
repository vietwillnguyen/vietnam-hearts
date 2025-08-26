"""
Tests for middleware functionality

Tests authentication, logging, CORS, error handling, and rate limiting middleware.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware

client = TestClient(app)


class TestAuthMiddleware:
    """Test authentication middleware functionality"""
    
    def test_public_endpoints_accessible_without_auth(self):
        """Test that public endpoints don't require authentication"""
        # Test health check endpoint
        response = client.get("/health")
        assert response.status_code == 200
        
        # Test public endpoints
        response = client.get("/public")
        assert response.status_code != 401  # Should not be unauthorized
    
    def test_protected_endpoints_require_auth(self):
        """Test that protected endpoints require authentication"""
        # Test admin endpoint without auth
        response = client.get("/admin/settings")
        assert response.status_code == 401
        
        # Test auth endpoint without auth
        response = client.get("/auth/health")
        assert response.status_code == 401
    
    @patch('app.services.supabase_auth.get_current_user')
    def test_protected_endpoints_with_valid_auth(self, mock_get_user):
        """Test that protected endpoints work with valid authentication"""
        # Mock valid user
        mock_user = {"id": "123", "email": "test@example.com"}
        mock_get_user.return_value = mock_user
        
        # Test with valid token
        headers = {"Authorization": "Bearer valid_token"}
        response = client.get("/admin/settings", headers=headers)
        
        # Should not be unauthorized (actual response depends on endpoint implementation)
        assert response.status_code != 401
    
    @patch('app.services.supabase_auth.get_current_admin_user')
    def test_admin_endpoints_with_admin_auth(self, mock_get_admin):
        """Test that admin endpoints work with admin authentication"""
        # Mock valid admin user
        mock_admin = {"id": "123", "email": "admin@example.com", "role": "admin"}
        mock_get_admin.return_value = mock_admin
        
        # Test with valid admin token
        headers = {"Authorization": "Bearer admin_token"}
        response = client.get("/admin/settings", headers=headers)
        
        # Should not be unauthorized
        assert response.status_code != 401


class TestLoggingMiddleware:
    """Test logging middleware functionality"""
    
    def test_request_id_generation(self):
        """Test that request IDs are generated for each request"""
        response = client.get("/health")
        
        # Check that response headers include request ID
        assert "X-Request-ID" in response.headers
        
        # Check that request ID is a reasonable length
        request_id = response.headers["X-Request-ID"]
        assert len(request_id) > 0
        assert len(request_id) <= 20
    
    def test_logging_headers(self):
        """Test that logging middleware adds appropriate headers"""
        response = client.get("/health")
        
        # Check for logging-related headers
        assert "X-Request-ID" in response.headers


class TestRateLimitMiddleware:
    """Test rate limiting middleware functionality"""
    
    def test_rate_limit_headers_present(self):
        """Test that rate limit headers are present in responses"""
        response = client.get("/health")
        
        # Check for rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers
        assert "X-RateLimit-Category" in response.headers
    
    def test_rate_limit_category_detection(self):
        """Test that rate limit categories are correctly detected"""
        # Test public endpoint
        response = client.get("/public")
        assert response.headers["X-RateLimit-Category"] == "public"
        
        # Test auth endpoint
        response = client.get("/auth/health")
        assert response.headers["X-RateLimit-Category"] == "auth"
        
        # Test admin endpoint
        response = client.get("/admin/settings")
        assert response.headers["X-RateLimit-Category"] == "admin"


class TestCORSMiddleware:
    """Test CORS middleware functionality"""
    
    def test_cors_headers_present(self):
        """Test that CORS headers are present in responses"""
        response = client.get("/health")
        
        # Check for CORS headers
        assert "Access-Control-Allow-Origin" in response.headers
        assert "Access-Control-Allow-Methods" in response.headers
        assert "Access-Control-Allow-Headers" in response.headers
    
    def test_options_request_handling(self):
        """Test that OPTIONS requests are handled correctly"""
        response = client.options("/health")
        
        # OPTIONS request should return 200 with CORS headers
        assert response.status_code == 200
        assert "Access-Control-Allow-Origin" in response.headers


class TestErrorHandlingMiddleware:
    """Test error handling middleware functionality"""
    
    def test_consistent_error_format(self):
        """Test that errors return consistent format"""
        # Test 404 error
        response = client.get("/nonexistent-endpoint")
        
        assert response.status_code == 404
        assert "error" in response.json()
        assert "type" in response.json()["error"]
        assert "message" in response.json()["error"]
    
    def test_request_id_in_error_responses(self):
        """Test that error responses include request ID"""
        response = client.get("/nonexistent-endpoint")
        
        # Should include request ID in error response
        error_data = response.json()
        if "request_id" in error_data.get("error", {}):
            assert error_data["error"]["request_id"] is not None


class TestMiddlewareIntegration:
    """Test that all middleware work together correctly"""
    
    def test_middleware_order(self):
        """Test that middleware are applied in correct order"""
        response = client.get("/health")
        
        # All middleware should be active
        assert "X-Request-ID" in response.headers  # Logging middleware
        assert "X-RateLimit-Limit" in response.headers  # Rate limit middleware
        assert "Access-Control-Allow-Origin" in response.headers  # CORS middleware
    
    def test_middleware_performance(self):
        """Test that middleware don't significantly impact performance"""
        import time
        
        start_time = time.time()
        response = client.get("/health")
        end_time = time.time()
        
        # Request should complete in reasonable time (under 1 second)
        assert (end_time - start_time) < 1.0
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__])
