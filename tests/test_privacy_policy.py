"""Tests for the public privacy policy page.

This page's URL goes on the Google OAuth consent screen for the volunteer
inbox, so two properties are load-bearing rather than cosmetic: Google's
reviewer fetches it unauthenticated, and Google requires the Limited Use
sentence to appear verbatim. Both are asserted here so a refactor that puts
the page behind auth, or an edit that paraphrases the disclosure, fails.
"""

import html

from fastapi.testclient import TestClient

from app.main import app
from app.routers.public import CONTACT_EMAIL, LIMITED_USE_DISCLOSURE

client = TestClient(app)


def test_privacy_page_is_reachable_without_authentication():
    response = client.get("/privacy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_privacy_page_carries_the_limited_use_disclosure_verbatim():
    # Unescaped, because Jinja renders the apostrophe in "Hearts'" as &#39; and
    # what Google requires is the sentence a reader sees, not the source bytes.
    body = html.unescape(client.get("/privacy").text)

    assert LIMITED_USE_DISCLOSURE in body
    assert (
        "Vietnam Hearts' use and transfer of information received from Google "
        "APIs will adhere to the Google API Services User Data Policy, "
        "including the Limited Use requirements" in body
    )
    assert "https://developers.google.com/terms/api-services-user-data-policy" in body


def test_privacy_page_carries_the_contact_address():
    body = client.get("/privacy").text

    assert CONTACT_EMAIL == "vietnam.hearts.volunteering@gmail.com"
    assert CONTACT_EMAIL in body


def test_privacy_page_names_the_services_that_receive_mail_content():
    """The consent screen is for Gmail access, so the inbox section is required."""
    body = client.get("/privacy").text

    assert "Volunteer inbox assistant" in body
    for service in ("Google Gemini", "LiteLLM", "Jev"):
        assert service in body, f"{service} is not disclosed on the privacy page"


def test_home_page_links_to_the_privacy_policy():
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/privacy"' in response.text
    assert "Privacy Policy" in response.text
