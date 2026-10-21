"""RFC 8058 one-click unsubscribe for the weekly reminder.

Gmail and Yahoo's bulk-sender rules expect a mail like this to carry
List-Unsubscribe and List-Unsubscribe-Post, and to honour a single POST with
no landing page and no confirmation step. Getting it wrong costs deliverability
for every volunteer, so the headers and the endpoint are pinned here.

Two properties matter beyond the happy path. A GET must never unsubscribe
anyone - mail clients and security scanners prefetch links, and RFC 8058 puts
the action behind POST for exactly that reason. And one-click stops the weekly
reminder only: the volunteer stays on announcements and keeps their account,
because the button sits in a weekly reminder and that is the mail they are
declining.
"""

import secrets

import pytest

from app.models import Volunteer as VolunteerModel


@pytest.fixture
def volunteer(test_db):
    v = VolunteerModel(
        name="Jane Volunteer",
        email="jane@example.com",
        email_unsubscribe_token=secrets.token_urlsafe(32),
        weekly_reminders_subscribed=True,
        all_emails_subscribed=True,
        is_active=True,
    )
    test_db.add(v)
    test_db.commit()
    test_db.refresh(v)
    return v


class TestOneClickEndpoint:
    def test_a_single_post_stops_the_weekly_reminder(self, client, test_db, volunteer):
        response = client.post(
            f"/unsubscribe/one-click?token={volunteer.email_unsubscribe_token}",
            data={"List-Unsubscribe": "One-Click"},
        )

        assert response.status_code == 200
        test_db.refresh(volunteer)
        assert volunteer.weekly_reminders_subscribed is False

    def test_it_leaves_announcements_and_the_account_alone(
        self, client, test_db, volunteer
    ):
        client.post(
            f"/unsubscribe/one-click?token={volunteer.email_unsubscribe_token}",
            data={"List-Unsubscribe": "One-Click"},
        )

        test_db.refresh(volunteer)
        assert volunteer.all_emails_subscribed is True
        assert volunteer.is_active is True

    def test_it_works_without_the_rfc_body(self, client, test_db, volunteer):
        # Senders vary in what they post; the token is the credential and the
        # URL already names the action, so the body is not required.
        response = client.post(
            f"/unsubscribe/one-click?token={volunteer.email_unsubscribe_token}"
        )

        assert response.status_code == 200
        test_db.refresh(volunteer)
        assert volunteer.weekly_reminders_subscribed is False

    def test_posting_twice_is_not_an_error(self, client, test_db, volunteer):
        # Mail providers retry, so the second POST must read as success rather
        # than bouncing the volunteer into an error page.
        url = f"/unsubscribe/one-click?token={volunteer.email_unsubscribe_token}"
        assert client.post(url).status_code == 200
        assert client.post(url).status_code == 200

        test_db.refresh(volunteer)
        assert volunteer.weekly_reminders_subscribed is False

    def test_a_get_never_unsubscribes_anyone(self, client, test_db, volunteer):
        # Clients and scanners prefetch links; an unsubscribe behind GET
        # unsubscribes people who never clicked.
        response = client.get(
            f"/unsubscribe/one-click?token={volunteer.email_unsubscribe_token}"
        )

        assert response.status_code == 405
        test_db.refresh(volunteer)
        assert volunteer.weekly_reminders_subscribed is True

    def test_an_unknown_token_changes_nothing(self, client, test_db, volunteer):
        response = client.post("/unsubscribe/one-click?token=not-a-real-token")

        assert response.status_code == 400
        test_db.refresh(volunteer)
        assert volunteer.weekly_reminders_subscribed is True

    def test_it_records_the_preference_change(self, client, test_db, volunteer):
        from app.models import EmailCommunication as EmailCommunicationModel

        client.post(f"/unsubscribe/one-click?token={volunteer.email_unsubscribe_token}")

        rows = (
            test_db.query(EmailCommunicationModel)
            .filter(EmailCommunicationModel.volunteer_id == volunteer.id)
            .all()
        )
        assert [r.email_type for r in rows] == ["preference_update_one_click"]


class TestReminderHeaders:
    """The headers that make the provider's own unsubscribe button appear."""

    def test_the_batch_message_carries_both_headers(self, test_db, volunteer):
        from app.services.email_service import EmailService

        service = EmailService()
        link = service.get_one_click_unsubscribe_link(volunteer, test_db)
        message = service._build_html_message(
            volunteer.email, "2 open slots this week (19/10-23/10)", "<p>hi</p>", link
        )

        assert message["List-Unsubscribe"] == f"<{link}>"
        assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_a_message_with_no_link_carries_neither(self, test_db, volunteer):
        # Confirmation and ad-hoc mail is not a subscribed list mailing, so it
        # must not advertise a list unsubscribe.
        from app.services.email_service import EmailService

        message = EmailService()._build_html_message(
            volunteer.email, "Welcome", "<p>hi</p>"
        )

        assert message["List-Unsubscribe"] is None
        assert message["List-Unsubscribe-Post"] is None

    def test_the_link_is_https_and_carries_the_token(self, test_db, volunteer):
        # RFC 8058 only allows List-Unsubscribe-Post alongside an https URI.
        from app.services.email_service import EmailService

        link = EmailService().get_one_click_unsubscribe_link(volunteer, test_db)

        assert "/unsubscribe/one-click?token=" in link
        assert volunteer.email_unsubscribe_token in link

    def test_the_bulk_send_sets_the_header_per_recipient(
        self, test_db, volunteer, monkeypatch
    ):
        """Each volunteer's header must carry their own token, not a shared one."""
        from app.services.email_service import EmailService

        second = VolunteerModel(
            name="Other Volunteer",
            email="other@example.com",
            email_unsubscribe_token=secrets.token_urlsafe(32),
            weekly_reminders_subscribed=True,
            all_emails_subscribed=True,
            is_active=True,
        )
        test_db.add(second)
        test_db.commit()

        service = EmailService()
        links = {
            v.email: service.get_one_click_unsubscribe_link(v, test_db)
            for v in (volunteer, second)
        }
        assert links[volunteer.email] != links[second.email]
