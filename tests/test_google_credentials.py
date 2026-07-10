import tempfile
from pathlib import Path
import pytest
from google.auth.exceptions import DefaultCredentialsError

import app.services.google_sheets as gs_module
from app.services.google_sheets import GoogleSheetsService


class DummyCreds:
    def __init__(self, email="sa@example.com"):
        self.service_account_email = email


def test_validate_config_adc_available(monkeypatch):
    """When ADC is available, _validate_config should succeed (no exception)."""
    def fake_default(*args, **kwargs):
        return (DummyCreds(), "project-id")

    monkeypatch.setattr(gs_module, "default", fake_default)

    svc = GoogleSheetsService()
    # Should not raise
    svc._validate_config(db=None)


def test_validate_config_fallback_to_file(monkeypatch, tmp_path):
    """When ADC is not available but a credentials file is provided, validation should pass."""
    def fake_default(*args, **kwargs):
        raise DefaultCredentialsError()

    monkeypatch.setattr(gs_module, "default", fake_default)

    # Create a temp credentials file and point the module-level variable to it
    cred_file = tmp_path / "sa.json"
    cred_file.write_text('{}')
    monkeypatch.setattr(gs_module, "GOOGLE_APPLICATION_CREDENTIALS", Path(cred_file))

    svc = GoogleSheetsService()
    svc._validate_config(db=None)  # should not raise


def test_validate_config_no_creds(monkeypatch, tmp_path):
    """When neither ADC nor credentials file exist, validation should raise ValueError."""
    def fake_default(*args, **kwargs):
        raise DefaultCredentialsError()

    monkeypatch.setattr(gs_module, "default", fake_default)
    # Ensure module var is None or points to non-existent file
    monkeypatch.setattr(gs_module, "GOOGLE_APPLICATION_CREDENTIALS", None)

    svc = GoogleSheetsService()
    with pytest.raises(ValueError):
        svc._validate_config(db=None)
