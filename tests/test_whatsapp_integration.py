"""
TDD tests for the WhatsApp community link, offered beside Zalo.

Some volunteers cannot register with Zalo, so every place that points them to the
Zalo group also offers the WhatsApp community as the fallback.

Covers:
- INVITE_LINK_WHATSAPP is a default setting, seeded with the community link
- ConfigHelper.get_invite_link_whatsapp() reads it from DB settings
- Confirmation, weekly reminder and base footer render the WhatsApp link
- An empty link hides the WhatsApp line rather than rendering a dead "#" link
- The confirmation, weekly reminder and admin bulk send render the configured link
"""

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services.email_service import EmailService, email_service
from app.services.google_sheets import sheets_service
from app.services.settings_service import (
    get_setting,
    initialize_default_settings,
    set_setting,
)
from app.utils.config_helper import ConfigHelper

WHATSAPP_URL = "https://chat.whatsapp.com/KpfV7kCbR9N1sW8J6ujD1o"
ZALO_URL = "https://zalo.me/g/gcmgkowx6gvotsghvsji"

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = os.path.join(REPO_ROOT, "templates", "email")


@pytest.fixture
def jinja_env():
    return Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=False)


def _common_vars(whatsapp=WHATSAPP_URL):
    return {
        "INVITE_LINK_ZALO": ZALO_URL,
        "INVITE_LINK_WHATSAPP": whatsapp,
        "ONBOARDING_GUIDE_LINK": "https://example.com/onboarding",
        "SCHEDULE_SIGNUP_LINK": "https://example.com/schedule",
        "INSTAGRAM_LINK": "https://instagram.com/vietnamhearts",
        "FACEBOOK_PAGE_LINK": "https://facebook.com/vietnamhearts",
        "EMAIL_PREFERENCES_LINK": "https://example.com/prefs",
    }


def _confirmation(jinja_env, whatsapp=WHATSAPP_URL):
    return jinja_env.get_template("confirmation-email.html").render(
        UserFullName="Test User", **_common_vars(whatsapp)
    )


def _weekly(jinja_env, whatsapp=WHATSAPP_URL):
    return jinja_env.get_template("weekly-reminder-email.html").render(
        first_name="Test",
        class_tables=["<table><tr><td>Sample</td></tr></table>"],
        **_common_vars(whatsapp),
    )


def _body_of(html):
    """The email's own content, without the shared footer."""
    return html.split('class="email-footer"')[0]


class TestWhatsAppSetting:
    def test_default_settings_seed_the_whatsapp_community_link(self, test_db):
        initialize_default_settings(test_db)
        assert get_setting(test_db, "INVITE_LINK_WHATSAPP") == WHATSAPP_URL

    def test_get_invite_link_whatsapp_returns_default_when_db_is_none(self):
        assert ConfigHelper.get_invite_link_whatsapp(db=None, default="x") == "x"

    def test_get_invite_link_whatsapp_reads_from_db(self, test_db):
        set_setting(test_db, "INVITE_LINK_WHATSAPP", WHATSAPP_URL)
        test_db.commit()
        assert ConfigHelper.get_invite_link_whatsapp(test_db) == WHATSAPP_URL

    def test_get_invite_link_whatsapp_returns_default_when_key_missing(self, test_db):
        assert ConfigHelper.get_invite_link_whatsapp(test_db, default="x") == "x"


class TestTemplatesOfferWhatsApp:
    def test_confirmation_email_offers_whatsapp_beside_zalo(self, jinja_env):
        body = _body_of(_confirmation(jinja_env))
        assert ZALO_URL in body
        assert WHATSAPP_URL in body
        assert "WhatsApp" in body

    def test_weekly_reminder_offers_whatsapp_beside_zalo(self, jinja_env):
        body = _body_of(_weekly(jinja_env))
        assert ZALO_URL in body
        assert WHATSAPP_URL in body

    def test_footer_links_whatsapp(self, jinja_env):
        html = _weekly(jinja_env)
        footer = html.split('class="email-footer"')[1]
        assert WHATSAPP_URL in footer

    @pytest.mark.parametrize("render", [_confirmation, _weekly])
    @pytest.mark.parametrize("empty", ["", "#"])
    def test_an_unset_link_hides_whatsapp_instead_of_linking_nowhere(
        self, jinja_env, render, empty
    ):
        html = render(jinja_env, whatsapp=empty)
        assert "WhatsApp" not in html


class TestRenderersPassTheConfiguredLink:
    """The link is a render variable, so a call site that forgets it renders the
    template with the WhatsApp line silently hidden."""

    CONFIGURED = "https://chat.whatsapp.com/ConfiguredForTest"

    @pytest.fixture(autouse=True)
    def configured_link(self, test_db):
        set_setting(test_db, "INVITE_LINK_WHATSAPP", self.CONFIGURED)
        test_db.commit()

    @pytest.fixture
    def admin_client(self, client):
        from app.dependencies.auth import get_current_admin_user
        from app.main import app

        app.dependency_overrides[get_current_admin_user] = lambda: {
            "id": "test-admin",
            "email": "admin@vietnamhearts.org",
        }
        yield client
        app.dependency_overrides.pop(get_current_admin_user, None)

    def test_confirmation_email(self, test_db, mock_volunteer):
        with patch("app.services.email_service.smtplib.SMTP") as smtp:
            assert EmailService().send_confirmation_email(test_db, mock_volunteer)

        message = smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
        html = next(
            part.get_payload(decode=True).decode()
            for part in message.walk()
            if part.get_content_type() == "text/html"
        )
        assert self.CONFIGURED in _body_of(html)

    def test_weekly_reminder(self, test_db, mock_volunteer):
        with (
            patch.object(sheets_service, "get_schedule_blocks", return_value=[]),
            patch.object(sheets_service, "get_schedule_sheets", return_value=[]),
        ):
            html, _ = EmailService().build_weekly_reminder_content(
                mock_volunteer, test_db
            )
        assert self.CONFIGURED in _body_of(html)

    def test_admin_weekly_reminder_send(self, admin_client, mock_volunteer):
        with (
            patch.object(sheets_service, "get_schedule_blocks", return_value=["block"]),
            patch.object(
                sheets_service,
                "get_current_schedule_dates",
                return_value=(datetime.now(), datetime.now()),
            ),
            patch.object(
                email_service,
                "build_class_table",
                return_value={
                    "class_name": "Test Class",
                    "table_html": "<table></table>",
                    "has_data": True,
                    "needs_volunteers": True,
                },
            ),
            patch.object(email_service, "send_custom_email") as send,
        ):
            response = admin_client.post("/admin/send-weekly-reminders")

        assert response.json()["status"] == "success"
        assert self.CONFIGURED in _body_of(send.call_args.kwargs["html_body"])
