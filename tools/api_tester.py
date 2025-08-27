#!/usr/bin/env python3
"""
CLI Tool for Manual API Testing

This tool provides a command-line interface for testing Vietnam Hearts API endpoints
manually. It's separate from the test suite and designed for development/debugging.

Usage:
    python tools/api_tester.py [endpoint_name] [--auth-type=gcloud|supabase]
    
Examples:
    python tools/api_tester.py health
    python tools/api_tester.py all
    python tools/api_tester.py send-confirmation-emails
    python tools/api_tester.py admin-dashboard --auth-type=supabase
"""

import os
import sys
import json
import time
import requests
import subprocess
import argparse
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Add the app directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# Load environment variables
load_dotenv()

class APITester:
    """CLI tool for manual API testing"""
    
    def __init__(self, auth_type: str = "supabase"):
        self.base_url = os.getenv("API_URL", "https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app")
        self.auth_type = auth_type
        self.session = requests.Session()
        
        # Configure session headers
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "APITester/1.0"
        })
        
        print(f"🔧 Vietnam Hearts API Tester")
        print(f"Base URL: {self.base_url}")
        print(f"Auth Type: {auth_type}")
        print("=" * 60)
    
    def get_auth_token(self) -> Optional[str]:
        """Get authentication token"""
        if self.auth_type == "supabase":
            return self._get_supabase_token()
        else:
            return self._get_gcloud_token()
    
    def _get_supabase_token(self) -> Optional[str]:
        """Get Supabase service role key"""
        try:
            print("🔑 Getting Supabase authentication token...")
            service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            if not service_role_key:
                print("❌ SUPABASE_SERVICE_ROLE_KEY not set")
                return None
            
            print("✅ Supabase service role key obtained successfully")
            return service_role_key
        except Exception as e:
            print(f"❌ Error getting Supabase token: {e}")
            return None
    
    def _get_gcloud_token(self) -> Optional[str]:
        """Get gcloud OIDC token"""
        try:
            print("🔑 Getting gcloud authentication token...")
            
            # Try using service account credentials file
            credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if credentials_file and os.path.exists(credentials_file):
                print(f"📁 Using service account credentials file: {credentials_file}")
                cmd = [
                    "gcloud", "auth", "activate-service-account",
                    "--key-file", credentials_file
                ]
                subprocess.run(cmd, capture_output=True, check=True)
            
            # Get OIDC token
            oauth_client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
            cmd = [
                "gcloud", "auth", "print-identity-token",
                f"--audiences={oauth_client_id}"
            ]
            
            print(f"🔄 Getting token for audience: {oauth_client_id}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            token = result.stdout.strip()
            if token:
                print("✅ gcloud authentication token obtained successfully")
                return token
            else:
                print("❌ Failed to get gcloud authentication token")
                return None
                
        except subprocess.CalledProcessError as e:
            print(f"❌ gcloud command failed: {e}")
            print(f"Error output: {e.stderr}")
            return None
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return None
    
    def make_request(self, endpoint: str, method: str = "POST", data: Dict[str, Any] = None, use_auth: bool = True) -> Dict[str, Any]:
        """Make authenticated request to API endpoint"""
        # Get authentication token if needed
        if use_auth:
            token = self.get_auth_token()
            if not token:
                return {"error": "Failed to get authentication token"}
            
            # Set appropriate auth header
            if self.auth_type == "supabase":
                self.session.headers["apikey"] = token
                self.session.headers.pop("Authorization", None)
            else:
                self.session.headers["Authorization"] = f"Bearer {token}"
                self.session.headers.pop("apikey", None)
        else:
            # Remove all auth headers for public endpoints
            self.session.headers.pop("Authorization", None)
            self.session.headers.pop("apikey", None)
        
        # Build URL
        url = f"{self.base_url}/admin/{endpoint}"
        
        try:
            print(f"\n🌐 Making {method} request to: {url}")
            time.sleep(1)  # Rate limiting
            
            if method.upper() == "GET":
                response = self.session.get(url)
            elif method.upper() == "POST":
                response = self.session.post(url, json=data or {})
            else:
                return {"error": f"Unsupported method: {method}"}
            
            print(f"📊 Response Status: {response.status_code}")
            
            # Parse response
            try:
                response_data = response.json()
                print(f"📄 Response Data: {json.dumps(response_data, indent=2)}")
                return response_data
            except json.JSONDecodeError:
                print(f"📄 Response Text: {response.text}")
                return {"status_code": response.status_code, "text": response.text}
                
        except requests.exceptions.ConnectionError:
            error_msg = f"❌ Connection error: Could not connect to {self.base_url}"
            print(error_msg)
            return {"error": error_msg}
        except Exception as e:
            error_msg = f"❌ Request failed: {str(e)}"
            print(error_msg)
            return {"error": error_msg}
    
    def test_endpoint(self, endpoint: str) -> bool:
        """Test a specific endpoint"""
        print(f"\n🧪 Testing {endpoint} endpoint")
        print("-" * 40)
        
        # Map endpoints to test methods
        endpoint_tests = {
            "health": lambda: self.make_request("health", method="GET", use_auth=True),
            "send-confirmation-emails": lambda: self.make_request("send-confirmation-emails", use_auth=True),
            "sync-volunteers": lambda: self.make_request("sync-volunteers", use_auth=True),
            "send-weekly-reminders": lambda: self.make_request("send-weekly-reminders", use_auth=True),
            "rotate-schedule": lambda: self.make_request("rotate-schedule", use_auth=True),
            "schedule-status": lambda: self.make_request("schedule-status", method="GET", use_auth=True),
        }
        
        if endpoint not in endpoint_tests:
            print(f"❌ Unknown endpoint: {endpoint}")
            return False
        
        result = endpoint_tests[endpoint]()
        
        if "error" in result:
            print(f"❌ {endpoint} failed: {result['error']}")
            return False
        
        if result.get("status") in ["success", "healthy"]:
            print(f"✅ {endpoint} passed!")
            return True
        else:
            print(f"❌ {endpoint} failed: {result}")
            return False
    
    def test_all_endpoints(self) -> Dict[str, bool]:
        """Test all available endpoints"""
        print("\n🚀 Testing All API Endpoints")
        print("=" * 60)
        
        endpoints = [
            "health",
            "send-confirmation-emails", 
            "sync-volunteers",
            "send-weekly-reminders",
            "rotate-schedule",
            "schedule-status"
        ]
        
        results = {}
        for endpoint in endpoints:
            results[endpoint] = self.test_endpoint(endpoint)
            time.sleep(1)  # Rate limiting
        
        # Print summary
        print("\n📊 Test Results Summary")
        print("=" * 60)
        passed = sum(results.values())
        total = len(results)
        
        for endpoint, success in results.items():
            status = "✅ PASS" if success else "❌ FAIL"
            print(f"{endpoint.replace('-', ' ').title()}: {status}")
        
        print(f"\nOverall: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All tests passed!")
        else:
            print("⚠️  Some tests failed. Check the output above for details.")
        
        return results


def main():
    """Main CLI function"""
    parser = argparse.ArgumentParser(description="Test Vietnam Hearts API endpoints")
    parser.add_argument("endpoint", help="Endpoint to test or 'all' for all endpoints")
    parser.add_argument("--auth-type", choices=["gcloud", "supabase"], default="supabase",
                       help="Authentication type to use (default: supabase)")
    
    args = parser.parse_args()
    endpoint = args.endpoint.lower()
    auth_type = args.auth_type
    
    # Create tester instance
    tester = APITester(auth_type=auth_type)
    
    # Run tests
    if endpoint == "all":
        tester.test_all_endpoints()
    else:
        success = tester.test_endpoint(endpoint)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
