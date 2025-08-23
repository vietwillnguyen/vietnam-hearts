#!/usr/bin/env python3
"""
Scheduler API Test Script for Vietnam Hearts

This script tests the scheduler API endpoints using either:
1. gcloud authentication (for public API endpoints)
2. Supabase authentication (for admin endpoints)

Usage:
    python tests/test_api.py [endpoint_name] [--auth-type=gcloud|supabase]
    
Examples:
    python tests/test_api.py health
    python tests/test_api.py all
    python tests/test_api.py send-confirmation-emails
    python tests/test_api.py admin-dashboard --auth-type=supabase
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

class SchedulerAPITester:
    """Test class for scheduler API endpoints"""
    
    def __init__(self, auth_type: str = "gcloud"):
        self.base_url = os.getenv("API_URL", "https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app")
        self.service_account = "auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com"
        self.api_prefix = "/admin"
        self.auth_type = auth_type
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
        print(f"Auth Type: {auth_type}")
        print("=" * 60)
    
    def get_auth_token(self) -> Optional[str]:
        """
        Get authentication token using either gcloud or Supabase
        Returns OIDC token for gcloud or JWT token for Supabase
        """
        if self.auth_type == "supabase":
            return self._get_supabase_token()
        else:
            return self._get_gcloud_token()
    
    def _get_gcloud_token(self) -> Optional[str]:
        """
        Get authentication token using gcloud CLI
        Returns OIDC token for the service account
        """
        try:
            print("🔑 Getting gcloud authentication token...")
            
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
                print("✅ gcloud authentication token obtained successfully")
                print(f"   Audience: {self.base_url}")
                return token
            else:
                print("❌ Failed to get gcloud authentication token")
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
            print(f"❌ Unexpected error getting gcloud token: {e}")
            return None
    
    def _get_supabase_token(self) -> Optional[str]:
        """
        Get authentication token using Supabase service role key
        Returns JWT token for the service account
        """
        try:
            print("🔑 Getting Supabase authentication token...")
            
            # For service accounts, we can use the service role key directly
            # or create a JWT token using the service account email
            service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            if not service_role_key:
                print("❌ SUPABASE_SERVICE_ROLE_KEY not set")
                return None
            
            # Return the service role key directly for Supabase native authentication
            print("✅ Supabase service role key obtained successfully")
            return service_role_key
        except Exception as e:
            print(f"❌ Unexpected error getting Supabase token: {e}")
            return None
    
    def make_request(self, endpoint: str, method: str = "POST", data: Dict[str, Any] = None, use_auth: bool = True) -> Dict[str, Any]:
        """
        Make authenticated request to scheduler endpoint
        
        Args:
            endpoint: Endpoint name (e.g., 'health', 'send-confirmation-emails')
            method: HTTP method (GET, POST)
            data: Request data for POST requests
            use_auth: Whether to include authentication headers
            
        Returns:
            Response data as dictionary
        """
        # Get authentication token if needed
        if use_auth:
            token = self.get_auth_token()
            if not token:
                return {"error": "Failed to get authentication token"}
            
            # For Supabase auth, use apikey header instead of Authorization
            if self.auth_type == "supabase":
                self.session.headers["apikey"] = token
                # Remove any existing Authorization header
                self.session.headers.pop("Authorization", None)
            else:
                # For gcloud auth, use Authorization header
                self.session.headers["Authorization"] = f"Bearer {token}"
                # Remove any existing apikey header
                self.session.headers.pop("apikey", None)
        else:
            # Remove all auth headers for public endpoints
            self.session.headers.pop("Authorization", None)
            self.session.headers.pop("apikey", None)
        
        # Build URL
        url = f"{self.base_url}{self.api_prefix}/{endpoint}"
        
        try:
            print(f"\n🌐 Making {method} request to: {url}")
            print(f"🔍 Request Headers: {dict(self.session.headers)}")
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
    
    def make_admin_request(self, endpoint: str, method: str = "GET", data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Make authenticated request to admin endpoint
        
        Args:
            endpoint: Admin endpoint name (e.g., 'dashboard', 'schedule-status')
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
        
        # Build URL for admin endpoints
        url = f"{self.base_url}/admin/{endpoint}"
        
        try:
            print(f"\n🌐 Making {method} request to admin endpoint: {url}")
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
        
        result = self.make_request("health", method="GET", use_auth=True)
        
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
        
        result = self.make_request("send-confirmation-emails", use_auth=True)
        
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
        
        result = self.make_request("sync-volunteers", use_auth=True)
        
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
        
        result = self.make_request("send-weekly-reminders", use_auth=True)
        
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
        
        result = self.make_request("rotate-schedule", use_auth=True)
        
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
    
    def test_admin_schedule_status(self) -> bool:
        """Test the admin schedule status endpoint"""
        print("\n📊 Testing Admin Schedule Status Endpoint")
        print("-" * 40)
        
        result = self.make_admin_request("schedule-status", method="GET")
        
        if "error" in result:
            print(f"❌ Admin schedule status failed: {result['error']}")
            return False
        
        if result.get("status") == "success":
            print("✅ Admin schedule status passed!")
            details = result.get("details", {})
            print(f"   Total sheets: {details.get('total_sheets', 0)}")
            print(f"   Visible sheets: {details.get('visible_sheets', 0)}")
            print(f"   Hidden sheets: {details.get('hidden_sheets', 0)}")
            print(f"   Display weeks: {details.get('display_weeks_count', 0)}")
            return True
        else:
            print(f"❌ Admin schedule status failed: {result}")
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
        
        # Test admin endpoints if using Supabase auth
        if self.auth_type == "supabase":
            time.sleep(1)
            results["admin_schedule_status"] = self.test_admin_schedule_status()
        
        # Test bot endpoints
        time.sleep(1)
        bot_results = self.test_bot_endpoints()
        results.update(bot_results)
        
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

    def test_bot_endpoints(self) -> Dict[str, bool]:
        """Test bot-related endpoints"""
        print("\n🤖 Testing Bot Endpoints")
        print("-" * 40)
        
        results = {}
        
        # Test bot health check
        print("\n🏥 Testing Bot Health Check")
        result = self.make_request("bot/health", method="GET", use_auth=False)
        if "error" not in result and result.get("status") == "healthy":
            print("✅ Bot health check passed!")
            services = result.get("services", {})
            print(f"   Knowledge Base: {services.get('knowledge_base', 'unknown')}")
            print(f"   Embeddings: {services.get('embeddings', 'unknown')}")
            print(f"   Gemini: {services.get('gemini', 'unknown')}")
            print(f"   Google Docs: {services.get('google_docs', 'unknown')}")
            print(f"   Supabase: {services.get('supabase', 'unknown')}")
            results["bot_health"] = True
        else:
            print(f"❌ Bot health check failed: {result}")
            results["bot_health"] = False
        
        time.sleep(1)
        
        # Test bot chat functionality
        print("\n💬 Testing Bot Chat")
        chat_data = {
            "message": "What qualifications do I need to volunteer?",
            "user_id": "test_user_123"
        }
        result = self.make_request("bot/chat", method="POST", data=chat_data, use_auth=True)
        if "error" not in result and result.get("response"):
            print("✅ Bot chat passed!")
            print(f"   Response: {result.get('response', 'No response')[:100]}...")
            print(f"   Context Used: {result.get('context_used', 0)} chunks")
            print(f"   Confidence: {result.get('confidence', 'unknown')}")
            if result.get("note"):
                print(f"   Note: {result.get('note')}")
            results["bot_chat"] = True
        else:
            print(f"❌ Bot chat failed: {result}")
            results["bot_chat"] = False
        
        time.sleep(1)
        
        # Test bot test endpoint
        print("\n🧪 Testing Bot Test Endpoint")
        test_data = {
            "message": "Tell me about Vietnam Hearts teaching requirements"
        }
        result = self.make_request("bot/test", method="POST", data=test_data, use_auth=False)
        if "error" not in result and result.get("status") == "success":
            print("✅ Bot test passed!")
            print(f"   Test Message: {result.get('test_message', 'No message')}")
            print(f"   Response: {result.get('response', 'No response')[:100]}...")
            print(f"   Context Used: {result.get('context_used', 0)} chunks")
            results["bot_test"] = True
        else:
            print(f"❌ Bot test failed: {result}")
            results["bot_test"] = False
        
        time.sleep(1)
        
        # Test document processing (if Supabase is available)
        print("\n📄 Testing Document Processing")
        doc_id = "11AKdzzkphTCDXyyNpxahWYG5d5pBeLpThvm0haVBw6k"
        doc_data = {
            "doc_id": doc_id,
            "metadata": {
                "title": "Teacher and TA Guide & Resources",
                "type": "volunteer_guide",
                "category": "teaching_resources"
            }
        }
        
        # Try to sync document (requires admin auth)
        if self.auth_type == "supabase":
            result = self.make_request("bot/knowledge-sync", method="POST", data=doc_data, use_auth=True)
            if "error" not in result and result.get("status") == "success":
                print("✅ Document sync passed!")
                print(f"   Document ID: {doc_id}")
                print(f"   Message: {result.get('message', 'No message')}")
                results["document_sync"] = True
            else:
                print(f"❌ Document sync failed: {result}")
                results["document_sync"] = False
        else:
            print("⚠️  Document sync requires Supabase authentication. Skipping...")
            results["document_sync"] = False
        
        time.sleep(1)
        
        # Test knowledge status
        print("\n📚 Testing Knowledge Status")
        result = self.make_request("bot/knowledge-sync/status", method="GET", use_auth=True)
        if "error" not in result and result.get("knowledge_service_available") == True:
            print("✅ Knowledge status check passed!")
            print(f"   Documents Count: {result.get('documents_count', 0)}")
            print(f"   Embeddings Available: {result.get('embeddings_available', False)}")
            print(f"   Gemini Available: {result.get('gemini_available', False)}")
            print(f"   Supabase Available: {result.get('supabase_available', False)}")
            print(f"   Document Service Available: {result.get('document_service_available', False)}")
            if result.get("documents"):
                print(f"   Documents: {len(result['documents'])} found")
                for doc in result["documents"]:
                    print(f"     - {doc.get('title', doc.get('id', 'Unknown'))}: {doc.get('chunks', 0)} chunks")
            results["knowledge_status"] = True
        else:
            print(f"❌ Knowledge status check failed: {result}")
            results["knowledge_status"] = False
        
        return results


def main():
    """Main function to run tests"""
    parser = argparse.ArgumentParser(description="Test Vietnam Hearts API endpoints")
    parser.add_argument("endpoint", help="Endpoint to test")
    parser.add_argument("--auth-type", choices=["gcloud", "supabase"], default="supabase",
                       help="Authentication type to use (default: gcloud)")
    
    args = parser.parse_args()
    endpoint = args.endpoint.lower()
    auth_type = args.auth_type
    
    # Create tester instance
    tester = SchedulerAPITester(auth_type=auth_type)
    
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
    elif endpoint == "admin-schedule-status":
        if auth_type != "supabase":
            print("❌ Admin endpoints require Supabase authentication. Use --auth-type=supabase")
            sys.exit(1)
        tester.test_admin_schedule_status()
    elif endpoint == "bot":
        tester.test_bot_endpoints()
    elif endpoint == "bot-health":
        tester.test_bot_endpoints()
    elif endpoint == "bot-chat":
        tester.test_bot_endpoints()
    elif endpoint == "bot-test":
        tester.test_bot_endpoints()
    elif endpoint == "document-sync":
        if auth_type != "supabase":
            print("❌ Document sync requires Supabase authentication. Use --auth-type=supabase")
            sys.exit(1)
        tester.test_bot_endpoints()
    elif endpoint == "knowledge-status":
        if auth_type != "supabase":
            print("❌ Knowledge status requires Supabase authentication. Use --auth-type=supabase")
            sys.exit(1)
        tester.test_bot_endpoints()
    else:
        print(f"❌ Unknown endpoint: {endpoint}")
        print("Available endpoints: health, send-confirmation-emails, sync-volunteers, send-weekly-reminders, rotate-schedule, admin-schedule-status, all, bot, bot-health, bot-chat, bot-test, document-sync, knowledge-status")
        sys.exit(1)


if __name__ == "__main__":
    main() 