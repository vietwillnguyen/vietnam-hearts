"""
Tests for app.utils.google_credentials.

Pins down the production bug (2026-07-11): after commit 4929486 dropped the
service-account key file from the Docker image, Cloud Run's Application
Default Credentials mint cloud-platform-scoped tokens, which the Sheets API
rejects with "Request had insufficient authentication scopes." Self-impersonation
via the IAM Credentials API is required to get a token with the literal
scopes Sheets/Drive/Docs expect.
"""

from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import DefaultCredentialsError

from app.utils.google_credentials import _get_scoped_credentials_cached, get_scoped_credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


@pytest.fixture(autouse=True)
def clear_credentials_cache():
    _get_scoped_credentials_cached.cache_clear()
    yield
    _get_scoped_credentials_cached.cache_clear()


def test_uses_file_based_credentials_when_key_file_present():
    with patch("app.utils.google_credentials.GOOGLE_APPLICATION_CREDENTIALS") as mock_path, \
         patch("app.utils.google_credentials.service_account.Credentials.from_service_account_file") as mock_from_file:
        mock_path.exists.return_value = True
        mock_from_file.return_value = "file-creds"

        result = get_scoped_credentials(SCOPES)

        mock_from_file.assert_called_once_with(str(mock_path), scopes=SCOPES)
        assert result == "file-creds"


def test_self_impersonates_when_no_key_file_present():
    """No key file (Cloud Run) -> must not hand back the raw ADC creds unscoped."""
    source_creds = MagicMock()
    source_creds.service_account_email = "runtime-sa@project.iam.gserviceaccount.com"

    with patch("app.utils.google_credentials.GOOGLE_APPLICATION_CREDENTIALS") as mock_path, \
         patch("app.utils.google_credentials.google.auth.default") as mock_default, \
         patch("app.utils.google_credentials.impersonated_credentials.Credentials") as mock_impersonated:
        mock_path.exists.return_value = False
        mock_default.return_value = (source_creds, "some-project")
        mock_impersonated.return_value = "impersonated-creds"

        result = get_scoped_credentials(SCOPES)

        source_creds.refresh.assert_called_once()
        mock_impersonated.assert_called_once_with(
            source_credentials=source_creds,
            target_principal="runtime-sa@project.iam.gserviceaccount.com",
            target_scopes=SCOPES,
            lifetime=3600,
        )
        assert result == "impersonated-creds"


def test_raises_clear_error_for_user_adc_without_service_account_email():
    """gcloud user-based ADC has no service_account_email; must not raise a raw AttributeError."""
    source_creds = MagicMock(spec=["refresh"])

    with patch("app.utils.google_credentials.GOOGLE_APPLICATION_CREDENTIALS") as mock_path, \
         patch("app.utils.google_credentials.google.auth.default") as mock_default:
        mock_path.exists.return_value = False
        mock_default.return_value = (source_creds, "some-project")

        with pytest.raises(DefaultCredentialsError):
            get_scoped_credentials(SCOPES)


def test_reuses_cached_credentials_for_same_scopes():
    """Repeated calls for the same scope set must not re-invoke self-impersonation each time."""
    source_creds = MagicMock()
    source_creds.service_account_email = "runtime-sa@project.iam.gserviceaccount.com"

    with patch("app.utils.google_credentials.GOOGLE_APPLICATION_CREDENTIALS") as mock_path, \
         patch("app.utils.google_credentials.google.auth.default") as mock_default, \
         patch("app.utils.google_credentials.impersonated_credentials.Credentials") as mock_impersonated:
        mock_path.exists.return_value = False
        mock_default.return_value = (source_creds, "some-project")
        mock_impersonated.return_value = "impersonated-creds"

        first = get_scoped_credentials(SCOPES)
        second = get_scoped_credentials(list(SCOPES))

        mock_default.assert_called_once()
        mock_impersonated.assert_called_once()
        assert first is second
