"""
Tests for schedule sheet rotation.

These pin down two production failure modes:

- 2026-07-03: a protected sheet threw HttpError during PASS 1 hiding,
  aborting the whole rotation so the next week's sheet was never created.
  Rotation must tolerate per-sheet failures, exclude non-date "Schedule *"
  tabs, name new sheets DD/MM/YYYY, and match existing sheets by date across
  both formats.
- 2026-07-20: the display window was anchored to `current_monday + 7 days`
  unconditionally, so triggering rotation on any day other than the exact
  moment the current week ended (e.g. the Monday a new week begins) skipped
  that week's sheet entirely - it was never shown, never hidden in the right
  order, and legacy-titled sheets that rotated out of view before ever being
  shown in the new format never got backfilled. Rotation must always anchor
  to the Monday of the week containing "now", keep display sheets in
  chronological order, show exactly `display_weeks_count` of them, and
  backfill every legacy title it touches regardless of visibility.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.google_sheets import GoogleSheetsService
from app.utils.schedule_dates import format_schedule_sheet_title


def sheet_props(title, sheet_id, index=0, hidden=False):
    return {
        "properties": {
            "title": title,
            "sheetId": sheet_id,
            "index": index,
            "hidden": hidden,
        }
    }


def current_monday():
    now = datetime.now()
    return now - timedelta(days=now.weekday())


@pytest.fixture
def service():
    svc = GoogleSheetsService.__new__(GoogleSheetsService)
    svc._service = MagicMock()
    svc._sheet = MagicMock()
    svc._initialized = True
    svc.set_sheet_visibility = MagicMock()
    svc.move_sheet = MagicMock()
    svc.rename_sheet = MagicMock()
    svc.create_sheet_from_template = MagicMock(return_value=999)
    svc.update_sheet_dates = MagicMock()
    return svc


def rotate(service, existing_sheets, weeks=2):
    service.get_schedule_sheets = MagicMock(return_value=existing_sheets)
    return service.rotate_schedule_sheets(MagicMock(), display_weeks_override=weeks)


class TestRotationResilience:
    def test_protected_sheet_failure_does_not_abort_rotation(self, service):
        """A sheet that cannot be hidden must not prevent new sheets being created."""
        protected = sheet_props("Schedule 07/07", sheet_id=77, hidden=False)
        existing = [protected]

        def fail_on_protected(sheet_id, hidden, db):
            if sheet_id == 77:
                raise Exception("HttpError 400: protected cell or object")

        service.set_sheet_visibility.side_effect = fail_on_protected

        now = datetime(2026, 7, 20)
        with (
            patch("app.services.google_sheets.datetime") as mock_dt,
            patch("app.utils.schedule_dates.datetime") as mock_dates_dt,
        ):
            mock_dt.now.return_value = now
            mock_dates_dt.now.return_value = now
            mock_dates_dt.strptime = datetime.strptime
            result = rotate(service, existing, weeks=2)

        # Both display weeks still get created despite the hide failure
        assert service.create_sheet_from_template.call_count == 2
        # Backfilled to canonical DD/MM/YYYY before the hide attempt failed
        failed_titles = [f["title"] for f in result["sheets_failed"]]
        assert failed_titles == ["Schedule 07/07/2026"]

    def test_schedule_config_and_template_never_touched(self, service):
        config = sheet_props("Schedule Config", sheet_id=48, hidden=False)
        template = sheet_props("Schedule Template", sheet_id=1, hidden=True)
        result = rotate(service, [config, template], weeks=1)

        touched_ids = [c.args[0] for c in service.set_sheet_visibility.call_args_list]
        assert 48 not in touched_ids
        assert result["sheets_failed"] == []


class TestRotationNaming:
    def test_new_sheets_created_with_ddmmyyyy_titles(self, service):
        rotate(service, [], weeks=2)

        created_dates = [
            c.args[1] for c in service.create_sheet_from_template.call_args_list
        ]
        assert created_dates[0].date() == current_monday().date()
        expected_titles = [format_schedule_sheet_title(d) for d in created_dates]
        assert all(
            t.startswith("Schedule ") and t.count("/") == 2 for t in expected_titles
        )

    def test_legacy_titled_sheet_matched_by_date_and_renamed(self, service):
        """A hidden legacy 'Schedule MM/DD' sheet for a display week is reused, not duplicated."""
        monday = current_monday()
        legacy_title = f"Schedule {monday.strftime('%m/%d')}"
        legacy = sheet_props(legacy_title, sheet_id=55, index=4, hidden=True)

        result = rotate(service, [legacy], weeks=1)

        service.create_sheet_from_template.assert_not_called()
        service.rename_sheet.assert_called_once()
        assert service.rename_sheet.call_args.args[0] == 55
        assert service.rename_sheet.call_args.args[1] == format_schedule_sheet_title(
            monday
        )
        # Made visible
        assert (55, False) in [
            (c.args[0], c.args[1]) for c in service.set_sheet_visibility.call_args_list
        ]
        assert format_schedule_sheet_title(monday) in result["sheets_renamed"]

    def test_canonical_titled_sheet_not_renamed(self, service):
        monday = current_monday()
        canonical = sheet_props(
            format_schedule_sheet_title(monday), sheet_id=60, hidden=True
        )

        rotate(service, [canonical], weeks=1)

        service.rename_sheet.assert_not_called()
        service.create_sheet_from_template.assert_not_called()


class TestCurrentScheduleDates:
    def test_parses_new_format_visible_sheet(self, service):
        title = format_schedule_sheet_title(datetime(2026, 7, 13))
        service.get_schedule_sheets = MagicMock(
            return_value=[sheet_props(title, sheet_id=2, hidden=False)]
        )
        monday, friday = service.get_current_schedule_dates(MagicMock())
        assert monday.date() == datetime(2026, 7, 13).date()
        assert friday.date() == datetime(2026, 7, 17).date()

    def test_parses_legacy_format_visible_sheet(self, service):
        service.get_schedule_sheets = MagicMock(
            return_value=[sheet_props("Schedule 07/13", sheet_id=2, hidden=False)]
        )
        monday, friday = service.get_current_schedule_dates(MagicMock())
        assert (monday.month, monday.day) == (7, 13)


class TestRotationAnchorDate:
    """Regression tests for the "current week gets skipped" bug.

    Previously the display window always started at
    `current_monday + timedelta(days=7)`, unconditionally skipping the week
    containing "now". That's correct only if rotation always runs the moment
    the current week ends; it's wrong for an ad-hoc/manual trigger on any
    other day (e.g. the Monday the new week begins), which dropped that
    week's sheet entirely instead of displaying it.
    """

    def test_display_starts_on_current_week_when_run_on_monday(self, service):
        monday = datetime(2026, 7, 20)
        assert monday.weekday() == 0  # sanity check: this really is a Monday

        with patch("app.services.google_sheets.datetime") as mock_dt:
            mock_dt.now.return_value = monday
            result = rotate(service, [], weeks=2)

        assert result["display_dates"][0] == monday.strftime("%d/%m/%Y")
        assert result["display_dates"][1] == (monday + timedelta(days=7)).strftime(
            "%d/%m/%Y"
        )

    def test_display_starts_on_current_weeks_monday_when_run_midweek(self, service):
        wednesday = datetime(2026, 7, 22)
        assert wednesday.weekday() == 2
        expected_monday = datetime(2026, 7, 20)

        with patch("app.services.google_sheets.datetime") as mock_dt:
            mock_dt.now.return_value = wednesday
            result = rotate(service, [], weeks=1)

        assert result["display_dates"][0] == expected_monday.strftime("%d/%m/%Y")


class TestRotationBackfill:
    """Regression tests: legacy MM/DD titles must be backfilled to DD/MM/YYYY
    even for sheets that fall outside the display window and only get hidden.

    Previously only sheets still inside the display window were renamed
    (in the "show" pass), so a legacy-titled sheet that rotated out of view
    kept its old-format title forever.
    """

    def test_legacy_sheet_outside_display_range_is_backfilled_when_hidden(
        self, service
    ):
        monday = datetime(2026, 7, 20)
        past_date = datetime(2026, 7, 6)  # two weeks before the display window
        legacy_title = f"Schedule {past_date.strftime('%m/%d')}"
        legacy = sheet_props(legacy_title, sheet_id=88, hidden=False)

        with (
            patch("app.services.google_sheets.datetime") as mock_dt,
            patch("app.utils.schedule_dates.datetime") as mock_dates_dt,
        ):
            mock_dt.now.return_value = monday
            mock_dates_dt.now.return_value = monday
            mock_dates_dt.strptime = datetime.strptime
            result = rotate(service, [legacy], weeks=1)

        service.rename_sheet.assert_called_once()
        assert service.rename_sheet.call_args.args[0] == 88
        assert service.rename_sheet.call_args.args[1] == format_schedule_sheet_title(
            past_date
        )
        assert format_schedule_sheet_title(past_date) in result["sheets_renamed"]


class TestRotationOrderingAndCount:
    """Direct checks for the two symptoms reported alongside the anchor bug:
    sheets not staying in chronological order, and more sheets than
    `display_weeks_count` remaining visible.
    """

    def test_sheets_moved_into_chronological_order(self, service):
        monday = current_monday()
        first = sheet_props(
            format_schedule_sheet_title(monday), sheet_id=10, hidden=True
        )
        second = sheet_props(
            format_schedule_sheet_title(monday + timedelta(days=7)),
            sheet_id=11,
            hidden=True,
        )
        third = sheet_props(
            format_schedule_sheet_title(monday + timedelta(days=14)),
            sheet_id=12,
            hidden=True,
        )

        # Deliberately scrambled input order - the target order must come
        # from the display dates, not from however the sheets happened to
        # come back from the API.
        result = rotate(service, [third, first, second], weeks=3)

        move_calls = {c.args[0]: c.args[1] for c in service.move_sheet.call_args_list}
        assert move_calls[10] == 1
        assert move_calls[11] == 2
        assert move_calls[12] == 3
        assert result["display_dates"] == [
            monday.strftime("%d/%m/%Y"),
            (monday + timedelta(days=7)).strftime("%d/%m/%Y"),
            (monday + timedelta(days=14)).strftime("%d/%m/%Y"),
        ]

    def test_only_configured_count_of_sheets_stays_visible(self, service):
        monday = current_monday()
        # 4 weeks already exist and are visible; only the first 2 should
        # remain visible once display_weeks_count is 2.
        sheets = [
            sheet_props(
                format_schedule_sheet_title(monday + timedelta(days=7 * i)),
                sheet_id=20 + i,
                hidden=False,
            )
            for i in range(4)
        ]

        result = rotate(service, sheets, weeks=2)

        assert result["display_dates"] == [
            monday.strftime("%d/%m/%Y"),
            (monday + timedelta(days=7)).strftime("%d/%m/%Y"),
        ]
        hidden_ids = [
            c.args[0]
            for c in service.set_sheet_visibility.call_args_list
            if c.args[1] is True
        ]
        assert 22 in hidden_ids
        assert 23 in hidden_ids
        assert 20 not in hidden_ids
        assert 21 not in hidden_ids
