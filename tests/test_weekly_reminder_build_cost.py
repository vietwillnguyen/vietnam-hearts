"""What one Sunday weekly-reminder run is allowed to cost per recipient.

The 2026-10-25 production run sent all 176 emails but took 547s, so Cloud Run
returned 504 at its 300s limit and Cloud Scheduler recorded the job FAILED.
Sharing the SMTP session (PR #67) had already taken the per-message network
cost out; what was left was the *content*, rebuilt from scratch for every
recipient - two Google Sheets reads and six Supabase settings reads a head,
all of them returning the same thing for the whole list.

These tests pin the shape of the fix rather than a wall-clock number, which
would be flaky: the shared half of the email is built a fixed number of times
per run, no matter how long the recipient list is.
"""

import secrets
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from app.main import app
from app.models import EmailCommunication as EmailCommunicationModel
from app.models import Volunteer as VolunteerModel
from app.services.email_service import email_service
from app.services.google_sheets import sheets_service
from app.utils.config_helper import ConfigHelper

WEEK_MONDAY = datetime(2026, 10, 19)
WEEK_FRIDAY = datetime(2026, 10, 23)

# Enough recipients that a per-volunteer round trip is unmistakable in the
# counts, while the test itself stays fast because nothing here sleeps.
RECIPIENTS = 30


class SilentSMTP:
    """A shared SMTP session that neither touches the network nor costs time."""

    def __init__(self, host, port, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, *args, **kwargs):
        pass

    def login(self, *args, **kwargs):
        pass

    def send_message(self, message, *args, **kwargs):
        pass

    def quit(self):
        pass


@pytest.fixture
def admin_client(client):
    from app.dependencies.auth import get_current_admin_user

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "id": "service-account-auto-scheduler",
        "email": "auto-scheduler@example.iam.gserviceaccount.com",
        "is_service_account": True,
    }
    yield client
    app.dependency_overrides.pop(get_current_admin_user, None)


@pytest.fixture
def counting_sheets():
    """Count every Google Sheets round trip the run makes."""
    calls = {"get_schedule_blocks": 0, "get_current_schedule_dates": 0}

    def get_schedule_blocks(db):
        calls["get_schedule_blocks"] += 1
        return [MagicMock()]

    def get_current_schedule_dates(db):
        calls["get_current_schedule_dates"] += 1
        return (WEEK_MONDAY, WEEK_FRIDAY)

    with (
        patch.object(sheets_service, "get_schedule_blocks", get_schedule_blocks),
        patch.object(
            sheets_service, "get_current_schedule_dates", get_current_schedule_dates
        ),
    ):
        yield calls


# The links that go into the reminder template; each getter is one Supabase
# round trip, and all six return the same value for every recipient.
LINK_GETTERS = (
    "get_schedule_signup_link",
    "get_invite_link_zalo",
    "get_invite_link_whatsapp",
    "get_onboarding_guide_link",
    "get_instagram_link",
    "get_facebook_page_link",
)


@pytest.fixture
def counting_settings():
    """Count every settings read, i.e. every Supabase round trip for a link."""
    calls = Counter()

    def counter(name):
        real = getattr(ConfigHelper, name)

        def counted(db, default=""):
            calls[name] += 1
            return real(db, default)

        return counted

    with ExitStack() as stack:
        for name in LINK_GETTERS:
            stack.enter_context(patch.object(ConfigHelper, name, counter(name)))
        yield calls


@pytest.fixture
def sending_week():
    """A week with an open slot, so the endpoint actually sends."""
    with (
        patch.object(ConfigHelper, "get_weekly_reminders_enabled", return_value=True),
        patch.object(ConfigHelper, "get_dry_run", return_value=False),
        patch.object(
            email_service,
            "build_class_table",
            return_value={
                "class_name": "Test Class",
                "table_html": "<table></table>",
                "has_data": True,
                "needs_volunteers": True,
                "open_slot_count": 2,
            },
        ),
        patch("app.services.email_service.smtplib.SMTP", SilentSMTP),
    ):
        yield


@pytest.fixture
def subscribed_volunteers(test_db):
    def make(count, with_token=True):
        for i in range(count):
            test_db.add(
                VolunteerModel(
                    name=f"Volunteer {i}",
                    email=f"v{i}@example.com",
                    positions=["Teacher"],
                    email_unsubscribe_token=(
                        secrets.token_urlsafe(32) if with_token else None
                    ),
                    weekly_reminders_subscribed=True,
                    all_emails_subscribed=True,
                    is_active=True,
                )
            )
        test_db.commit()

    return make


class TestSharedContentIsBuiltOncePerRun:
    """The expensive half of the email does not depend on who receives it."""

    def test_sheets_are_read_once_however_many_recipients(
        self,
        admin_client,
        test_db,
        sending_week,
        counting_sheets,
        subscribed_volunteers,
    ):
        subscribed_volunteers(RECIPIENTS)

        response = admin_client.post("/admin/send-weekly-reminders")

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert (
            test_db.query(EmailCommunicationModel)
            .filter(EmailCommunicationModel.status == "sent")
            .count()
            == RECIPIENTS
        )
        assert counting_sheets["get_schedule_blocks"] == 1, (
            f"{counting_sheets['get_schedule_blocks']} schedule-block reads for "
            f"{RECIPIENTS} recipients; the schedule is the same for all of them "
            "and each read is a Google Sheets round trip"
        )
        assert counting_sheets["get_current_schedule_dates"] == 1, (
            f"{counting_sheets['get_current_schedule_dates']} schedule-date reads "
            f"for {RECIPIENTS} recipients"
        )

    def test_settings_links_are_read_once_however_many_recipients(
        self,
        admin_client,
        test_db,
        sending_week,
        counting_sheets,
        counting_settings,
        subscribed_volunteers,
    ):
        subscribed_volunteers(RECIPIENTS)

        response = admin_client.post("/admin/send-weekly-reminders")

        assert response.status_code == 200
        # Six links went into the template for every recipient; each one is a
        # Supabase round trip, and at 176 recipients that was ~1.5s a head.
        for name in LINK_GETTERS:
            assert counting_settings[name] == 1, (
                f"{name} was read {counting_settings[name]} times for "
                f"{RECIPIENTS} recipients; it is the same for all of them"
            )

    def test_missing_unsubscribe_tokens_cost_one_commit_for_the_batch(
        self,
        admin_client,
        test_db,
        sending_week,
        counting_sheets,
        subscribed_volunteers,
    ):
        subscribed_volunteers(RECIPIENTS, with_token=False)

        commits = []
        real_issue = email_service.ensure_unsubscribe_tokens

        def counted(volunteers, db):
            commits.append(len(list(volunteers)))
            return real_issue(volunteers, db)

        with patch.object(email_service, "ensure_unsubscribe_tokens", counted):
            response = admin_client.post("/admin/send-weekly-reminders")

        assert response.status_code == 200
        assert commits == [RECIPIENTS], (
            "tokens must be issued for the whole batch in one call: a commit "
            "per volunteer costs a round trip and expires every loaded row"
        )
        assert all(
            v.email_unsubscribe_token for v in test_db.query(VolunteerModel).all()
        )

    def test_issuing_missing_tokens_does_not_reload_each_volunteer(
        self,
        admin_client,
        test_db,
        test_engine,
        sending_week,
        counting_sheets,
        subscribed_volunteers,
    ):
        subscribed_volunteers(RECIPIENTS, with_token=False)

        reloads = []

        def count_reloads(conn, cursor, statement, parameters, context, many):
            # Refreshing one expired row selects that volunteer by primary
            # key; the run itself only ever loads the recipient list in bulk.
            if (
                statement.lstrip().upper().startswith("SELECT")
                and "FROM volunteers" in statement
                and "WHERE volunteers.id = " in statement
            ):
                reloads.append(statement)

        event.listen(test_engine, "before_cursor_execute", count_reloads)
        try:
            response = admin_client.post("/admin/send-weekly-reminders")
        finally:
            event.remove(test_engine, "before_cursor_execute", count_reloads)

        assert response.status_code == 200
        assert reloads == [], (
            f"{len(reloads)} single-volunteer reloads for {RECIPIENTS} "
            "recipients: committing the issued tokens before rendering expires "
            "every loaded volunteer, and each reload is a database round trip"
        )
        assert all(
            v.email_unsubscribe_token for v in test_db.query(VolunteerModel).all()
        )


class TestSingleRecipientPathStillWorks:
    """The preview/test-send endpoint builds the shared half for itself."""

    def test_build_weekly_reminder_content_still_returns_body_and_subject(
        self, test_db, mock_volunteer, sending_week, counting_sheets
    ):
        html_body, subject = email_service.build_weekly_reminder_content(
            mock_volunteer, test_db
        )

        assert "<table></table>" in html_body
        assert mock_volunteer.name.split()[0] in html_body
        # Sunday end date, not the sheet's Friday: unchanged from before the
        # shared-content split.
        assert subject == email_service.get_reminder_subject(
            WEEK_MONDAY, datetime(2026, 10, 25), 2
        )
        assert counting_sheets["get_schedule_blocks"] == 1
