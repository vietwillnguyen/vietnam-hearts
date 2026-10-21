"""Delivery behaviour of the Sunday weekly-reminder job.

Pins down the four-week outage (2026-09-27..2026-10-18) in which the job
alternated 403s and 504s at the Cloud Run 300s limit and nothing was sent.
The delivery half of that failure was cost and bookkeeping:

- one SMTP connect + STARTTLS + AUTH per recipient, measured at ~1.85s each
  from Cloud Run, so the run grew past the deadline as volunteers accumulated;
- every row committed PENDING before the first send, so a run killed at the
  deadline orphaned them forever;
- send_custom_email swallows SMTP errors and returns False, but the endpoint
  ignored the return value and marked every row "sent" regardless - which also
  means a resumed run cannot trust "sent" unless that is fixed first.
"""

import secrets
import smtplib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event

from app.main import app
from app.models import EmailCommunication as EmailCommunicationModel
from app.models import Volunteer as VolunteerModel
from app.routers.admin.helpers import WEEKLY_REMINDER_BULK_TEMPLATE
from app.services.email_service import email_service
from app.services.google_sheets import sheets_service
from app.utils.config_helper import ConfigHelper

WEEK_MONDAY = datetime(2026, 10, 19)
WEEK_FRIDAY = datetime(2026, 10, 23)


class RecordingSMTP:
    """Stands in for smtplib.SMTP and counts what a batch actually costs."""

    connects = 0
    logins = 0
    sent = []
    # Raised by every send_message: a permanent per-recipient rejection.
    reject_every_send = False
    # Recipient positions (1-based) whose first send attempt drops the session.
    drop_session_before = ()
    _attempts = 0

    @classmethod
    def reset(cls):
        cls.connects = 0
        cls.logins = 0
        cls.sent = []
        cls.reject_every_send = False
        cls.drop_session_before = ()
        cls._attempts = 0

    def __init__(self, host, port, *args, **kwargs):
        type(self).connects += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, *args, **kwargs):
        pass

    def login(self, *args, **kwargs):
        type(self).logins += 1

    def send_message(self, message, *args, **kwargs):
        cls = type(self)
        cls._attempts += 1
        if cls.reject_every_send:
            raise smtplib.SMTPRecipientsRefused({message["To"]: (550, b"no such user")})
        if cls._attempts in cls.drop_session_before:
            raise smtplib.SMTPServerDisconnected("connection closed by server")
        cls.sent.append(message["To"])

    def quit(self):
        pass


@pytest.fixture
def smtp():
    RecordingSMTP.reset()
    with patch("app.services.email_service.smtplib.SMTP", RecordingSMTP):
        yield RecordingSMTP


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
def subscribed_volunteers(test_db):
    def make(count):
        volunteers = []
        for i in range(count):
            volunteer = VolunteerModel(
                name=f"Volunteer {i}",
                email=f"v{i}@example.com",
                positions=["Teacher"],
                email_unsubscribe_token=secrets.token_urlsafe(32),
                weekly_reminders_subscribed=True,
                all_emails_subscribed=True,
                is_active=True,
            )
            test_db.add(volunteer)
            volunteers.append(volunteer)
        test_db.commit()
        return volunteers

    return make


@pytest.fixture
def sending_week():
    """A week with an open slot, so the endpoint actually sends."""
    with (
        patch.object(ConfigHelper, "get_weekly_reminders_enabled", return_value=True),
        patch.object(ConfigHelper, "get_dry_run", return_value=False),
        patch.object(sheets_service, "get_schedule_blocks", return_value=["block"]),
        patch.object(
            sheets_service,
            "get_current_schedule_dates",
            return_value=(WEEK_MONDAY, WEEK_FRIDAY),
        ),
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
    ):
        yield


@pytest.fixture
def other_services_healthy():
    """Keep /admin/health off the network so only the reminder verdict moves it."""
    bot = MagicMock()
    bot.return_value.get_knowledge_status = AsyncMock(
        return_value={
            "knowledge_service_available": True,
            "embeddings_available": True,
            "gemini_available": True,
            "document_service_available": True,
            "supabase_available": True,
            "documents_count": 1,
        }
    )
    with (
        patch("app.services.auth_service.auth_service.admin_supabase", object()),
        patch(
            "app.services.auth_service.auth_service._check_admin_db",
            AsyncMock(return_value=True),
        ),
        patch("app.services.bot_service.BotService", bot),
    ):
        yield


def reminder_rows(db):
    return (
        db.query(EmailCommunicationModel)
        .filter(EmailCommunicationModel.email_type == "weekly_reminder")
        .all()
    )


def status_counts(db):
    counts = {}
    for row in reminder_rows(db):
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


class TestBatchSmtpCost:
    """The run has to fit a request deadline, so the batch shares one session."""

    def test_sends_whole_batch_over_one_smtp_session(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(25)

        response = admin_client.post("/admin/send-weekly-reminders")

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert len(smtp.sent) == 25
        assert smtp.connects == 1, (
            f"{smtp.connects} SMTP connections for 25 recipients; a connect, "
            "STARTTLS handshake and AUTH per message is what blew the deadline"
        )
        assert smtp.logins == 1


class TestMidBatchDisconnect:
    """One shared session must not turn a dropped socket into a lost run."""

    def test_reconnects_and_finishes_the_batch(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(4)
        smtp.drop_session_before = (2,)

        admin_client.post("/admin/send-weekly-reminders")

        assert len(smtp.sent) == 4
        assert smtp.connects == 2, "expected exactly one reconnect"
        assert status_counts(test_db) == {"sent": 4}


class TestFailedSendsAreRecordedAsFailed:
    def test_refused_sends_are_not_recorded_as_sent(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(3)
        smtp.reject_every_send = True

        response = admin_client.post("/admin/send-weekly-reminders")

        assert response.status_code == 200
        assert status_counts(test_db) == {"failed": 3}
        assert all(row.error_message for row in reminder_rows(test_db))

    def test_does_not_reconnect_for_a_permanent_rejection(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        """A refused recipient is not a broken session; keep the one connect."""
        subscribed_volunteers(3)
        smtp.reject_every_send = True

        admin_client.post("/admin/send-weekly-reminders")

        assert smtp.connects == 1

    def test_reports_counts_so_a_total_failure_is_visible(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(3)
        smtp.reject_every_send = True

        body = admin_client.post("/admin/send-weekly-reminders").json()

        assert body["details"]["sent"] == 0
        assert body["details"]["failed"] == 3


class TestResumeAfterAnAbandonedRun:
    """A run killed at the deadline must be safe to retry."""

    def test_does_not_resend_to_volunteers_already_sent_this_week(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        already, pending = subscribed_volunteers(2)
        subject = email_service.get_reminder_subject(WEEK_MONDAY, WEEK_FRIDAY, 2)
        test_db.add(
            EmailCommunicationModel(
                volunteer_id=already.id,
                recipient_email=already.email,
                email_type="weekly_reminder",
                subject=subject,
                status="sent",
                sent_at=datetime.now(),
            )
        )
        test_db.commit()

        response = admin_client.post("/admin/send-weekly-reminders")

        assert response.status_code == 200
        assert smtp.sent == [pending.email]

    def test_does_not_resend_when_the_open_slot_count_changed(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        """A resumed run must not be fooled by its own subject changing.

        The subject now leads with the number of open slots, and that number
        moves the instant anyone signs up. A run killed at the deadline, then
        retried after a volunteer signed up, would compute a different subject
        - so keying the resume on the whole subject would re-email everyone the
        first attempt had already reached. The week's date range is the key
        instead.
        """
        already, pending = subscribed_volunteers(2)
        test_db.add(
            EmailCommunicationModel(
                volunteer_id=already.id,
                recipient_email=already.email,
                email_type="weekly_reminder",
                # Same week, a count no longer current.
                subject=email_service.get_reminder_subject(
                    WEEK_MONDAY, WEEK_FRIDAY, 99
                ),
                status="sent",
                sent_at=datetime.now(),
            )
        )
        test_db.commit()

        response = admin_client.post("/admin/send-weekly-reminders")

        assert response.status_code == 200
        assert smtp.sent == [pending.email]

    def test_resends_a_volunteer_whose_send_failed(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        (volunteer,) = subscribed_volunteers(1)
        subject = email_service.get_reminder_subject(WEEK_MONDAY, WEEK_FRIDAY, 2)
        test_db.add(
            EmailCommunicationModel(
                volunteer_id=volunteer.id,
                recipient_email=volunteer.email,
                email_type="weekly_reminder",
                subject=subject,
                status="failed",
                error_message="smtp server said no",
            )
        )
        test_db.commit()

        admin_client.post("/admin/send-weekly-reminders")

        assert smtp.sent == [volunteer.email]

    def test_sends_again_for_a_different_week(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        (volunteer,) = subscribed_volunteers(1)
        test_db.add(
            EmailCommunicationModel(
                volunteer_id=volunteer.id,
                recipient_email=volunteer.email,
                email_type="weekly_reminder",
                subject=email_service.get_reminder_subject(
                    WEEK_MONDAY - timedelta(days=7), WEEK_FRIDAY - timedelta(days=7), 2
                ),
                status="sent",
                sent_at=datetime.now() - timedelta(days=7),
            )
        )
        test_db.commit()

        admin_client.post("/admin/send-weekly-reminders")

        assert smtp.sent == [volunteer.email]


class TestStalePendingRows:
    """PENDING rows from an abandoned run never resolve on their own."""

    def test_marks_abandoned_pending_rows_failed(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        (volunteer,) = subscribed_volunteers(1)
        orphan = EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="weekly_reminder",
            subject="🗓️ Weekly Volunteer Reminder – Schedule Update (28/09 to 02/10)",
            status="PENDING",
        )
        test_db.add(orphan)
        test_db.commit()
        orphan.created_at = datetime.now() - timedelta(days=21)
        test_db.commit()

        admin_client.post("/admin/send-weekly-reminders")
        test_db.refresh(orphan)

        assert orphan.status == "failed"
        assert "abandoned" in (orphan.error_message or "").lower()

    def test_leaves_a_recent_pending_row_alone(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        """A concurrent run's in-flight rows must not be declared dead."""
        (volunteer,) = subscribed_volunteers(1)
        in_flight = EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="weekly_reminder",
            subject="some other run",
            status="PENDING",
        )
        test_db.add(in_flight)
        test_db.commit()

        admin_client.post("/admin/send-weekly-reminders")
        test_db.refresh(in_flight)

        assert in_flight.status == "PENDING"


class TestWeeklyReminderRunHealth:
    """Cloud Scheduler status is the only place a failed Sunday shows today."""

    def test_reports_ok_after_a_clean_run(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(2)
        admin_client.post("/admin/send-weekly-reminders")

        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "ok"
        assert body["last_run"]["sent"] == 2
        assert body["last_run"]["failed"] == 0

    def test_reports_failed_when_the_last_run_could_not_send(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(2)
        smtp.reject_every_send = True
        admin_client.post("/admin/send-weekly-reminders")

        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "failed"
        assert body["last_run"]["failed"] == 2

    def test_reports_stale_when_no_run_landed_for_over_a_week(
        self, admin_client, test_db, subscribed_volunteers
    ):
        (volunteer,) = subscribed_volunteers(1)
        old = EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="weekly_reminder",
            subject="🗓️ Weekly Volunteer Reminder – Schedule Update (21/09 to 25/09)",
            template_name=WEEKLY_REMINDER_BULK_TEMPLATE,
            status="sent",
            sent_at=datetime.now() - timedelta(days=30),
        )
        test_db.add(old)
        test_db.commit()
        old.created_at = datetime.now() - timedelta(days=30)
        test_db.commit()

        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "stale"
        assert body["days_since_last_run"] >= 29

    def test_reports_stale_when_nothing_was_ever_sent(self, admin_client, test_db):
        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "stale"
        assert body["last_run"] is None


class TestDuplicateRecipientAddresses:
    """Volunteer.email carries no unique constraint, so two rows can share one."""

    def test_records_an_outcome_for_every_row_sharing_an_address(
        self, admin_client, test_db, smtp, sending_week
    ):
        for name in ("Chi", "Chi's sister"):
            test_db.add(
                VolunteerModel(
                    name=name,
                    email="shared@example.com",
                    positions=["Teacher"],
                    email_unsubscribe_token=secrets.token_urlsafe(32),
                    weekly_reminders_subscribed=True,
                    all_emails_subscribed=True,
                    is_active=True,
                )
            )
        test_db.commit()

        admin_client.post("/admin/send-weekly-reminders")

        assert status_counts(test_db) == {"sent": 2}, (
            "a row left PENDING means its outcome was attributed to the other "
            "volunteer sharing the address"
        )


class TestSmtpUnavailable:
    """A bad GMAIL_APP_PASSWORD or a blocked port fails the whole run, not one."""

    def test_records_every_recipient_failed_without_retrying_each(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(5)

        attempts = []

        class RefusingSMTP:
            def __init__(self, host, port, *args, **kwargs):
                attempts.append((host, port))

            def starttls(self, *args, **kwargs):
                pass

            def login(self, *args, **kwargs):
                raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

            def quit(self):
                pass

            def close(self):
                pass

        with patch("app.services.email_service.smtplib.SMTP", RefusingSMTP):
            body = admin_client.post("/admin/send-weekly-reminders").json()

        assert body["details"] == {
            "sent": 0,
            "failed": 5,
            "skipped_already_sent": 0,
        }
        assert status_counts(test_db) == {"failed": 5}
        assert len(attempts) == 1, (
            f"{len(attempts)} connection attempts for an unusable server; the "
            "run should stop paying a connect per recipient once it is clear "
            "no session can be opened"
        )


class TestRunCutOffMidBatch:
    """Delivered rows must survive a run that dies before the batch ends."""

    def test_delivered_rows_read_sent_and_the_rerun_skips_them(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        volunteers = subscribed_volunteers(5)
        send_html_batch = email_service.send_html_batch

        def cut_off_after_two(messages):
            outcomes = send_html_batch(messages)
            yield next(outcomes)
            yield next(outcomes)
            raise RuntimeError("request cut off mid-batch")

        with patch.object(email_service, "send_html_batch", cut_off_after_two):
            body = admin_client.post("/admin/send-weekly-reminders").json()
        test_db.expire_all()

        assert body["status"] == "error"
        assert smtp.sent == [volunteers[0].email, volunteers[1].email]
        assert status_counts(test_db) == {"sent": 2, "PENDING": 3}

        smtp.sent = []
        admin_client.post("/admin/send-weekly-reminders")

        assert smtp.sent == [v.email for v in volunteers[2:]]


class TestDryRunRecording:
    """A send DRY_RUN skipped must not read as delivered."""

    def test_dry_run_row_does_not_make_the_real_run_skip_the_volunteer(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        (volunteer,) = subscribed_volunteers(1)
        subject = email_service.get_reminder_subject(WEEK_MONDAY, WEEK_FRIDAY, 2)
        with (
            patch.object(ConfigHelper, "get_dry_run", return_value=True),
            patch.object(
                ConfigHelper,
                "get_dry_run_email_recipient",
                return_value="captain@example.com",
            ),
        ):
            recorded = email_service.send_custom_email(
                to_email=volunteer.email,
                subject=subject,
                html_body="<p>hi</p>",
                db=test_db,
                volunteer_id=volunteer.id,
                email_type="weekly_reminder",
            )

        assert recorded is True
        assert smtp.sent == []
        assert status_counts(test_db) == {"dry_run": 1}

        admin_client.post("/admin/send-weekly-reminders")

        assert smtp.sent == [volunteer.email]

    def test_reminder_stats_count_dry_run_separately(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(1)
        admin_client.post("/admin/send-weekly-reminders")
        (volunteer,) = subscribed_volunteers(1)
        with (
            patch.object(ConfigHelper, "get_dry_run", return_value=True),
            patch.object(
                ConfigHelper,
                "get_dry_run_email_recipient",
                return_value="captain@example.com",
            ),
        ):
            email_service.send_custom_email(
                to_email=volunteer.email,
                subject="manual",
                html_body="<p>hi</p>",
                db=test_db,
                volunteer_id=volunteer.id,
                email_type="weekly_reminder",
            )

        stats = admin_client.get("/admin/reminder-stats").json()["statistics"]

        assert stats["total_sent"] == 1
        assert stats["successful"] == 1
        assert stats["failed"] == 0
        assert stats["dry_run"] == 1
        assert stats["success_rate"] == 100


class TestRunHealthPerRecipient:
    """A resumed run that reaches everyone must clear the failure."""

    def test_reports_ok_after_a_resume_delivers_the_failed_sends(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(3)
        smtp.reject_every_send = True
        admin_client.post("/admin/send-weekly-reminders")
        smtp.reject_every_send = False
        admin_client.post("/admin/send-weekly-reminders")

        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "ok"
        assert body["last_run"]["recipients"] == 3
        assert body["last_run"]["sent"] == 3
        assert body["last_run"]["failed"] == 0

    def test_a_manual_send_does_not_mask_a_failed_bulk_run(
        self,
        admin_client,
        test_db,
        smtp,
        sending_week,
        subscribed_volunteers,
        other_services_healthy,
    ):
        volunteer, _ = subscribed_volunteers(2)
        smtp.reject_every_send = True
        admin_client.post("/admin/send-weekly-reminders")
        smtp.reject_every_send = False

        manual = admin_client.post(
            f"/admin/volunteers/{volunteer.id}/send-weekly-reminder"
        )
        assert manual.status_code == 200

        body = admin_client.get("/admin/weekly-reminder-health").json()
        assert body["status"] == "failed"
        assert body["last_run"]["recipients"] == 2

        health = admin_client.get("/admin/health").json()
        assert health["services"]["weekly_reminders"]["status"] == "failed"
        assert health["status"] == "degraded"

    def test_a_manual_send_alone_is_not_a_run(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        (volunteer,) = subscribed_volunteers(1)

        admin_client.post(f"/admin/volunteers/{volunteer.id}/send-weekly-reminder")
        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "stale"
        assert body["last_run"] is None


class TestRunHealthUnderDryRun:
    """DRY_RUN left on in prod means no volunteer is reminded."""

    def test_reports_dry_run_rather_than_ok(
        self,
        admin_client,
        test_db,
        smtp,
        sending_week,
        subscribed_volunteers,
        other_services_healthy,
    ):
        captain, _ = subscribed_volunteers(2)
        with (
            patch.object(ConfigHelper, "get_dry_run", return_value=True),
            patch.object(
                ConfigHelper, "get_dry_run_email_recipient", return_value=captain.email
            ),
        ):
            admin_client.post("/admin/send-weekly-reminders")
            body = admin_client.get("/admin/weekly-reminder-health").json()
            health = admin_client.get("/admin/health").json()

        assert smtp.sent == [captain.email]
        assert body["status"] == "dry_run"
        assert body["dry_run"] is True
        assert health["status"] == "degraded"

    def test_reports_dry_run_off_in_the_payload(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(1)
        admin_client.post("/admin/send-weekly-reminders")

        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "ok"
        assert body["dry_run"] is False

    def test_admin_health_is_healthy_after_a_clean_run(
        self,
        admin_client,
        test_db,
        smtp,
        sending_week,
        subscribed_volunteers,
        other_services_healthy,
    ):
        subscribed_volunteers(1)
        admin_client.post("/admin/send-weekly-reminders")

        health = admin_client.get("/admin/health").json()

        assert health["services"]["weekly_reminders"]["status"] == "ok"
        assert health["status"] == "healthy", health["services"]

    def test_a_dry_run_bulk_test_does_not_pass_for_a_later_real_run(
        self,
        admin_client,
        test_db,
        smtp,
        sending_week,
        subscribed_volunteers,
        other_services_healthy,
    ):
        captain, other = subscribed_volunteers(2)
        with (
            patch.object(ConfigHelper, "get_dry_run", return_value=True),
            patch.object(
                ConfigHelper, "get_dry_run_email_recipient", return_value=captain.email
            ),
        ):
            admin_client.post("/admin/send-weekly-reminders")

        assert status_counts(test_db) == {"dry_run": 1}

        body = admin_client.get("/admin/weekly-reminder-health").json()
        health = admin_client.get("/admin/health").json()

        assert body["status"] != "ok"
        assert body["last_run"] is None
        assert health["status"] == "degraded"

        smtp.sent = []
        admin_client.post("/admin/send-weekly-reminders")

        assert smtp.sent == [captain.email, other.email]


class TestBatchDatabaseCost:
    """Per-outcome commits must not cost a row reload per message."""

    def test_does_not_reload_reminder_rows_while_sending(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        subscribed_volunteers(6)
        reads = []

        def count_reads(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("SELECT") and (
                "email_communications" in statement
            ):
                reads.append(statement)

        engine = test_db.get_bind()
        event.listen(engine, "before_cursor_execute", count_reads)
        try:
            admin_client.post("/admin/send-weekly-reminders")
        finally:
            event.remove(engine, "before_cursor_execute", count_reads)

        assert status_counts(test_db) == {"sent": 6}
        assert len(reads) <= 2, (
            f"{len(reads)} reads of email_communications for 6 messages; the "
            "batch should not re-select each row after every commit"
        )


class TestDryRunRowsNeverReadAsARealRun:
    """A DRY_RUN test's row must stay out of run health whatever its outcome."""

    def dry_run_bulk(self, admin_client, recipient):
        with (
            patch.object(ConfigHelper, "get_dry_run", return_value=True),
            patch.object(
                ConfigHelper, "get_dry_run_email_recipient", return_value=recipient
            ),
        ):
            admin_client.post("/admin/send-weekly-reminders")

    def test_a_failed_dry_run_send_is_not_the_last_run(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        captain, _ = subscribed_volunteers(2)
        smtp.reject_every_send = True
        self.dry_run_bulk(admin_client, captain.email)

        assert status_counts(test_db) == {"failed": 1}

        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "stale"
        assert body["last_run"] is None

    def test_a_failed_dry_run_send_does_not_fail_a_clean_real_run(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        (volunteer,) = subscribed_volunteers(1)
        captain = VolunteerModel(
            name="Captain",
            email="captain@example.com",
            positions=["Teacher"],
            email_unsubscribe_token=secrets.token_urlsafe(32),
            weekly_reminders_subscribed=False,
            all_emails_subscribed=True,
            is_active=True,
        )
        test_db.add(captain)
        test_db.commit()
        smtp.reject_every_send = True
        self.dry_run_bulk(admin_client, captain.email)
        smtp.reject_every_send = False

        admin_client.post("/admin/send-weekly-reminders")
        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert smtp.sent == [volunteer.email]
        assert body["status"] == "ok"
        assert body["last_run"]["recipients"] == 1

    def test_an_abandoned_dry_run_row_reaped_failed_is_not_a_run(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        captain, _ = subscribed_volunteers(2)

        def cut_off_before_sending(messages):
            raise RuntimeError("request cut off before the first send")
            yield

        with patch.object(email_service, "send_html_batch", cut_off_before_sending):
            self.dry_run_bulk(admin_client, captain.email)

        (row,) = reminder_rows(test_db)
        assert row.status == "PENDING"
        row.created_at = datetime.now() - timedelta(days=1)
        test_db.commit()

        with (
            patch.object(sheets_service, "get_schedule_blocks", return_value=[]),
            patch.object(
                email_service,
                "build_class_table",
                return_value={
                    "table_html": "",
                    "needs_volunteers": False,
                    "open_slot_count": 0,
                },
            ),
        ):
            admin_client.post("/admin/send-weekly-reminders")
        test_db.expire_all()

        assert status_counts(test_db) == {"failed": 1}

        body = admin_client.get("/admin/weekly-reminder-health").json()

        assert body["status"] == "stale"
        assert body["last_run"] is None


class TestDryRunRowsStayOutOfReminderStats:
    """A DRY_RUN test is never a real failure in the stats the captain reads."""

    def test_a_failed_dry_run_bulk_send_is_not_counted_as_a_failure(
        self, admin_client, test_db, smtp, sending_week, subscribed_volunteers
    ):
        captain, _ = subscribed_volunteers(2)
        smtp.reject_every_send = True
        with (
            patch.object(ConfigHelper, "get_dry_run", return_value=True),
            patch.object(
                ConfigHelper, "get_dry_run_email_recipient", return_value=captain.email
            ),
        ):
            admin_client.post("/admin/send-weekly-reminders")
        (row,) = reminder_rows(test_db)
        row.sent_at = datetime.now()
        test_db.commit()

        assert status_counts(test_db) == {"failed": 1}

        stats = admin_client.get("/admin/reminder-stats").json()["statistics"]
        logs = admin_client.get("/admin/email-logs").json()["emails"]

        assert stats["failed"] == 0
        assert stats["total_sent"] == 0
        assert stats["dry_run"] == 1
        assert all(week["failed"] == 0 for week in stats["weekly_stats"].values())
        assert sum(week["dry_run"] for week in stats["weekly_stats"].values()) == 1
        assert [(e["status"], e["dry_run"]) for e in logs] == [("failed", True)]
