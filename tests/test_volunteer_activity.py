"""Tests for volunteer activity attribution and weekly-reminder recipient selection.

The schedule sheet is the only record of who actually turns up, and it holds
free-text names a volunteer typed, never an email. Every test here exists
because one of those real cell values would otherwise be dropped or
misattributed - see the fixture names in tests/fixtures/schedule_sheets.py.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models import Volunteer as VolunteerModel
from app.services.schedule_parser import discover_schedule_blocks
from app.services.volunteer_activity import (
    extract_signup_names,
    match_signup_name,
    normalize_name,
    record_week_activity,
    select_weekly_reminder_recipients,
    signup_name_candidates,
)
from tests.fixtures.schedule_sheets import CURRENT_WEEK_GRID


def make_volunteer(db, name: str, **kwargs) -> VolunteerModel:
    """Persist a volunteer with the subscription defaults the model declares."""
    volunteer = VolunteerModel(
        name=name,
        email=kwargs.pop("email", f"{name.replace(' ', '.').lower()}@example.com"),
        is_active=kwargs.pop("is_active", True),
        weekly_reminders_subscribed=kwargs.pop("weekly_reminders_subscribed", True),
        all_emails_subscribed=kwargs.pop("all_emails_subscribed", True),
        **kwargs,
    )
    db.add(volunteer)
    db.commit()
    return volunteer


class TestNormalizeName:
    def test_strips_diacritics_case_and_padding(self):
        assert normalize_name(" Mỹ Hạnh Trần ") == "my hanh tran"

    def test_collapses_internal_whitespace_and_drops_punctuation(self):
        assert normalize_name("Anthony  T.") == "anthony t"

    def test_empty_for_a_blank_cell(self):
        assert normalize_name("   ") == ""


class TestSignupNameCandidates:
    def test_splits_a_multi_name_cell(self):
        assert signup_name_candidates("Tran Bui, Joe Tran") == ["Tran Bui", "Joe Tran"]

    def test_splits_on_semicolons_and_slashes_too(self):
        assert signup_name_candidates("Annie / Thomas; Nico") == [
            "Annie",
            "Thomas",
            "Nico",
        ]

    def test_keeps_both_a_nickname_and_its_parenthetical_name(self):
        # "Jasmine (Quỳnh Như)" must reach the volunteer record whichever of
        # the two names it was created under.
        assert signup_name_candidates("Jasmine (Quỳnh Như) ") == [
            "Jasmine",
            "Quỳnh Như",
        ]

    @pytest.mark.parametrize(
        "cell",
        [
            "",
            "   ",
            "Need Volunteers",
            "need volunteers",
            "Optional",
            "No class",
            "No class: holiday",
            "TBD",
            "N/A",
            "-",
        ],
    )
    def test_placeholders_are_not_people(self, cell):
        assert signup_name_candidates(cell) == []


class TestExtractSignupNames:
    def test_reads_every_filled_role_cell_of_the_live_grid(self):
        blocks = discover_schedule_blocks([row[1:7] for row in CURRENT_WEEK_GRID])
        names = extract_signup_names(blocks)

        # Teachers, head TAs and assistants from all three blocks.
        assert "Truc Quynh Dang" in names
        assert "Mỹ Hạnh Trần" in names
        assert "Nico" in names
        assert "Joe Tran" in names
        assert "Annie" in names and "Thu Hằng" in names
        assert "Anthony T" in names

    def test_ignores_placeholder_cells(self):
        blocks = discover_schedule_blocks([row[1:7] for row in CURRENT_WEEK_GRID])
        names = extract_signup_names(blocks)

        assert not any(normalize_name(n).startswith("need volunteer") for n in names)

    def test_ignores_a_name_typed_outside_a_class_block(self):
        # "Toan Tran" sits in the separator row between two blocks, so it is
        # not a signup for any class slot.
        blocks = discover_schedule_blocks([row[1:7] for row in CURRENT_WEEK_GRID])
        assert "Toan Tran" not in extract_signup_names(blocks)

    def test_ignores_the_curriculum_row(self):
        blocks = discover_schedule_blocks([row[1:7] for row in CURRENT_WEEK_GRID])
        assert not any(
            "curriculum" in normalize_name(n) for n in extract_signup_names(blocks)
        )


class TestMatchSignupName:
    def test_exact_name(self, test_db):
        volunteer = make_volunteer(test_db, "Joe Tran")
        assert match_signup_name(test_db, "Joe Tran") == [volunteer.id]

    def test_matches_through_missing_diacritics(self, test_db):
        volunteer = make_volunteer(test_db, "Mỹ Hạnh Trần")
        assert match_signup_name(test_db, "My Hanh Tran") == [volunteer.id]

    def test_matches_a_swapped_vietnamese_name_order(self, test_db):
        volunteer = make_volunteer(test_db, "Trần Mỹ Hạnh")
        assert match_signup_name(test_db, "Mỹ Hạnh Trần") == [volunteer.id]

    def test_matches_a_shortened_name_anchored_on_the_first_word(self, test_db):
        volunteer = make_volunteer(test_db, "Nico Nguyen")
        assert match_signup_name(test_db, "Nico") == [volunteer.id]

    def test_does_not_match_on_a_shared_family_name(self, test_db):
        make_volunteer(test_db, "Mỹ Hạnh Trần")
        assert match_signup_name(test_db, "Tran") == []

    def test_an_ambiguous_first_name_keeps_every_candidate(self, test_db):
        # Guessing one of two volunteers named John would unsubscribe the
        # other, so both stay in. Every error here favours keeping a
        # volunteer on the list.
        first = make_volunteer(test_db, "John Le", email="john.le@example.com")
        second = make_volunteer(test_db, "John Pham", email="john.pham@example.com")
        assert match_signup_name(test_db, "John") == sorted([first.id, second.id])

    def test_unknown_name_matches_nothing(self, test_db):
        make_volunteer(test_db, "Joe Tran")
        assert match_signup_name(test_db, "Someone Else") == []

    def test_an_inactive_volunteer_can_still_be_matched(self, test_db):
        # A volunteer the sunset policy already deactivated who turns up on the
        # sheet again must be recorded as active, not skipped.
        volunteer = make_volunteer(test_db, "Joe Tran", is_active=False)
        assert match_signup_name(test_db, "Joe Tran") == [volunteer.id]


class TestRecordWeekActivity:
    def test_stamps_matched_volunteers_with_the_week(self, test_db):
        volunteer = make_volunteer(test_db, "Joe Tran")
        monday = datetime(2026, 10, 12)

        result = record_week_activity(test_db, ["Joe Tran"], monday)

        test_db.refresh(volunteer)
        assert volunteer.last_active_at == monday
        assert result.matched == 1
        assert result.unmatched == []

    def test_keeps_the_most_recent_week(self, test_db):
        volunteer = make_volunteer(test_db, "Joe Tran")
        record_week_activity(test_db, ["Joe Tran"], datetime(2026, 10, 12))
        record_week_activity(test_db, ["Joe Tran"], datetime(2026, 9, 7))

        test_db.refresh(volunteer)
        assert volunteer.last_active_at == datetime(2026, 10, 12)

    def test_advances_to_a_later_week(self, test_db):
        volunteer = make_volunteer(test_db, "Joe Tran")
        record_week_activity(test_db, ["Joe Tran"], datetime(2026, 9, 7))
        record_week_activity(test_db, ["Joe Tran"], datetime(2026, 10, 12))

        test_db.refresh(volunteer)
        assert volunteer.last_active_at == datetime(2026, 10, 12)

    def test_reports_names_it_could_not_place(self, test_db):
        make_volunteer(test_db, "Joe Tran")

        result = record_week_activity(
            test_db, ["Joe Tran", "Yer", "Laure"], datetime(2026, 10, 12)
        )

        assert result.matched == 1
        assert result.unmatched == ["Laure", "Yer"]

    def test_dry_run_writes_nothing(self, test_db):
        volunteer = make_volunteer(test_db, "Joe Tran")

        result = record_week_activity(
            test_db, ["Joe Tran"], datetime(2026, 10, 12), dry_run=True
        )

        test_db.refresh(volunteer)
        assert volunteer.last_active_at is None
        assert result.matched == 1


class TestSelectWeeklyReminderRecipients:
    now = datetime(2026, 10, 21, tzinfo=UTC).replace(tzinfo=None)

    def test_includes_a_volunteer_active_inside_the_window(self, test_db):
        volunteer = make_volunteer(test_db, "Recent One")
        volunteer.last_active_at = self.now - timedelta(weeks=2)
        test_db.commit()

        recipients = select_weekly_reminder_recipients(test_db, now=self.now)
        assert [v.id for v in recipients] == [volunteer.id]

    def test_excludes_a_volunteer_last_active_before_the_window(self, test_db):
        volunteer = make_volunteer(test_db, "Lapsed One")
        volunteer.last_active_at = self.now - timedelta(weeks=12)
        volunteer.created_at = self.now - timedelta(weeks=20)
        test_db.commit()

        assert select_weekly_reminder_recipients(test_db, now=self.now) == []

    def test_falls_back_to_the_signup_date_when_never_seen_on_the_sheet(self, test_db):
        # A volunteer accepted last week has had no chance to appear on the
        # schedule yet; dropping them would be the opposite of the intent.
        volunteer = make_volunteer(test_db, "Brand New")
        volunteer.created_at = self.now - timedelta(weeks=1)
        test_db.commit()

        recipients = select_weekly_reminder_recipients(test_db, now=self.now)
        assert [v.id for v in recipients] == [volunteer.id]

    def test_excludes_an_old_signup_never_seen_on_the_sheet(self, test_db):
        volunteer = make_volunteer(test_db, "Signed Up And Vanished")
        volunteer.created_at = self.now - timedelta(weeks=30)
        test_db.commit()

        assert select_weekly_reminder_recipients(test_db, now=self.now) == []

    def test_still_honours_the_unsubscribe_flags(self, test_db):
        unsubscribed = make_volunteer(
            test_db, "Opted Out", weekly_reminders_subscribed=False
        )
        unsubscribed.last_active_at = self.now
        inactive = make_volunteer(test_db, "Deactivated", is_active=False)
        inactive.last_active_at = self.now
        test_db.commit()

        assert select_weekly_reminder_recipients(test_db, now=self.now) == []

    def test_window_width_comes_from_the_setting(self, test_db):
        from app.services.settings_service import set_setting

        volunteer = make_volunteer(test_db, "Four Weeks Back")
        volunteer.last_active_at = self.now - timedelta(weeks=4)
        test_db.commit()

        set_setting(test_db, "REMINDER_ACTIVE_WEEKS", "2")
        assert select_weekly_reminder_recipients(test_db, now=self.now) == []

        set_setting(test_db, "REMINDER_ACTIVE_WEEKS", "8")
        assert len(select_weekly_reminder_recipients(test_db, now=self.now)) == 1

    def test_zero_weeks_turns_the_recency_filter_off(self, test_db):
        from app.services.settings_service import set_setting

        volunteer = make_volunteer(test_db, "Long Gone")
        volunteer.last_active_at = self.now - timedelta(weeks=52)
        test_db.commit()

        set_setting(test_db, "REMINDER_ACTIVE_WEEKS", "0")
        recipients = select_weekly_reminder_recipients(test_db, now=self.now)
        assert [v.id for v in recipients] == [volunteer.id]


def tab(title, hidden=False):
    return {
        "properties": {
            "title": title,
            "sheetId": abs(hash(title)) % 10**9,
            "hidden": hidden,
        }
    }


class TestRecordActivityFromScheduleSheets:
    """Which tabs the scan reads, and how it dates what it finds."""

    def scan(self, db, tabs, **kwargs):
        from unittest.mock import MagicMock, patch

        from app.services import volunteer_activity

        fake = MagicMock()
        fake.get_schedule_sheets.return_value = tabs
        fake.get_schedule_range.return_value = [row[1:7] for row in CURRENT_WEEK_GRID]
        with patch.object(volunteer_activity, "_sheets", return_value=fake):
            return fake, volunteer_activity.record_activity_from_schedule_sheets(
                db, **kwargs
            )

    def test_credits_the_week_each_tab_is_named_for(self, test_db):
        make_volunteer(test_db, "Joe Tran")
        _, results = self.scan(test_db, [tab("Schedule 12/10/2026")])

        assert [title for title, _ in results] == ["Schedule 12/10/2026"]
        volunteer = test_db.query(VolunteerModel).one()
        assert volunteer.last_active_at == datetime(2026, 10, 12)

    def test_skips_the_undated_template_tab(self, test_db):
        # "Schedule Template" carries the "Schedule " prefix every other tab
        # does and is visible whenever a volunteer is editing it, but its
        # placeholder rows are nobody's signup.
        fake, results = self.scan(
            test_db, [tab("Schedule Template"), tab("Schedule 12/10/2026")]
        )

        assert [title for title, _ in results] == ["Schedule 12/10/2026"]
        assert fake.get_schedule_range.call_count == 1

    def test_skips_hidden_past_weeks_by_default(self, test_db):
        fake, results = self.scan(
            test_db,
            [tab("Schedule 12/10/2026"), tab("Schedule 05/10/2026", hidden=True)],
        )

        assert [title for title, _ in results] == ["Schedule 12/10/2026"]

    def test_include_hidden_reaches_the_archived_weeks(self, test_db):
        make_volunteer(test_db, "Joe Tran")
        _, results = self.scan(
            test_db,
            [tab("Schedule 12/10/2026"), tab("Schedule 05/10/2026", hidden=True)],
            include_hidden=True,
        )

        assert [title for title, _ in results] == [
            "Schedule 12/10/2026",
            "Schedule 05/10/2026",
        ]
        # The later week wins however the tabs are ordered.
        assert test_db.query(VolunteerModel).one().last_active_at == datetime(
            2026, 10, 12
        )

    def test_qualifies_the_range_with_the_tab_title(self, test_db):
        # An unqualified range resolves to the first sheet in the spreadsheet,
        # which rotation pins to the template.
        fake, _ = self.scan(test_db, [tab("Schedule 12/10/2026")])

        _, range_name = fake.get_schedule_range.call_args.args
        assert range_name == "'Schedule 12/10/2026'!B1:G100"

    def test_dry_run_reports_without_writing(self, test_db):
        make_volunteer(test_db, "Joe Tran")
        _, results = self.scan(test_db, [tab("Schedule 12/10/2026")], dry_run=True)

        assert results[0][1].matched == 1
        assert test_db.query(VolunteerModel).one().last_active_at is None


class TestWeeklyReminderSendHonoursTheWindow:
    """The recency window has to hold on the real send path, not just in the query.

    POST /admin/send-weekly-reminders builds and sends per recipient, so this
    exercises the endpoint a Cloud Scheduler job actually calls.
    """

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

    def send(self, admin_client):
        from datetime import datetime as real_datetime
        from unittest.mock import patch

        from app.services.email_service import email_service
        from app.services.google_sheets import sheets_service

        with (
            patch.object(sheets_service, "get_schedule_blocks", return_value=["block"]),
            patch.object(
                sheets_service,
                "get_current_schedule_dates",
                return_value=(real_datetime.now(), real_datetime.now()),
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
            patch.object(email_service, "send_custom_email") as send_custom,
        ):
            response = admin_client.post("/admin/send-weekly-reminders")
        return response, [c.kwargs["to_email"] for c in send_custom.call_args_list]

    def test_a_lapsed_volunteer_gets_no_reminder(self, admin_client, test_db):
        lapsed = make_volunteer(test_db, "Lapsed One", email="lapsed@example.com")
        lapsed.created_at = datetime.now() - timedelta(weeks=40)
        lapsed.last_active_at = datetime.now() - timedelta(weeks=30)
        active = make_volunteer(test_db, "Active One", email="active@example.com")
        active.last_active_at = datetime.now() - timedelta(days=3)
        test_db.commit()

        response, sent_to = self.send(admin_client)

        assert response.json()["status"] == "success"
        assert sent_to == ["active@example.com"]

    def test_no_email_communication_row_is_created_for_a_lapsed_volunteer(
        self, admin_client, test_db
    ):
        # The PENDING rows are written before the SMTP loop, so a volunteer
        # excluded from the send must not leave a reminder row behind either -
        # otherwise the reminder-stats page reports sends that never happened.
        from app.models import EmailCommunication as EmailCommunicationModel

        lapsed = make_volunteer(test_db, "Lapsed Two", email="lapsed2@example.com")
        lapsed.created_at = datetime.now() - timedelta(weeks=40)
        lapsed.last_active_at = datetime.now() - timedelta(weeks=30)
        test_db.commit()

        self.send(admin_client)

        assert (
            test_db.query(EmailCommunicationModel)
            .filter(EmailCommunicationModel.volunteer_id == lapsed.id)
            .count()
            == 0
        )
