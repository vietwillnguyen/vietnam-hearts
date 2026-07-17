"""
Shared credential resolution for Google Workspace APIs (Sheets/Drive/Docs).

Cloud Run's Application Default Credentials are always cloud-platform-scoped
(Cloud Run has no per-service scope configuration like GCE instance scopes),
and Workspace APIs reject that scope with ACCESS_TOKEN_SCOPE_INSUFFICIENT.
Self-impersonation via the IAM Credentials API mints a token with the literal
scopes requested, sidestepping that limitation without needing a stored key.
"""

import os
from functools import cache
from pathlib import Path

import google.auth
from google.auth import impersonated_credentials
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.config import GOOGLE_APPLICATION_CREDENTIALS
from app.utils.logging_config import get_logger

logger = get_logger("google_credentials")

_ENV_VAR = "GOOGLE_APPLICATION_CREDENTIALS"


def default_credentials():
    """google.auth.default(), tolerating a stale GOOGLE_APPLICATION_CREDENTIALS.

    When the env var points at a file that doesn't exist (e.g. a leftover
    key-file path from before the image stopped shipping secrets/),
    google.auth.default() raises DefaultCredentialsError immediately instead
    of falling through to the metadata server. Hide the stale var for the
    call so the attached service account can still be resolved.
    """
    env_value = os.environ.get(_ENV_VAR)
    if not env_value or Path(env_value).exists():
        return google.auth.default()

    logger.warning(
        f"{_ENV_VAR} is set to '{env_value}' but no such file exists; "
        "ignoring it and falling back to Application Default Credentials"
    )
    os.environ.pop(_ENV_VAR, None)
    try:
        return google.auth.default()
    finally:
        os.environ[_ENV_VAR] = env_value


def get_scoped_credentials(scopes: list[str]):
    """Return credentials authorized for the given OAuth scopes."""
    return _get_scoped_credentials_cached(tuple(sorted(scopes)))


@cache
def _get_scoped_credentials_cached(scopes: tuple[str, ...]):
    if GOOGLE_APPLICATION_CREDENTIALS.exists():
        return service_account.Credentials.from_service_account_file(
            str(GOOGLE_APPLICATION_CREDENTIALS), scopes=list(scopes)
        )

    source_credentials, _ = default_credentials()
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
