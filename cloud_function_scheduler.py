"""
Cloud Function for Vietnam Hearts Scheduler

This function can be triggered by Cloud Scheduler to run various scheduler operations.
"""

import os
import json
import requests
from typing import Dict, Any
from datetime import datetime

# Configuration
BASE_URL = "https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app"
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

def make_request(endpoint: str, method: str = "POST", data: Dict[str, Any] = None) -> Dict[str, Any]:
    """Make authenticated request to scheduler endpoint"""
    url = f"{BASE_URL}/admin/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "apikey": SUPABASE_SERVICE_ROLE_KEY
    }
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data or {})
        else:
            return {"error": f"Unsupported method: {method}"}
        
        return {
            "status_code": response.status_code,
            "data": response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
        }
    except Exception as e:
        return {"error": str(e)}

def sync_volunteers(request):
    """Cloud Function to sync volunteers"""
    result = make_request("sync-volunteers", method="POST")
    return json.dumps(result)

def send_weekly_reminders(request):
    """Cloud Function to send weekly reminders"""
    result = make_request("send-weekly-reminders", method="POST")
    return json.dumps(result)

def send_confirmation_emails(request):
    """Cloud Function to send confirmation emails"""
    result = make_request("send-confirmation-emails", method="POST")
    return json.dumps(result)

def rotate_schedule(request):
    """Cloud Function to rotate schedule"""
    result = make_request("rotate-schedule", method="POST")
    return json.dumps(result)

def health_check(request):
    """Cloud Function to check health"""
    result = make_request("health", method="GET")
    return json.dumps(result)

def scheduler_dispatcher(request):
    """Main dispatcher function that routes to different operations based on request data"""
    request_json = request.get_json(silent=True)
    
    if not request_json:
        return json.dumps({"error": "No JSON data provided"})
    
    operation = request_json.get("operation")
    
    operations = {
        "sync_volunteers": sync_volunteers,
        "send_weekly_reminders": send_weekly_reminders,
        "send_confirmation_emails": send_confirmation_emails,
        "rotate_schedule": rotate_schedule,
        "health_check": health_check
    }
    
    if operation not in operations:
        return json.dumps({"error": f"Unknown operation: {operation}"})
    
    return operations[operation](request) 