#!/usr/bin/env python3
"""
Debug script for authentication issues
"""

import os
import subprocess
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def main():
    print("🔍 Authentication Debug Information")
    print("=" * 50)
    
    # Check environment variables
    oauth_client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    service_account_email = os.getenv("SERVICE_ACCOUNT_EMAIL")
    
    print(f"GOOGLE_OAUTH_CLIENT_ID: {oauth_client_id}")
    print(f"SERVICE_ACCOUNT_EMAIL: {service_account_email}")
    print()
    
    if not oauth_client_id:
        print("❌ GOOGLE_OAUTH_CLIENT_ID not set!")
        return
    
    # Test token generation
    print("🔑 Testing token generation...")
    try:
        cmd = [
            "gcloud", "auth", "print-identity-token",
            f"--audiences={oauth_client_id}"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        token = result.stdout.strip()
        print(f"✅ Token generated successfully")
        print(f"   Token length: {len(token)} characters")
        print(f"   Token preview: {token[:50]}...")
        
        # Decode the token to see its contents (without verification)
        import base64
        try:
            # Split the JWT token
            parts = token.split('.')
            if len(parts) == 3:
                # Decode the payload (second part)
                payload = parts[1]
                # Add padding if needed
                payload += '=' * (4 - len(payload) % 4)
                decoded = base64.urlsafe_b64decode(payload)
                token_data = json.loads(decoded)
                
                print(f"   Token payload:")
                print(f"     iss: {token_data.get('iss', 'N/A')}")
                print(f"     aud: {token_data.get('aud', 'N/A')}")
                print(f"     email: {token_data.get('email', 'N/A')}")
                print(f"     sub: {token_data.get('sub', 'N/A')}")
                
        except Exception as e:
            print(f"   Could not decode token payload: {e}")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Token generation failed: {e}")
        print(f"Error output: {e.stderr}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main() 