"""
Tests for schedule sheet rotation.

These pin down the failure mode observed in production (2026-07-03): a
protected sheet threw HttpError during PASS 1 hiding, aborting the whole
rotation so the next week's sheet was never created. Rotation must now
tolerate per-sheet failures, exclude non-date "Schedule *" tabs, name new
sheets DD/MM/YYYY, and match existing sheets by date across both formats.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

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


def next_monday():
    now = datetime.now()
    return now - timedelta(days=now.weekday()) + timedelta(days=7)


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

        result = rotate(service, existing, weeks=2)

        # Both display weeks still get created despite the hide failure
        assert service.create_sheet_from_template.call_count == 2
        failed_titles = [f["title"] for f in result["sheets_failed"]]
        assert failed_titles == ["Schedule 07/07"]

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
        assert created_dates[0].date() == next_monday().date()
        expected_titles = [format_schedule_sheet_title(d) for d in created_dates]
        assert all(
            t.startswith("Schedule ") and t.count("/") == 2 for t in expected_titles
        )

    def test_legacy_titled_sheet_matched_by_date_and_renamed(self, service):
        """A hidden legacy 'Schedule MM/DD' sheet for a display week is reused, not duplicated."""
        monday = next_monday()
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
        monday = next_monday()
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
