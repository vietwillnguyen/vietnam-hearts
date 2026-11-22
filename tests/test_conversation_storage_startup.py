"""Startup refuses a production service that would store inbox mail without its key.

The rule is in ``field_crypto.check_storage_key_at_startup`` and is tested
case by case there. These tests cover the wiring: the lifespan reads the real
``EMAIL_BOT_ENABLED``, the real ``EMAIL_BOT_MODE`` setting and the real key
variable, and an ``EncryptionKeyError`` stops the app from starting. The
deploy this PR triggers runs with the bot off and, possibly, no key yet, so the
"starts anyway" case matters as much as the refusal.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.services.settings_service import set_setting
from app.utils.field_crypto import KEYS_SETTING, EncryptionKeyError
from tests.fixtures.logs import attached_caplog

VALID_KEY = os.environ[KEYS_SETTING]


@pytest.fixture
def production(test_engine, test_db, monkeypatch):
    """The lifespan as production runs it, against the test database."""
    import app.database as app_db

    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=test_engine))
    monkeypatch.setattr("app.config.ENVIRONMENT", "production")
    with patch("app.config.validate_config"):
        yield test_db


def start():
    from app.main import app

    with TestClient(app):
        pass


class TestRefusingToStart:
    def test_bot_on_and_no_key_refuses_naming_the_setting(
        self, production, monkeypatch
    ):
        monkeypatch.setattr("app.config.EMAIL_BOT_ENABLED", True)
        monkeypatch.delenv(KEYS_SETTING)
        set_setting(production, "EMAIL_BOT_MODE", "draft")

        with pytest.raises(EncryptionKeyError) as raised:
            start()
        assert KEYS_SETTING in str(raised.value)

    def test_a_malformed_key_refuses_even_with_the_bot_off(
        self, production, monkeypatch
    ):
        monkeypatch.setattr("app.config.EMAIL_BOT_ENABLED", False)
        monkeypatch.setenv(KEYS_SETTING, "k1:not-a-key")
        set_setting(production, "EMAIL_BOT_MODE", "off")

        with pytest.raises(EncryptionKeyError):
            start()


class TestStartingAnyway:
    def test_the_bot_off_and_no_key_starts(self, production, monkeypatch, caplog):
        # This is the deploy the merge triggers: EMAIL_BOT_MODE=off, key not
        # yet set. It must come up.
        monkeypatch.setattr("app.config.EMAIL_BOT_ENABLED", True)
        monkeypatch.delenv(KEYS_SETTING)
        set_setting(production, "EMAIL_BOT_MODE", "off")

        with attached_caplog(caplog, "main", level=logging.INFO):
            start()

        assert f"{KEYS_SETTING} is not set" in caplog.text

    def test_a_valid_key_starts_with_the_bot_on(self, production, monkeypatch, caplog):
        monkeypatch.setattr("app.config.EMAIL_BOT_ENABLED", True)
        monkeypatch.setenv(KEYS_SETTING, VALID_KEY)
        set_setting(production, "EMAIL_BOT_MODE", "auto")

        with attached_caplog(caplog, "main", level=logging.INFO):
            start()

        assert "conversation encryption key: ok" in caplog.text
        # The status is logged; the key never is.
        assert VALID_KEY.split(":", 1)[1] not in caplog.text
