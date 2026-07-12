"""
Shared credential resolution for Google Workspace APIs (Sheets/Drive/Docs).

Cloud Run's Application Default Credentials are always cloud-platform-scoped
(Cloud Run has no per-service scope configuration like GCE instance scopes),
and Workspace APIs reject that scope with ACCESS_TOKEN_SCOPE_INSUFFICIENT.
Self-impersonation via the IAM Credentials API mints a token with the literal
scopes requested, sidestepping that limitation without needing a stored key.
"""

from functools import lru_cache
from typing import List, Tuple

import google.auth
from google.auth import impersonated_credentials
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.config import GOOGLE_APPLICATION_CREDENTIALS


def get_scoped_credentials(scopes: List[str]):
    """Return credentials authorized for the given OAuth scopes."""
    return _get_scoped_credentials_cached(tuple(sorted(scopes)))


@lru_cache(maxsize=None)
def _get_scoped_credentials_cached(scopes: Tuple[str, ...]):
    if GOOGLE_APPLICATION_CREDENTIALS.exists():
        return service_account.Credentials.from_service_account_file(
            str(GOOGLE_APPLICATION_CREDENTIALS), scopes=list(scopes)
        )

    source_credentials, _ = google.auth.default()
    source_credentials.refresh(Request())

    service_account_email = getattr(source_credentials, "service_account_email", None)
    if not service_account_email:
        raise DefaultCredentialsError(
            "Self-impersonation requires a service account identity (GCE/Cloud Run "
            "metadata-server credentials) or a GOOGLE_APPLICATION_CREDENTIALS key file. "
            f"google.auth.default() returned {type(source_credentials).__name__}, which has "
            "no service_account_email; user-based `gcloud auth application-default login` "
            "credentials are not supported here."
        )

    return impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=service_account_email,
        target_scopes=list(scopes),
        lifetime=3600,
    )
