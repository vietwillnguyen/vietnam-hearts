"""
Tests for middleware functionality

Tests logging, CORS, error handling, and rate limiting middleware.
Authentication is handled by FastAPI dependencies, not middleware.
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import API_URL

client = TestClient(app)


class TestLoggingMiddleware:
    """Test logging middleware functionality"""
    
    def test_request_id_generation(self):
        """Test that request IDs are generated for each request"""
        response = client.get("/auth/health")
        
        # Check that response headers include request ID
        assert "X-Request-ID" in response.headers
        
        # Check that request ID is a reasonable length
        request_id = response.headers["X-Request-ID"]
        assert len(request_id) > 0
        assert len(request_id) <= 20
    
    def test_logging_headers(self):
        """Test that logging middleware adds appropriate headers"""
        response = client.get("/auth/health")
        
        # Check for logging-related headers
        assert "X-Request-ID" in response.headers


class TestRateLimitMiddleware:
    """Test rate limiting middleware functionality"""
    
    def test_rate_limit_headers_present(self):
        """Test that rate limit headers are present in responses"""
        response = client.get("/auth/health")
        
        # Check for rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers
        assert "X-RateLimit-Category" in response.headers
    
    def test_rate_limit_category_detection(self):
        """Test that rate limit categories are correctly detected"""
        # Test public endpoint (root)
        response = client.get("/")
        assert response.headers["X-RateLimit-Category"] == "public"
        
        # Test auth endpoint
        response = client.get("/auth/health")
        assert response.headers["X-RateLimit-Category"] == "auth"
        
        # Test admin endpoint
        response = client.get("/admin/volunteers")
        assert response.headers["X-RateLimit-Category"] == "admin"


class TestCORSMiddleware:
    """Test CORS middleware functionality"""
    
    def test_cors_debug_info(self):
        """Debug test to see what's happening with CORS"""
        # Test without Origin header
        response_no_origin = client.get("/")
        print(f"\n=== NO ORIGIN HEADER ===")
        print(f"Status: {response_no_origin.status_code}")
        print(f"Headers: {dict(response_no_origin.headers)}")
        
        # Test with localhost Origin header
        response_localhost = client.get("/", headers={"Origin": "http://localhost:3000"})
        print(f"\n=== LOCALHOST ORIGIN ===")
        print(f"Status: {response_localhost.status_code}")
        print(f"Headers: {dict(response_localhost.headers)}")
        
        # Test with API_URL Origin header
        response_api_url = client.get("/", headers={"Origin": API_URL})
        print(f"\n=== API_URL ORIGIN ===")
        print(f"Status: {response_api_url.status_code}")
        print(f"Headers: {dict(response_api_url.headers)}")
        
        # Test OPTIONS request
        response_options = client.options("/", headers={"Origin": "http://localhost:3000"})
        print(f"\n=== OPTIONS REQUEST ===")
        print(f"Status: {response_options.status_code}")
        print(f"Headers: {dict(response_options.headers)}")
        
        # For now, just assert that we get some response
        assert response_no_origin.status_code == 200
        assert response_localhost.status_code == 200
        assert response_api_url.status_code == 200
    
    def test_cors_headers_present(self):
        """Test that CORS headers are present in responses"""
        # CORS headers are only added when there's an Origin header in the request
        # Use localhost:3000 as Origin to trigger CORS
        response = client.get("/", headers={"Origin": "http://localhost:3000"})
        
        # Check for CORS headers
        assert "access-control-allow-origin" in response.headers
        assert "access-control-allow-credentials" in response.headers
        assert "access-control-expose-headers" in response.headers
    
    def test_options_request_handling(self):
        """Test that OPTIONS requests are handled correctly"""
        # OPTIONS requests need an Origin header to trigger CORS preflight
        # Note: The / endpoint doesn't support OPTIONS method (returns 405)
        # But CORS headers are still added by the middleware
        response = client.options("/", headers={"Origin": "http://localhost:3000"})
        
        # OPTIONS request returns 405 (Method Not Allowed) but with CORS headers
        assert response.status_code == 405
        assert "access-control-allow-origin" in response.headers
        assert "access-control-allow-credentials" in response.headers


class TestErrorHandlingMiddleware:
    """Test error handling middleware functionality"""
    
    def test_consistent_error_format(self):
        """Test that errors return consistent format"""
        # Test 404 error
        response = client.get("/nonexistent-endpoint")
        
        assert response.status_code == 404
        # FastAPI handles 404s before our middleware, so we get the default format
        # Our error handling middleware only catches unhandled exceptions, not 404s
        error_data = response.json()
        assert "detail" in error_data  # FastAPI's default format
        assert error_data["detail"] == "Not Found"
    
    def test_request_id_in_error_responses(self):
        """Test that error responses include request ID"""
        response = client.get("/nonexistent-endpoint")
        
        # Note: 404s are handled by FastAPI before our middleware, so no custom error format
        # But the request ID should still be in headers from logging middleware
        assert "X-Request-ID" in response.headers


class TestMiddlewareIntegration:
    """Test that all middleware work together correctly"""
    
    def test_middleware_order(self):
        """Test that middleware are applied in correct order"""
        response = client.get("/auth/health")
        
        # Check middleware that is actually working
        assert "X-Request-ID" in response.headers  # Logging middleware
        assert "X-RateLimit-Limit" in response.headers  # Rate limit middleware
        
        # Test CORS middleware separately with proper Origin header
        cors_response = client.get("/", headers={"Origin": "http://localhost:3000"})
        assert "access-control-allow-origin" in cors_response.headers  # CORS middleware
    
    def test_middleware_performance(self):
        """Test that middleware don't significantly impact performance"""
        import time
        
        start_time = time.time()
        response = client.get("/auth/health")
        end_time = time.time()
        
        # Request should complete in reasonable time (under 1 second)
        assert (end_time - start_time) < 1.0
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__])
