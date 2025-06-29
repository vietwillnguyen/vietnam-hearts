"""
Purpose:
- Implements the OAuth login flow for users to obtain an ID token.
- This is used for testing the API endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
import httpx
import secrets
import os
import time
from urllib.parse import urlencode
from fastapi import Depends

from app.utils.auth import require_google_auth

from app.config import GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET

GOOGLE_CLIENT_ID = GOOGLE_OAUTH_CLIENT_ID
GOOGLE_CLIENT_SECRET = GOOGLE_OAUTH_CLIENT_SECRET
REDIRECT_URI = os.getenv(
    "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8080/auth/callback"
)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# In-memory session storage (for demo only)
sessions = {}

oauth_router = APIRouter(prefix="/auth", tags=["auth"])

@oauth_router.get("/login")
async def login():
    state = secrets.token_urlsafe(32)
    sessions[state] = {"created_at": time.time()}
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid email profile",
        "response_type": "code",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


@oauth_router.get("/callback")
async def auth_callback(
    code: str = Query(...), state: str = Query(...), error: str = Query(None)
):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if state not in sessions:
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    del sessions[state]
    token_data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }
    
    # Configure httpx client with proper timeouts
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(GOOGLE_TOKEN_URL, data=token_data)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to exchange code for token: {response.text}",
                )
            tokens = response.json()
        except httpx.ConnectTimeout:
            raise HTTPException(
                status_code=500,
                detail="Connection timeout when exchanging authorization code. Please try again.",
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Network error when exchanging authorization code: {str(e)}",
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error during token exchange: {str(e)}",
            )
    
    # Show the ID token on a simple HTML page for copy-paste
    html = f"""
    <h2>Login successful!</h2>
    <p>Copy your <b>ID Token</b> below and use it in the FastAPI docs 'Authorize' dialog:</p>
    <textarea rows="8" cols="80">{tokens.get('id_token')}</textarea>
    <p><a href="/docs">Go to API docs</a></p>
    """
    return HTMLResponse(content=html)


@oauth_router.get("/test")
async def test_page():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Google Auth</title>
    </head>
    <body>
        <h1>Google OAuth Test</h1>
        <button onclick="window.location.href='/auth/login'">Login with Google</button>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@oauth_router.get("/protected")
async def protected_endpoint(user_info: dict = Depends(require_google_auth)):
    return {"message": "This is a protected endpoint", "user": user_info}
