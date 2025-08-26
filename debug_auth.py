#!/usr/bin/env python3
"""
Debug Authentication Flow

This script helps debug the authentication issues by testing the flow step by step.
"""

import requests
import json
from urllib.parse import urljoin

# Configuration
BASE_URL = "http://localhost:8080"
API_URL = f"{BASE_URL}"

def test_health_endpoint():
    """Test the health endpoint to ensure the API is running"""
    print("🔍 Testing health endpoint...")
    try:
        response = requests.get(f"{API_URL}/health")
        print(f"✅ Health check: {response.status_code}")
        if response.status_code == 200:
            print(f"   Response: {response.json()}")
        return True
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False

def test_public_endpoints():
    """Test public endpoints that don't require authentication"""
    print("\n🔍 Testing public endpoints...")
    
    endpoints = [
        "/public/health",
        "/auth/health",
        "/bot/chat"
    ]
    
    for endpoint in endpoints:
        try:
            response = requests.get(f"{API_URL}{endpoint}")
            print(f"✅ {endpoint}: {response.status_code}")
        except Exception as e:
            print(f"❌ {endpoint}: {e}")

def test_protected_endpoints():
    """Test protected endpoints that require authentication"""
    print("\n🔍 Testing protected endpoints (should fail without auth)...")
    
    endpoints = [
        "/admin/dashboard",
        "/admin/volunteers",
        "/auth/me"
    ]
    
    for endpoint in endpoints:
        try:
            response = requests.get(f"{API_URL}{endpoint}")
            print(f"✅ {endpoint}: {response.status_code} (expected 401)")
            if response.status_code != 401:
                print(f"   ⚠️  Unexpected status code!")
        except Exception as e:
            print(f"❌ {endpoint}: {e}")

def test_oauth_flow():
    """Test the OAuth sign-in flow"""
    print("\n🔍 Testing OAuth sign-in flow...")
    
    try:
        # Step 1: Initiate Google sign-in
        signin_data = {"redirect_to": f"{API_URL}/auth/callback"}
        response = requests.post(f"{API_URL}/auth/signin/google", json=signin_data)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ OAuth sign-in initiated: {result.get('message', 'Success')}")
            
            if 'auth_url' in result:
                print(f"   🔗 Auth URL: {result['auth_url']}")
                print("   📝 To complete authentication:")
                print("   1. Open the auth URL in your browser")
                print("   2. Complete Google sign-in")
                print("   3. You'll be redirected to the callback")
                print("   4. Check the browser console for tokens")
            else:
                print(f"   ❌ No auth URL in response: {result}")
        else:
            print(f"❌ OAuth sign-in failed: {response.status_code}")
            print(f"   Response: {response.text}")
            
    except Exception as e:
        print(f"❌ OAuth sign-in test failed: {e}")

def test_with_token(token):
    """Test protected endpoints with a valid token"""
    print(f"\n🔍 Testing protected endpoints with token: {token[:20]}...")
    
    headers = {"Authorization": f"Bearer {token}"}
    
    endpoints = [
        "/admin/dashboard",
        "/admin/volunteers",
        "/auth/me"
    ]
    
    for endpoint in endpoints:
        try:
            response = requests.get(f"{API_URL}{endpoint}", headers=headers)
            print(f"✅ {endpoint}: {response.status_code}")
            
            if response.status_code == 200:
                print(f"   ✅ Access granted!")
            elif response.status_code == 401:
                print(f"   ❌ Still unauthorized - token might be invalid")
            elif response.status_code == 403:
                print(f"   ❌ Forbidden - user might not have admin access")
            else:
                print(f"   ⚠️  Unexpected status: {response.status_code}")
                
        except Exception as e:
            print(f"❌ {endpoint}: {e}")

def main():
    """Run all tests"""
    print("🚀 Vietnam Hearts Authentication Debug Tool")
    print("=" * 50)
    
    # Test basic functionality
    if not test_health_endpoint():
        print("❌ API is not running. Please start the server first.")
        return
    
    # Test public endpoints
    test_public_endpoints()
    
    # Test protected endpoints (should fail)
    test_protected_endpoints()
    
    # Test OAuth flow
    test_oauth_flow()
    
    # Ask for token to test authenticated access
    print("\n" + "=" * 50)
    print("🔑 To test authenticated access:")
    print("1. Complete the OAuth flow in your browser")
    print("2. Check the browser console for the access_token")
    print("3. Run this script again with: python debug_auth.py <token>")
    
    # If token provided as argument, test with it
    import sys
    if len(sys.argv) > 1:
        token = sys.argv[1]
        test_with_token(token)

if __name__ == "__main__":
    main()
