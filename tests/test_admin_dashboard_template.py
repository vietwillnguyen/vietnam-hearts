"""Tests for how the admin dashboard renders the Cron Job Schedules fields.

The three cron inputs were built by looking each key up in a nested
``{% for s in settings %}`` loop and assigning ``{% set current_value = s.value %}``
inside it. Jinja2 scopes ``{% set %}`` to the block it appears in, so the
assignment was discarded at ``{% endfor %}`` and every cron input rendered
``value=""`` - hidden behind a placeholder that happened to read like the
default expression.

Nothing looked wrong until the cadence became settings-driven: pressing
"Save All Settings" to edit any unrelated field PUT those empty strings back,
wiping all three CRON_* values, after which Apply to Cloud Scheduler could no
longer restore the schedule it was supposed to push.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import re

import pytest

from app.main import app
from app.services.settings_service import get_setting, set_setting

STORED_CRON_VALUES = {
    "CRON_SYNC_VOLUNTEERS": "0 */2 * * *",
    "CRON_SEND_WEEKLY_REMINDERS": "0 12 * * 0",
    "CRON_ROTATE_SCHEDULE": "0 * * * *",
    "CRON_POLL_INBOX": "0 8,18 * * *",
}


@pytest.fixture
def admin_client(client):
    from app.dependencies.auth import get_current_admin_user

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "id": "test-admin",
        "email": "admin@vietnamhearts.org",
    }
    yield client
    app.dependency_overrides.pop(get_current_admin_user, None)


def find_input(html: str, key: str) -> str:
    """The rendered <input> tag for a settings key."""
    match = re.search(rf'<input\b[^>]*\bid="{re.escape(key)}"[^>]*>', html)
    assert match, f"no <input> rendered for {key}"
    return match.group(0)


def attr(tag: str, name: str) -> str | None:
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', tag)
    return match.group(1) if match else None


class TestCronFieldsRenderStoredValues:
    @pytest.mark.parametrize("key,expected", list(STORED_CRON_VALUES.items()))
    def test_cron_input_carries_the_stored_value(
        self, admin_client, test_db, key: str, expected: str
    ):
        for setting_key, value in STORED_CRON_VALUES.items():
            set_setting(test_db, setting_key, value)

        response = admin_client.get("/admin/dashboard")
        assert response.status_code == 200

        assert attr(find_input(response.text, key), "value") == expected

    def test_a_saved_edit_survives_a_reload(self, admin_client, test_db):
        """The round trip the admin actually performs: edit, save, reload."""
        for setting_key, value in STORED_CRON_VALUES.items():
            set_setting(test_db, setting_key, value)

        edited = "15 3 * * 1"
        response = admin_client.put(
            "/settings/CRON_ROTATE_SCHEDULE", json={"value": edited}
        )
        assert response.status_code == 200

        reloaded = admin_client.get("/admin/dashboard")
        assert (
            attr(find_input(reloaded.text, "CRON_ROTATE_SCHEDULE"), "value") == edited
        )

    def test_save_all_settings_does_not_wipe_the_cadence(self, admin_client, test_db):
        """The reported failure, end to end.

        "Save All Settings" PUTs back every field the page rendered, so blank
        cron inputs silently replaced all three schedules on any unrelated
        edit - and Apply to Cloud Scheduler then had nothing left to push.
        """
        for setting_key, value in STORED_CRON_VALUES.items():
            set_setting(test_db, setting_key, value)

        html = admin_client.get("/admin/dashboard").text
        rendered = {
            key: attr(find_input(html, key), "value") for key in STORED_CRON_VALUES
        }

        for key, value in rendered.items():
            assert (
                admin_client.put(f"/settings/{key}", json={"value": value}).status_code
                == 200
            )

        for key, expected in STORED_CRON_VALUES.items():
            assert get_setting(test_db, key) == expected

    def test_missing_value_looks_empty_rather_than_populated(
        self, admin_client, test_db
    ):
        """A genuinely unset field must not wear the default as a placeholder.

        The placeholder was the example expression, so an empty field was
        visually indistinguishable from a configured one - which is why the
        blanking went unnoticed.
        """
        set_setting(test_db, "CRON_ROTATE_SCHEDULE", "")

        response = admin_client.get("/admin/dashboard")
        tag = find_input(response.text, "CRON_ROTATE_SCHEDULE")

        assert attr(tag, "value") == ""
        assert attr(tag, "placeholder") != "0 * * * *"


class TestInboxBotCard:
    """The card an operator reads to know what the bot is doing.

    The mode badge and the error banner are rendered server-side from the
    settings already in context, so they are correct on first paint rather than
    after a fetch. The counters and the escalation list come from
    ``/admin/email-bot/*`` because they change every run.
    """

    def test_the_card_is_present(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "Inbox Bot" in html
        assert 'id="inbox-bot-card"' in html

    @pytest.mark.parametrize("mode", ["off", "draft", "auto"])
    def test_the_mode_badge_shows_the_stored_mode(self, admin_client, test_db, mode):
        set_setting(test_db, "EMAIL_BOT_MODE", mode)
        html = admin_client.get("/admin/dashboard").text

        match = re.search(
            r'<span id="inbox-bot-mode"[^>]*>([^<]*)</span>', html, re.DOTALL
        )
        assert match, "the mode badge did not render"
        assert match.group(1).strip() == mode

    def test_off_says_plainly_that_nothing_happens(self, admin_client, test_db):
        set_setting(test_db, "EMAIL_BOT_MODE", "off")
        html = admin_client.get("/admin/dashboard").text
        assert "Nothing is read, labelled, or drafted while the mode is off." in html

    def test_draft_says_plainly_that_nothing_is_sent(self, admin_client, test_db):
        set_setting(test_db, "EMAIL_BOT_MODE", "draft")
        html = admin_client.get("/admin/dashboard").text
        assert "Nothing is sent." in html

    def test_the_banner_appears_when_a_run_recorded_an_error(
        self, admin_client, test_db
    ):
        set_setting(test_db, "EMAIL_BOT_LAST_ERROR", "the Gmail grant was revoked")
        html = admin_client.get("/admin/dashboard").text

        assert 'id="inbox-bot-banner"' in html
        assert "the Gmail grant was revoked" in html

    def test_the_banner_is_absent_after_a_clean_run(self, admin_client, test_db):
        # The pipeline clears the setting on a clean run, so a stale banner
        # would mean the problem is still there.
        set_setting(test_db, "EMAIL_BOT_LAST_ERROR", "")
        html = admin_client.get("/admin/dashboard").text
        assert 'id="inbox-bot-banner"' not in html

    def test_the_card_reads_the_run_and_escalation_endpoints(
        self, admin_client, test_db
    ):
        html = admin_client.get("/admin/dashboard").text
        assert "/admin/email-bot/runs" in html
        assert "/admin/email-bot/escalations" in html

    def test_the_card_offers_no_way_to_trigger_a_poll(self, admin_client, test_db):
        # Polling is the scheduler's job. A button that ran the bot on click
        # would make "twice a day" untrue and give the run lock something to
        # refuse.
        html = admin_client.get("/admin/dashboard").text
        assert "/admin/email-bot/poll" not in html

    def test_the_resume_button_posts_to_the_resume_endpoint(
        self, admin_client, test_db
    ):
        html = admin_client.get("/admin/dashboard").text
        assert "resumeInboxConversation" in html
        assert "/resume" in html

    def test_rendered_escalation_values_are_escaped(self, admin_client, test_db):
        # The list is built in JavaScript from API values, so it needs an
        # escaper rather than raw interpolation into innerHTML.
        html = admin_client.get("/admin/dashboard").text
        assert "function escapeHtml" in html
        assert "escapeHtml(item.category" in html


class TestPollInboxCronField:
    def test_the_new_cadence_field_renders_its_stored_value(
        self, admin_client, test_db
    ):
        set_setting(test_db, "CRON_POLL_INBOX", "30 7,19 * * *")
        html = admin_client.get("/admin/dashboard").text
        assert attr(find_input(html, "CRON_POLL_INBOX"), "value") == "30 7,19 * * *"

    def test_it_is_labelled_for_a_human(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "Poll Volunteer Inbox" in html


class TestAcceptanceAndAgreementOnTheCard:
    """The card says the same thing the design's gate does.

    The gates come from one table, ``ACCEPTANCE_GATES``, which the card's
    prose and the metrics endpoint both render, and which is held to the
    design's Evaluation record here, so the card and the design cannot drift
    into stating different gates.
    """

    def test_both_sections_are_present(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert 'id="inbox-bot-acceptance"' in html
        assert 'id="inbox-bot-agreement"' in html

    def test_the_card_reads_the_metrics_endpoint(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "/admin/email-bot/metrics" in html

    def test_the_gate_table_matches_the_design(self):
        from pathlib import Path

        from app.services.email_bot.gates import ACCEPTANCE_GATES

        design = (
            Path(__file__).resolve().parents[1]
            / "docs/superpowers/specs/2026-09-29-email-channel-design.md"
        )
        record = design.read_text().split("## Evaluation record", 1)[1]
        rows = {
            cells[0]: cells[2]
            for line in record.splitlines()
            if line.startswith("|")
            and len(cells := [cell.strip() for cell in line.strip("|").split("|")]) >= 3
        }

        def gate(measurement: str) -> float:
            found = re.fullmatch(r"at least (\d+) percent", rows[measurement])
            assert found, rows[measurement]
            return int(found.group(1)) / 100

        assert {
            "signup": gate("Sign-up drafts sent unchanged"),
            "faq": gate("FAQ drafts sent unchanged"),
        } == dict(ACCEPTANCE_GATES)

    def test_the_prose_renders_the_gate_table(self, admin_client, test_db, monkeypatch):
        from app.services.email_bot import gates

        monkeypatch.setattr(gates, "ACCEPTANCE_GATES", {"signup": 0.75, "faq": 0.6})
        html = admin_client.get("/admin/dashboard").text
        assert "the gate is 75% for sign-up drafts and 60% for FAQ drafts" in html

    def test_the_prose_states_what_the_measurement_is_for(self, admin_client, test_db):
        # An operator looking at this number has to know it is the thing that
        # decides whether the bot ever sends.
        html = admin_client.get("/admin/dashboard").text
        assert "sent automatically" in html

    def test_not_measured_yet_reads_differently_from_zero(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "not measured yet" in html

    def test_a_low_agreement_is_not_presented_as_a_fault(self, admin_client, test_db):
        # It means the two classifiers disagree about this inbox, which is
        # information, not an error.
        html = admin_client.get("/admin/dashboard").text
        assert "not itself a fault" in html

    def test_the_rendered_values_are_escaped(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "escapeHtml(kind)" in html


class TestTodaysSendingOnTheCard:
    """What auto mode has left today.

    Shown even while the mode is off, because an operator about to turn it on
    wants to know what it would be allowed to do before they do.
    """

    def test_the_section_is_present(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert 'id="inbox-bot-caps"' in html

    def test_it_says_a_reached_cap_drafts_rather_than_drops(
        self, admin_client, test_db
    ):
        # The thing an operator most needs to know about a cap here: running
        # out does not lose mail.
        html = admin_client.get("/admin/dashboard").text
        assert "A reached cap drafts the reply rather than dropping it" in html

    def test_it_says_the_day_is_the_local_one(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "Vietnam day" in html

    def test_the_renderer_is_wired_up(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "renderCaps" in html


class TestTheKnowledgeBaseSectionOnTheCard:
    def test_the_section_is_present(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert 'id="inbox-bot-knowledge-base"' in html

    def test_it_points_at_the_editor_guide(self, admin_client, test_db):
        # The people who edit the doc are coordinators, not engineers, so the
        # card has to tell them where the guide is.
        html = admin_client.get("/admin/dashboard").text
        assert "KNOWLEDGE_BASE_EDITING.md" in html

    def test_never_synced_is_worded_differently_from_stale(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "Never synced" in html
        assert "daily sync has probably stopped" in html

    def test_the_renderer_is_wired_up(self, admin_client, test_db):
        html = admin_client.get("/admin/dashboard").text
        assert "renderKnowledgeBase" in html
        assert "/admin/email-bot/metrics" in html


class TestTheBannerNamesTheRunbookForARevokedGrant:
    def test_a_revoked_grant_banner_shows_the_reason(self, admin_client, test_db):
        set_setting(
            test_db,
            "EMAIL_BOT_LAST_ERROR",
            "the Gmail refresh token no longer works; re-consent by the runbook "
            "in docs/GMAIL_BOT_SETUP.md",
        )
        html = admin_client.get("/admin/dashboard").text

        assert 'id="inbox-bot-banner"' in html
        # The reason the pipeline writes already names the runbook, so an
        # operator reading the banner knows where to go.
        assert "GMAIL_BOT_SETUP.md" in html
