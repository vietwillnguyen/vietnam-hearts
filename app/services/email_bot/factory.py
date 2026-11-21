"""Assembling a pipeline from configuration, and nothing else.

Split out of the router so the endpoint stays about HTTP and the wiring stays
testable on its own. It is also the one place that knows which classifier
decides and which shadows, so flipping ``TRIAGE_CLASSIFIER`` is one setting read
rather than a branch scattered through the pipeline.

Nothing here is constructed while the bot is off. The endpoint checks
``EMAIL_BOT_ENABLED`` and the mode before calling in, so a deployment with no
Gmail grant never builds a Gmail client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app import config
from app.services.channels.gmail import GmailAdapter
from app.services.channels.gmail_transport import GmailTransport
from app.services.email_bot.pipeline import EmailBotPipeline
from app.services.email_bot.settings import EmailBotSettings
from app.services.notifier import (
    CompositeNotifier,
    DiscordNotifier,
    EmailForwardNotifier,
)
from app.services.triage.jev import JevClassifier
from app.services.triage.litellm import LiteLLMClassifier
from app.services.triage.shadow import ShadowingClassifier
from app.utils.logging_config import get_logger

logger = get_logger("email_bot_factory")


class EmailBotNotConfigured(RuntimeError):
    """A credential the bot cannot work without is missing.

    Raised rather than degraded: a poll that quietly does nothing because a
    refresh token is unset looks identical, on the dashboard, to a poll that
    found no mail.
    """


@dataclass(frozen=True)
class Credentials:
    """The environment half of the configuration, gathered in one place."""

    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    typesafe_api_key: str = ""
    discord_webhook_url: str = ""
    anthropic_api_key: str = ""

    @classmethod
    def from_env(cls) -> Credentials:
        return cls(
            gmail_client_id=config.GMAIL_OAUTH_CLIENT_ID or "",
            gmail_client_secret=config.GMAIL_OAUTH_CLIENT_SECRET or "",
            gmail_refresh_token=config.GMAIL_OAUTH_REFRESH_TOKEN or "",
            typesafe_api_key=config.TYPESAFE_API_KEY or "",
            discord_webhook_url=config.DISCORD_WEBHOOK_URL or "",
            anthropic_api_key=config.ANTHROPIC_API_KEY or "",
        )

    def require_gmail(self) -> None:
        missing = [
            name
            for name, value in (
                ("GMAIL_OAUTH_CLIENT_ID", self.gmail_client_id),
                ("GMAIL_OAUTH_CLIENT_SECRET", self.gmail_client_secret),
                ("GMAIL_OAUTH_REFRESH_TOKEN", self.gmail_refresh_token),
            )
            if not value
        ]
        if missing:
            raise EmailBotNotConfigured(
                f"Missing Gmail credentials: {', '.join(missing)}"
            )


def build_classifier(
    settings: EmailBotSettings, credentials: Credentials
) -> ShadowingClassifier:
    """The decider wrapped around the shadow, per ``TRIAGE_CLASSIFIER``.

    Both are constructed whenever their keys allow it, because the shadow's
    whole purpose is to be measured on the same traffic the decider sees. A
    shadow that cannot be built is simply absent: the decision is unaffected and
    the disagreement report is thinner.
    """
    jev: JevClassifier | None = None
    if credentials.typesafe_api_key:
        jev = JevClassifier.from_api_key(credentials.typesafe_api_key)

    fallback = LiteLLMClassifier(
        model=settings.triage_fallback_model,
        api_key=_litellm_api_key(settings, credentials),
    )

    if settings.triage_classifier == "litellm":
        return ShadowingClassifier(decider=fallback, shadow=jev)

    if jev is None:
        # TRIAGE_CLASSIFIER says jev but there is no key for it. Falling back to
        # the other implementation keeps the inbox triaged, and says so loudly,
        # rather than failing every poll over a setting mismatch.
        logger.error(
            "TRIAGE_CLASSIFIER is jev but TYPESAFE_API_KEY is unset; "
            "deciding with %s instead",
            fallback.name,
        )
        return ShadowingClassifier(decider=fallback, shadow=None)

    return ShadowingClassifier(decider=jev, shadow=fallback)


def _litellm_api_key(
    settings: EmailBotSettings, credentials: Credentials
) -> str | None:
    """The key the configured shadow model needs, if we hold a specific one.

    Returning None lets LiteLLM read its own provider environment variables,
    which is the right behaviour for a Gemini model whose key is already in the
    environment as GEMINI_API_KEY.
    """
    if settings.triage_fallback_model.startswith("anthropic/"):
        return credentials.anthropic_api_key or None
    return None


def build_notifier(
    settings: EmailBotSettings, credentials: Credentials, db: Session
) -> CompositeNotifier:
    from app.services.email_service import email_service

    return CompositeNotifier(
        [
            EmailForwardNotifier(email_service, settings.escalation_owner_email, db=db),
            DiscordNotifier(credentials.discord_webhook_url),
        ]
    )


def build_pipeline(
    db: Session,
    settings: EmailBotSettings,
    credentials: Credentials | None = None,
    bot_service: Any = None,
) -> EmailBotPipeline:
    creds = credentials or Credentials.from_env()
    creds.require_gmail()

    adapter = GmailAdapter(
        GmailTransport.from_credentials(
            creds.gmail_client_id, creds.gmail_client_secret, creds.gmail_refresh_token
        )
    )

    if bot_service is None:
        from app.dependencies.services import get_bot_service

        bot_service = get_bot_service()

    return EmailBotPipeline(
        db=db,
        adapter=adapter,
        classifier=build_classifier(settings, creds),
        notifier=build_notifier(settings, creds, db),
        settings=settings,
        bot_service=bot_service,
        gemini_client=getattr(
            getattr(bot_service, "knowledge_service", None), "gemini_client", None
        ),
        admin_emails=tuple(config.ADMIN_EMAILS),
    )
