#!/usr/bin/env python3
"""
Scheduler API Test Script for Vietnam Hearts

This script tests the scheduler API endpoints using Google Cloud authentication.
It uses the service account: auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com

Usage:
    python tests/test_api.py [endpoint_name]
    
Examples:
    python tests/test_api.py health
    python tests/test_api.py all
    python tests/test_api.py send-confirmation-emails
"""

import os
import sys
import json
import time
import requests
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Add the app directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# Load environment variables
load_dotenv()

class SchedulerAPITester:
    """Test class for scheduler API endpoints"""
    
    def __init__(self):
        self.base_url = os.getenv("API_BASE_URL", "http://localhost:8080")
        self.service_account = "auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com"
        self.api_prefix = "/api"
        self.session = requests.Session()
        
        # Configure session headers
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "SchedulerAPITester/1.0"
        })
        
        print(f"🔧 Scheduler API Tester")
        print(f"Base URL: {self.base_url}")
        print(f"Service Account: {self.service_account}")
        print(f"API Prefix: {self.api_prefix}")
        print("=" * 60)
    
    def get_auth_token(self) -> Optional[str]:
        """
        Get authentication token using gcloud CLI
        Returns OIDC token for the service account
        """
        try:
            print("🔑 Getting authentication token...")
            
            # Try using service account credentials file first
            credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if credentials_file and os.path.exists(credentials_file):
                print(f"📁 Using service account credentials file: {credentials_file}")
                # Use gcloud with service account key
                cmd = [
                    "gcloud", "auth", "activate-service-account",
                    "--key-file", credentials_file
                ]
                subprocess.run(cmd, capture_output=True, check=True)
            
            # Use gcloud to get OIDC token for the service account
            # Try using the API URL as audience first, then fall back to OAuth client ID
            oauth_client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
            
            # Try API URL as audience first
            cmd = [
                "gcloud", "auth", "print-identity-token",
                f"--audiences={oauth_client_id}"
            ]
            
            print(f"🔄 Trying audience: {oauth_client_id}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            token = result.stdout.strip()
            if token:
                print("✅ Authentication token obtained successfully")
                print(f"   Audience: {self.base_url}")
                return token
            else:
                print("❌ Failed to get authentication token")
                return None
                
        except subprocess.CalledProcessError as e:
            print(f"❌ gcloud command failed: {e}")
            print(f"Error output: {e.stderr}")
            print("\n💡 Make sure you have:")
            print("1. gcloud CLI installed and configured")
            print("2. Authenticated with: gcloud auth login")
            print("3. Set the correct project: gcloud config set project refined-vector-457419")
            print("4. Set GOOGLE_OAUTH_CLIENT_ID in your .env file")
            print("5. Set GOOGLE_APPLICATION_CREDENTIALS to your service account key file")
            return None
        except Exception as e:
            print(f"❌ Unexpected error getting token: {e}")
            return None
    
    def make_request(self, endpoint: str, method: str = "POST", data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Make authenticated request to scheduler endpoint
        
        Args:
            endpoint: Endpoint name (e.g., 'health', 'send-confirmation-emails')
            method: HTTP method (GET, POST)
            data: Request data for POST requests
            
        Returns:
            Response data as dictionary
        """
        # Get authentication token
        token = self.get_auth_token()
        if not token:
            return {"error": "Failed to get authentication token"}
        
        # Set authorization header
        self.session.headers["Authorization"] = f"Bearer {token}"
        
        # Build URL
        url = f"{self.base_url}{self.api_prefix}/{endpoint}"
        
        try:
            print(f"\n🌐 Making {method} request to: {url}")
            time.sleep(1)
            if method.upper() == "GET":
                response = self.session.get(url)
            elif method.upper() == "POST":
                response = self.session.post(url, json=data or {})
            else:
                return {"error": f"Unsupported method: {method}"}
            
            print(f"📊 Response Status: {response.status_code}")
            
            # Try to parse JSON response
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
        except requests.exceptions.Timeout:
            error_msg = "❌ Request timeout"
            print(error_msg)
            return {"error": error_msg}
        except Exception as e:
            error_msg = f"❌ Request failed: {str(e)}"
            print(error_msg)
            return {"error": error_msg}
    
    def test_health_check(self) -> bool:
        """Test the health check endpoint"""
        print("\n🏥 Testing Health Check Endpoint")
        print("-" * 40)
        
        result = self.make_request("health", method="GET")
        
        if "error" in result:
            print(f"❌ Health check failed: {result['error']}")
            return False
        
        if result.get("status") == "healthy":
            print("✅ Health check passed!")
            print(f"   Google Sheets connectivity: {result.get('google_sheets_connectivity', 'unknown')}")
            print(f"   Submissions count: {result.get('submissions_count', 0)}")
            return True
        else:
            print(f"❌ Health check failed: {result}")
            return False
    
    def test_send_confirmation_emails(self) -> bool:
        """Test the send confirmation emails endpoint"""
        print("\n📧 Testing Send Confirmation Emails Endpoint")
        print("-" * 40)
        
        result = self.make_request("send-confirmation-emails")
        
        if "error" in result:
            print(f"❌ Send confirmation emails failed: {result['error']}")
            return False
        
        if result.get("status") == "success":
            print("✅ Send confirmation emails passed!")
            print(f"   Message: {result.get('message', 'No message')}")
            return True
        else:
            print(f"❌ Send confirmation emails failed: {result}")
            return False
    
    def test_sync_volunteers(self) -> bool:
        """Test the sync volunteers endpoint"""
        print("\n🔄 Testing Sync Volunteers Endpoint")
        print("-" * 40)
        
        result = self.make_request("sync-volunteers")
        
        if "error" in result:
            print(f"❌ Sync volunteers failed: {result['error']}")
            return False
        
        status = result.get("status")
        if status in ["success", "partial_success"]:
            print(f"✅ Sync volunteers {'passed' if status == 'success' else 'partially succeeded'}!")
            print(f"   Message: {result.get('message', 'No message')}")
            if result.get("details"):
                print(f"   Details: {json.dumps(result['details'], indent=2)}")
            return True
        else:
            print(f"❌ Sync volunteers failed: {result}")
            return False
    
    def test_send_weekly_reminders(self) -> bool:
        """Test the send weekly reminders endpoint"""
        print("\n📅 Testing Send Weekly Reminders Endpoint")
        print("-" * 40)
        
        result = self.make_request("send-weekly-reminders")
        
        if "error" in result:
            print(f"❌ Send weekly reminders failed: {result['error']}")
            return False
        
        if result.get("status") == "success":
            print("✅ Send weekly reminders passed!")
            print(f"   Message: {result.get('message', 'No message')}")
            return True
        else:
            print(f"❌ Send weekly reminders failed: {result}")
            return False
    
    def test_rotate_schedule(self) -> bool:
        """Test the rotate schedule endpoint"""
        print("\n🔄 Testing Rotate Schedule Endpoint")
        print("-" * 40)
        
        result = self.make_request("rotate-schedule")
        
        if "error" in result:
            print(f"❌ Rotate schedule failed: {result['error']}")
            return False
        
        if result.get("status") == "success":
            print("✅ Rotate schedule passed!")
            print(f"   Message: {result.get('message', 'No message')}")
            return True
        else:
            print(f"❌ Rotate schedule failed: {result}")
            return False
    
    def test_all_endpoints(self) -> Dict[str, bool]:
        """Test all scheduler endpoints"""
        print("\n🚀 Testing All Scheduler Endpoints")
        print("=" * 60)
        
        results = {}
        
        # Test health check first
        results["health"] = self.test_health_check()
        
        # Add delay between requests to avoid overwhelming the server
        time.sleep(1)
        
        # Test other endpoints
        results["send_confirmation_emails"] = self.test_send_confirmation_emails()
        time.sleep(1)
        
        results["sync_volunteers"] = self.test_sync_volunteers()
        time.sleep(1)
        
        results["send_weekly_reminders"] = self.test_send_weekly_reminders()
        time.sleep(1)
        
        results["rotate_schedule"] = self.test_rotate_schedule()
        
        # Print summary
        print("\n📊 Test Results Summary")
        print("=" * 60)
        passed = sum(results.values())
        total = len(results)
        
        for endpoint, success in results.items():
            status = "✅ PASS" if success else "❌ FAIL"
            print(f"{endpoint.replace('_', ' ').title()}: {status}")
        
        print(f"\nOverall: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All tests passed!")
        else:
            print("⚠️  Some tests failed. Check the output above for details.")
        
        return results


def main():
    """Main function to run tests"""
    if len(sys.argv) < 2:
        print("Usage: python test_api.py [endpoint_name]")
        print("\nAvailable endpoints:")
        print("  health                    - Health check")
        print("  send-confirmation-emails  - Send confirmation emails")
        print("  sync-volunteers          - Sync volunteers from Google Sheets")
        print("  send-weekly-reminders    - Send weekly reminder emails")
        print("  rotate-schedule          - Rotate schedule sheets")
        print("  all                      - Test all endpoints")
        sys.exit(1)
    
    endpoint = sys.argv[1].lower()
    
    # Create tester instance
    tester = SchedulerAPITester()
    
    # Run tests based on endpoint
    if endpoint == "all":
        tester.test_all_endpoints()
    elif endpoint == "health":
        tester.test_health_check()
    elif endpoint == "send-confirmation-emails":
        tester.test_send_confirmation_emails()
    elif endpoint == "sync-volunteers":
        tester.test_sync_volunteers()
    elif endpoint == "send-weekly-reminders":
        tester.test_send_weekly_reminders()
    elif endpoint == "rotate-schedule":
        tester.test_rotate_schedule()
    else:
        print(f"❌ Unknown endpoint: {endpoint}")
        print("Available endpoints: health, send-confirmation-emails, sync-volunteers, send-weekly-reminders, rotate-schedule, all")
        sys.exit(1)


if __name__ == "__main__":
    main() 