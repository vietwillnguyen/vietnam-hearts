"""The weekly reminder's subject line, and the run key hidden inside it.

The subject now leads with the number of open slots, so a volunteer can see
from their inbox whether the week needs them. That number changes the moment
anyone signs up, which matters more than it looks: the subject was *also* the
key that identifies a run, both for resuming a run cut off at the Cloud Run
deadline and for grouping a run's rows in the health report. Keying either on
the whole subject would mean a resumed run whose count had moved re-emailed
everyone it had already reached - undoing exactly what PR #67 fixed.

So the week's date range is the run key, and these tests pin that the count
can move without disturbing it.
"""

from datetime import datetime, timedelta

from app.models import EmailCommunication as EmailCommunicationModel
from app.routers.admin.helpers import (
    get_weekly_reminder_run_health,
    reminder_week_key_of,
)
from app.services.email_service import EmailService, reminder_week_key
from tests.test_email_service import _block

MONDAY = datetime(2026, 10, 19)
FRIDAY = datetime(2026, 10, 23)


class TestSubjectFormat:
    def test_names_the_open_slot_count_and_the_week(self):
        assert (
            EmailService().get_reminder_subject(MONDAY, FRIDAY, 3)
            == "3 open slots this week (19/10-23/10)"
        )

    def test_a_single_slot_is_singular(self):
        assert (
            EmailService().get_reminder_subject(MONDAY, FRIDAY, 1)
            == "1 open slot this week (19/10-23/10)"
        )

    def test_dates_are_day_first(self):
        # The organization reads dd/mm; 19/10 must never render as 10/19.
        assert "(19/10-23/10)" in EmailService().get_reminder_subject(MONDAY, FRIDAY, 2)


class TestWeekKey:
    def test_is_the_same_whatever_the_slot_count(self):
        service = EmailService()
        keys = {
            reminder_week_key_of(service.get_reminder_subject(MONDAY, FRIDAY, n))
            for n in (1, 2, 7)
        }
        assert keys == {reminder_week_key(MONDAY, FRIDAY)}

    def test_differs_between_weeks(self):
        assert reminder_week_key(MONDAY, FRIDAY) != reminder_week_key(
            MONDAY - timedelta(days=7), FRIDAY - timedelta(days=7)
        )

    def test_a_subject_from_the_old_format_keys_on_itself(self):
        # Rows written before this change carry "... (27/07 to 02/08)". They
        # must keep grouping together rather than collapsing into one bucket
        # with every other historic row.
        old = "🗓️ Weekly Volunteer Reminder – Schedule Update (27/07 to 02/08)"
        other = "🗓️ Weekly Volunteer Reminder – Schedule Update (03/08 to 09/08)"
        assert reminder_week_key_of(old) != reminder_week_key_of(other)


class TestOpenSlotCount:
    """What build_class_table reports as an open slot."""

    def test_counts_each_missing_teacher(self):
        block = _block(
            days=["Tuesday", "Thursday"],
            teacher=["", ""],
            assistants=["Someone", "Someone"],
        )
        assert EmailService().build_class_table(block)["open_slot_count"] == 2

    def test_counts_a_missing_head_ta(self):
        block = _block(
            has_head_ta=True,
            days=["Tuesday"],
            teacher=["A Teacher"],
            head_ta=[""],
            assistants=["Someone"],
        )
        assert EmailService().build_class_table(block)["open_slot_count"] == 1

    def test_counts_an_explicit_call_for_assistants(self):
        block = _block(
            days=["Tuesday"],
            teacher=["A Teacher"],
            assistants=["Need Volunteers"],
        )
        assert EmailService().build_class_table(block)["open_slot_count"] == 1

    def test_a_covered_week_has_no_open_slots(self):
        block = _block(
            max_assistants=1,
            days=["Tuesday", "Thursday"],
            teacher=["A Teacher", "B Teacher"],
            assistants=["Someone", "Someone"],
        )
        result = EmailService().build_class_table(block)
        assert result["open_slot_count"] == 0
        assert result["needs_volunteers"] is False

    def test_a_blank_non_teaching_day_is_not_an_open_slot(self):
        # The sheet keeps a column for every weekday and leaves the days with
        # no class blank, so a blank Monday is not an unfilled slot.
        block = _block(
            days=["Thứ Hai 19/10", "Thứ Ba 20/10"],
            teacher=["", ""],
            assistants=["Someone", "Someone"],
        )
        result = EmailService().build_class_table(
            block, teaching_days=frozenset({"tue"}), week_monday=MONDAY
        )
        assert result["open_slot_count"] == 1

    def test_a_parse_failure_claims_one_slot_rather_than_none(self):
        # needs_volunteers already defaults True on a parse failure so a bug
        # never silently cancels the reminder; the count has to agree with it,
        # or the subject would announce "0 open slots" on an email sent
        # precisely because slots might be open.
        result = EmailService().build_class_table(object())
        assert result["has_data"] is False
        assert result["needs_volunteers"] is True
        assert result["open_slot_count"] == 1


class TestRunKeySurvivesAChangingCount:
    """The resume and health paths must key on the week, not the whole subject."""

    def test_run_health_groups_rows_whose_count_moved(self, test_db):
        from app.models import Volunteer as VolunteerModel

        service = EmailService()
        for index, (count, status) in enumerate(((5, "sent"), (3, "sent"))):
            volunteer = VolunteerModel(
                name=f"Volunteer {index}", email=f"v{index}@example.com", is_active=True
            )
            test_db.add(volunteer)
            test_db.commit()
            test_db.add(
                EmailCommunicationModel(
                    volunteer_id=volunteer.id,
                    recipient_email=f"v{index}@example.com",
                    email_type="weekly_reminder",
                    subject=service.get_reminder_subject(MONDAY, FRIDAY, count),
                    template_name="weekly-reminder-email.html",
                    status=status,
                    sent_at=datetime.now(),
                    created_at=datetime.now(),
                )
            )
        test_db.commit()

        health = get_weekly_reminder_run_health(test_db)

        # One run of two recipients, not two runs of one.
        assert health["weeks_seen"] == 1
        assert health["last_run"]["recipients"] == 2
        assert health["last_run"]["sent"] == 2
        assert health["status"] == "ok"


# The matching resume test - that a run whose count moved still skips the
# volunteers it already reached - lives in test_weekly_reminder_delivery.py,
# alongside the SMTP and sending-week fixtures the send path needs.
