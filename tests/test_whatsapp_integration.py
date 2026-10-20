"""
TDD tests for the WhatsApp community link, offered beside Zalo.

Some volunteers cannot register with Zalo, so every place that points them to the
Zalo group also offers the WhatsApp community as the fallback.

Covers:
- INVITE_LINK_WHATSAPP is a default setting, seeded with the community link
- ConfigHelper.get_invite_link_whatsapp() reads it from DB settings
- Confirmation, weekly reminder and base footer render the WhatsApp link
- An empty link hides the WhatsApp line rather than rendering a dead "#" link
- Every renderer that passes INVITE_LINK_ZALO also passes INVITE_LINK_WHATSAPP
"""

import os
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

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


def test_every_renderer_that_passes_zalo_also_passes_whatsapp():
    """The link is a render variable, so a call site that forgets it renders the
    template with the WhatsApp line silently hidden."""
    for path in (REPO_ROOT / "app").rglob("*.py"):
        text = path.read_text()
        zalo = len(re.findall(r"INVITE_LINK_ZALO[\"']?\s*[=:]", text))
        whatsapp = len(re.findall(r"INVITE_LINK_WHATSAPP[\"']?\s*[=:]", text))
        if path.name == "settings_service.py":
            continue
        assert zalo == whatsapp, (
            f"{path.relative_to(REPO_ROOT)} passes INVITE_LINK_ZALO {zalo} time(s) "
            f"and INVITE_LINK_WHATSAPP {whatsapp} time(s)"
        )
