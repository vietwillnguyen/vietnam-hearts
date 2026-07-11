"""
Shared credential resolution for Google Workspace APIs (Sheets/Drive/Docs).

Cloud Run's Application Default Credentials are always cloud-platform-scoped
(Cloud Run has no per-service scope configuration like GCE instance scopes),
and Workspace APIs reject that scope with ACCESS_TOKEN_SCOPE_INSUFFICIENT.
Self-impersonation via the IAM Credentials API mints a token with the literal
scopes requested, sidestepping that limitation without needing a stored key.
"""

from typing import List

import google.auth
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.config import GOOGLE_APPLICATION_CREDENTIALS


def get_scoped_credentials(scopes: List[str]):
    """Return credentials authorized for the given OAuth scopes."""
    if GOOGLE_APPLICATION_CREDENTIALS.exists():
        return service_account.Credentials.from_service_account_file(
            str(GOOGLE_APPLICATION_CREDENTIALS), scopes=scopes
        )

    source_credentials, _ = google.auth.default()
    source_credentials.refresh(Request())
    return impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=source_credentials.service_account_email,
        target_scopes=scopes,
        lifetime=3600,
    )
