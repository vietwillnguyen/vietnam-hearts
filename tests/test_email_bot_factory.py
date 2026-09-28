"""Wiring: which classifier decides, and what a missing credential does.

Split out of the router so the endpoint stays about HTTP. What is tested here is
the one decision the factory owns that has real consequences - ``TRIAGE_CLASSIFIER``
picking the decider - plus the refusal that keeps a half-configured deployment
from looking like a quiet day.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

from unittest.mock import MagicMock, patch

import pytest

from app.services.email_bot.factory import (
    Credentials,
    EmailBotNotConfigured,
    build_classifier,
    build_notifier,
    build_pipeline,
)
from app.services.email_bot.settings import EmailBotSettings
from app.services.triage.jev import JevClassifier
from app.services.triage.litellm import LiteLLMClassifier

FULL = Credentials(
    gmail_client_id="client-id",
    gmail_client_secret="client-secret",
    gmail_refresh_token="1//refresh",
    typesafe_api_key="ts-key",
    discord_webhook_url="https://discord.example.com/hook",
    anthropic_api_key="sk-ant",
)


def settings(**overrides) -> EmailBotSettings:
    values = {"mode": "draft", "escalation_owner_email": "owner@example.com"}
    values.update(overrides)
    return EmailBotSettings(**values)


@pytest.fixture(autouse=True)
def no_real_typesafe_client():
    """Never construct a real vendor client, which would read the environment."""
    with patch.object(
        JevClassifier,
        "from_api_key",
        lambda key, model="jev-latest": (JevClassifier(MagicMock(), model=model)),
    ):
        yield


class TestWhichClassifierDecides:
    def test_jev_decides_by_default_with_litellm_shadowing(self):
        classifier = build_classifier(settings(triage_classifier="jev"), FULL)

        assert isinstance(classifier._decider, JevClassifier)
        assert isinstance(classifier._shadow, LiteLLMClassifier)

    def test_the_setting_can_swap_the_two_round(self):
        # Flipping it needs no deploy, which is the whole point: E2 decides
        # this from evidence and changes one dashboard field.
        classifier = build_classifier(settings(triage_classifier="litellm"), FULL)

        assert isinstance(classifier._decider, LiteLLMClassifier)
        assert isinstance(classifier._shadow, JevClassifier)

    def test_the_shadow_model_comes_from_the_setting(self):
        classifier = build_classifier(
            settings(triage_fallback_model="anthropic/claude-haiku-4-5-20251001"), FULL
        )
        assert classifier._shadow.name == (
            "litellm:anthropic/claude-haiku-4-5-20251001"
        )

    def test_an_anthropic_model_is_given_the_anthropic_key(self):
        classifier = build_classifier(
            settings(triage_fallback_model="anthropic/claude-haiku-4-5-20251001"), FULL
        )
        assert classifier._shadow._api_key == "sk-ant"

    def test_a_gemini_model_is_left_to_read_its_own_environment(self):
        # GEMINI_API_KEY is already in the environment for the answer path, and
        # LiteLLM reads it itself.
        classifier = build_classifier(
            settings(triage_fallback_model="gemini/gemini-3.5-flash-lite"), FULL
        )
        assert classifier._shadow._api_key is None

    def test_a_missing_jev_key_falls_back_loudly_rather_than_failing_every_poll(
        self, caplog
    ):
        from tests.fixtures.logs import attached_caplog

        without_jev = Credentials(
            gmail_client_id="a", gmail_client_secret="b", gmail_refresh_token="c"
        )
        with attached_caplog(caplog, "email_bot_factory", level="ERROR"):
            classifier = build_classifier(
                settings(triage_classifier="jev"), without_jev
            )

        # The inbox still gets triaged, and the mismatch is on the record.
        assert isinstance(classifier._decider, LiteLLMClassifier)
        assert classifier._shadow is None
        assert "TYPESAFE_API_KEY" in caplog.text

    def test_litellm_deciding_with_no_jev_key_has_no_shadow(self):
        without_jev = Credentials(
            gmail_client_id="a", gmail_client_secret="b", gmail_refresh_token="c"
        )
        classifier = build_classifier(
            settings(triage_classifier="litellm"), without_jev
        )
        assert isinstance(classifier._decider, LiteLLMClassifier)
        assert classifier._shadow is None


class TestCredentials:
    def test_from_env_reads_the_documented_names(self, monkeypatch):
        import app.config as config

        monkeypatch.setattr(config, "GMAIL_OAUTH_CLIENT_ID", "id", raising=False)
        monkeypatch.setattr(
            config, "GMAIL_OAUTH_CLIENT_SECRET", "secret", raising=False
        )
        monkeypatch.setattr(config, "GMAIL_OAUTH_REFRESH_TOKEN", "token", raising=False)
        monkeypatch.setattr(config, "TYPESAFE_API_KEY", "ts", raising=False)
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "hook", raising=False)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "ant", raising=False)

        credentials = Credentials.from_env()

        assert credentials.gmail_client_id == "id"
        assert credentials.gmail_refresh_token == "token"
        assert credentials.typesafe_api_key == "ts"
        assert credentials.discord_webhook_url == "hook"
        assert credentials.anthropic_api_key == "ant"

    def test_unset_variables_read_as_empty_rather_than_none(self, monkeypatch):
        import app.config as config

        for name in (
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
            "TYPESAFE_API_KEY",
            "DISCORD_WEBHOOK_URL",
            "ANTHROPIC_API_KEY",
        ):
            monkeypatch.setattr(config, name, None, raising=False)

        credentials = Credentials.from_env()
        assert credentials.gmail_client_id == ""
        assert credentials.typesafe_api_key == ""

    def test_the_full_set_passes_the_gmail_requirement(self):
        FULL.require_gmail()

    @pytest.mark.parametrize(
        "field,name",
        [
            ("gmail_client_id", "GMAIL_OAUTH_CLIENT_ID"),
            ("gmail_client_secret", "GMAIL_OAUTH_CLIENT_SECRET"),
            ("gmail_refresh_token", "GMAIL_OAUTH_REFRESH_TOKEN"),
        ],
    )
    def test_a_missing_gmail_credential_is_named_in_the_refusal(self, field, name):
        # A poll that quietly does nothing because a refresh token is unset
        # looks identical, on the dashboard, to a poll that found no mail.
        import dataclasses

        broken = dataclasses.replace(FULL, **{field: ""})
        with pytest.raises(EmailBotNotConfigured, match=name):
            broken.require_gmail()

    def test_every_missing_credential_is_named_at_once(self):
        with pytest.raises(EmailBotNotConfigured) as raised:
            Credentials().require_gmail()

        message = str(raised.value)
        for name in (
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
        ):
            assert name in message

    def test_the_refusal_never_contains_a_credential_value(self):
        import dataclasses

        broken = dataclasses.replace(FULL, gmail_refresh_token="")
        with pytest.raises(EmailBotNotConfigured) as raised:
            broken.require_gmail()

        assert "client-secret" not in str(raised.value)
        assert "1//refresh" not in str(raised.value)


class TestNotifier:
    def test_it_forwards_to_the_configured_owner_and_posts_to_discord(self, test_db):
        composite = build_notifier(
            settings(escalation_owner_email="owner@example.com"), FULL, test_db
        )
        from app.services.notifier import DiscordNotifier, EmailForwardNotifier

        forward = [
            n for n in composite._notifiers if isinstance(n, EmailForwardNotifier)
        ]
        discord = [n for n in composite._notifiers if isinstance(n, DiscordNotifier)]

        assert forward[0].recipient == "owner@example.com"
        assert discord

    def test_the_forward_recipient_is_fixed_at_construction(self, test_db):
        from app.services.notifier import EmailForwardNotifier

        composite = build_notifier(
            settings(escalation_owner_email="owner@example.com"), FULL, test_db
        )
        forward = next(
            n for n in composite._notifiers if isinstance(n, EmailForwardNotifier)
        )
        # No event, header or later setting read can redirect it.
        assert not hasattr(forward, "set_recipient")


class TestBuildPipeline:
    def test_it_refuses_before_constructing_a_gmail_client(self, test_db):
        with patch("app.services.email_bot.factory.GmailTransport") as transport:
            with pytest.raises(EmailBotNotConfigured):
                build_pipeline(test_db, settings(), Credentials())
        transport.from_credentials.assert_not_called()

    def test_it_assembles_a_pipeline_from_a_full_credential_set(self, test_db):
        bot = MagicMock()
        bot.knowledge_service.gemini_client = MagicMock()

        with patch("app.services.email_bot.factory.GmailTransport") as transport:
            transport.from_credentials.return_value = MagicMock(
                inbox_address="inbox@example.com"
            )
            pipeline = build_pipeline(test_db, settings(), FULL, bot_service=bot)

        assert pipeline.settings.mode == "draft"
        assert pipeline.bot_service is bot
        assert pipeline.gemini_client is bot.knowledge_service.gemini_client
        # Only a draft sink exists in this phase, so nothing can be sent.
        assert set(pipeline.sinks) == {"draft"}

    def test_the_admin_addresses_reach_the_internal_sender_guard(self, test_db):
        import app.config as config

        with (
            patch.object(config, "ADMIN_EMAILS", ["coord@example.com"]),
            patch("app.services.email_bot.factory.GmailTransport") as transport,
        ):
            transport.from_credentials.return_value = MagicMock(
                inbox_address="inbox@example.com"
            )
            pipeline = build_pipeline(
                test_db, settings(), FULL, bot_service=MagicMock()
            )

        assert pipeline.admin_emails == ("coord@example.com",)
