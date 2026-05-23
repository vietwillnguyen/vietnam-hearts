"""
TDD tests for Zalo group chat integration.

Covers:
- ConfigHelper.get_invite_link_zalo() reads INVITE_LINK_ZALO from DB settings
- Confirmation email template renders Zalo link (not Discord/FB Messenger group chat)
- Weekly reminder email template renders Zalo link (not Discord)
- Base email footer renders Zalo link (not Discord)
"""

import os
import pytest
from unittest.mock import MagicMock
from jinja2 import Environment, FileSystemLoader

from app.utils.config_helper import ConfigHelper
from app.services.settings_service import get_setting, set_setting

ZALO_URL = "https://zalo.me/g/gcmgkowx6gvotsghvsji"

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "email")


@pytest.fixture
def jinja_env():
    return Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=False)


class TestConfigHelperZalo:
    """Unit tests for ConfigHelper.get_invite_link_zalo()"""

    def test_get_invite_link_zalo_returns_default_when_db_is_none(self):
        result = ConfigHelper.get_invite_link_zalo(db=None, default="fallback")
        assert result == "fallback"

    def test_get_invite_link_zalo_returns_empty_default_when_db_is_none(self):
        result = ConfigHelper.get_invite_link_zalo(db=None)
        assert result == ""

    def test_get_invite_link_zalo_reads_from_db(self, test_db):
        set_setting(test_db, "INVITE_LINK_ZALO", ZALO_URL)
        test_db.commit()

        result = ConfigHelper.get_invite_link_zalo(test_db)
        assert result == ZALO_URL

    def test_get_invite_link_zalo_returns_default_when_key_missing(self, test_db):
        result = ConfigHelper.get_invite_link_zalo(test_db, default="fallback")
        assert result == "fallback"


class TestConfirmationEmailTemplate:
    """Tests that confirmation-email.html renders Zalo link correctly"""

    def _render(self, jinja_env, extra_vars=None):
        template = jinja_env.get_template("confirmation-email.html")
        vars = {
            "UserFullName": "Test User",
            "INVITE_LINK_ZALO": ZALO_URL,
            "INVITE_LINK_DISCORD": "#",
            "INVITE_LINK_FACEBOOK_MESSENGER": "#",
            "ONBOARDING_GUIDE_LINK": "https://example.com/onboarding",
            "SCHEDULE_SIGNUP_LINK": "https://example.com/schedule",
            "INSTAGRAM_LINK": "https://instagram.com/vietnamhearts",
            "FACEBOOK_PAGE_LINK": "https://facebook.com/vietnamhearts",
            "EMAIL_PREFERENCES_LINK": "https://example.com/prefs",
        }
        if extra_vars:
            vars.update(extra_vars)
        return template.render(**vars)

    def test_confirmation_email_contains_zalo_link(self, jinja_env):
        html = self._render(jinja_env)
        assert ZALO_URL in html

    def test_confirmation_email_says_zalo(self, jinja_env):
        html = self._render(jinja_env)
        assert "Zalo" in html

    def test_confirmation_email_does_not_direct_to_discord_group(self, jinja_env):
        html = self._render(jinja_env)
        # Should not invite users to join Discord community as a group chat
        assert "Join Discord" not in html
        assert "Join our Discord" not in html

    def test_confirmation_email_does_not_direct_to_facebook_messenger(self, jinja_env):
        html = self._render(jinja_env)
        assert "Join Facebook chat" not in html
        assert "Join our Facebook Messenger Chat" not in html
        assert "Facebook Messenger Chat" not in html

    def test_confirmation_email_location_note_references_zalo(self, jinja_env):
        html = self._render(jinja_env)
        # The school location paragraph should point to Zalo, not Discord
        assert "Discord Group Chat" not in html


class TestWeeklyReminderEmailTemplate:
    """Tests that weekly-reminder-email.html renders Zalo link correctly"""

    def _render(self, jinja_env):
        template = jinja_env.get_template("weekly-reminder-email.html")
        return template.render(
            first_name="Test",
            class_tables=["<table><tr><td>Sample</td></tr></table>"],
            SCHEDULE_SIGNUP_LINK="https://example.com/schedule",
            INVITE_LINK_ZALO=ZALO_URL,
            INVITE_LINK_DISCORD="#",
            INVITE_LINK_FACEBOOK_MESSENGER="#",
            ONBOARDING_GUIDE_LINK="https://example.com/onboarding",
            INSTAGRAM_LINK="https://instagram.com/vietnamhearts",
            FACEBOOK_PAGE_LINK="https://facebook.com/vietnamhearts",
            EMAIL_PREFERENCES_LINK="https://example.com/prefs",
        )

    def test_weekly_reminder_contains_zalo_link(self, jinja_env):
        html = self._render(jinja_env)
        assert ZALO_URL in html

    def test_weekly_reminder_says_zalo(self, jinja_env):
        html = self._render(jinja_env)
        assert "Zalo" in html

    def test_weekly_reminder_does_not_direct_to_discord(self, jinja_env):
        html = self._render(jinja_env)
        assert "Discord" not in html


class TestBaseEmailFooter:
    """Tests that base_email.html footer renders Zalo link instead of Discord"""

    def _render_footer(self, jinja_env):
        # Use weekly-reminder to get the full base template rendered
        template = jinja_env.get_template("weekly-reminder-email.html")
        return template.render(
            first_name="Test",
            class_tables=[],
            SCHEDULE_SIGNUP_LINK="#",
            INVITE_LINK_ZALO=ZALO_URL,
            INVITE_LINK_DISCORD="#",
            INVITE_LINK_FACEBOOK_MESSENGER="#",
            ONBOARDING_GUIDE_LINK="#",
            INSTAGRAM_LINK="#",
            FACEBOOK_PAGE_LINK="#",
            EMAIL_PREFERENCES_LINK="#",
        )

    def test_base_email_footer_contains_zalo_link(self, jinja_env):
        html = self._render_footer(jinja_env)
        assert ZALO_URL in html

    def test_base_email_footer_does_not_link_to_discord(self, jinja_env):
        html = self._render_footer(jinja_env)
        # The footer social link should be Zalo, not Discord
        assert "Discord" not in html
