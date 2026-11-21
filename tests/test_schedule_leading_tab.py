"""
The reminder must read the week it leads with, not the Schedule Template.

A range with no tab name resolves to the *first sheet in the spreadsheet*, and
rotation deliberately keeps "Schedule Template" there (it inserts the display
weeks from index 1). So `get_schedule_blocks` read the template on every run:
the live spreadsheet answered a bare "B1:G100" with "'Schedule
Template'!B1:G29". Every weekly reminder therefore described the template's
"Need Volunteers" placeholders and its placeholder June dates instead of the
week volunteers had signed up on - and with Grade 1A added to the template,
a fourth, entirely unfilled class.

Both helpers here also have to look past the template when picking the leading
tab: it matches the "Schedule " prefix every other tab does, and it is visible
whenever somebody has been editing it, as the captain had on 2026-10-15.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services.google_sheets import (
    SCHEDULE_CLASS_AREA_RANGE,
    GoogleSheetsService,
)
from app.utils.sheet_utils import a1_range
from tests.fixtures.schedule_sheets import CURRENT_WEEK_GRID, TEMPLATE_GRID

TEMPLATE = {
    "properties": {
        "title": "Schedule Template",
        "sheetId": 1911285501,
        "index": 0,
        "hidden": False,
    }
}


def tab(title, sheet_id, index, hidden=False):
    return {
        "properties": {
            "title": title,
            "sheetId": sheet_id,
            "index": index,
            "hidden": hidden,
        }
    }


CURRENT = tab("Schedule 12/10/2026", 1775524467, 1)
NEXT_WEEK = tab("Schedule 19/10/2026", 43864870, 2)
LAST_WEEK = tab("Schedule 05/10/2026", 419457119, 4, hidden=True)


@pytest.fixture
def service():
    svc = GoogleSheetsService.__new__(GoogleSheetsService)
    svc._service = MagicMock()
    svc._sheet = MagicMock()
    svc._initialized = True
    return svc


def with_tabs(service, tabs, grid=None):
    service.get_schedule_sheets = MagicMock(return_value=tabs)
    # B1:G100, as production asks for: the label column plus C:G.
    service.get_range_from_sheet = MagicMock(
        return_value=[row[1:7] for row in (grid if grid is not None else [])]
    )
    return service


class TestLeadingScheduleSheet:
    def test_the_visible_template_is_not_the_leading_week(self, service):
        with_tabs(service, [TEMPLATE, CURRENT, NEXT_WEEK])

        leading = service.get_leading_schedule_sheet(MagicMock())

        assert leading["properties"]["title"] == "Schedule 12/10/2026"

    def test_hidden_weeks_are_skipped(self, service):
        with_tabs(service, [TEMPLATE, LAST_WEEK, CURRENT])

        leading = service.get_leading_schedule_sheet(MagicMock())

        assert leading["properties"]["title"] == "Schedule 12/10/2026"

    def test_a_spreadsheet_with_no_visible_week_has_no_leading_tab(self, service):
        with_tabs(service, [TEMPLATE, LAST_WEEK])

        assert service.get_leading_schedule_sheet(MagicMock()) is None


class TestScheduleBlocksReadTheLeadingWeek:
    def test_the_class_grid_is_read_from_the_leading_weeks_tab(self, service):
        with_tabs(service, [TEMPLATE, CURRENT, NEXT_WEEK], grid=CURRENT_WEEK_GRID)

        with patch(
            "app.services.google_sheets.ConfigHelper.get_schedule_sheet_id",
            return_value="sid",
        ):
            blocks = service.get_schedule_blocks(MagicMock())

        _, _, requested_range = service.get_range_from_sheet.call_args.args
        assert requested_range == "'Schedule 12/10/2026'!B1:G100"
        assert [block.name for block in blocks] == ["Grade 1B", "Grade 2", "Grade 5"]
        # The real signups, not the template's placeholders.
        assert blocks[0].teacher[1] == "Truc Quynh Dang"

    def test_the_template_is_never_the_source_of_the_reminder(self, service):
        """Guards the regression directly: served the template's grid, the
        parse would yield its four unfilled blocks."""
        with_tabs(service, [TEMPLATE, CURRENT], grid=TEMPLATE_GRID)

        with patch(
            "app.services.google_sheets.ConfigHelper.get_schedule_sheet_id",
            return_value="sid",
        ):
            service.get_schedule_blocks(MagicMock())

        _, _, requested_range = service.get_range_from_sheet.call_args.args
        assert "Schedule Template" not in requested_range

    def test_an_explicit_qualified_range_is_passed_through(self, service):
        with_tabs(service, [TEMPLATE, CURRENT], grid=CURRENT_WEEK_GRID)

        with patch(
            "app.services.google_sheets.ConfigHelper.get_schedule_sheet_id",
            return_value="sid",
        ):
            service.get_schedule_blocks(MagicMock(), "'Schedule 19/10/2026'!B1:G40")

        _, _, requested_range = service.get_range_from_sheet.call_args.args
        assert requested_range == "'Schedule 19/10/2026'!B1:G40"

    def test_no_visible_week_raises_rather_than_reporting_no_classes(self, service):
        """An empty parse reads downstream as "no open slots", which would
        cancel the week's reminder without saying why."""
        with_tabs(service, [TEMPLATE, LAST_WEEK])

        with pytest.raises(ValueError, match="No visible dated schedule sheet"):
            service.get_schedule_blocks(MagicMock())


class TestLeadingTabDates:
    def test_dates_come_from_the_leading_week_not_the_template(self, service):
        with_tabs(service, [TEMPLATE, CURRENT, NEXT_WEEK])

        monday, friday = service.get_current_schedule_dates(MagicMock())

        assert monday == datetime(2026, 10, 12)
        assert friday == datetime(2026, 10, 16)


class TestA1Quoting:
    def test_a_title_with_slashes_and_spaces_is_quoted(self):
        assert (
            a1_range("Schedule 12/10/2026", SCHEDULE_CLASS_AREA_RANGE)
            == "'Schedule 12/10/2026'!B1:G100"
        )

    def test_an_apostrophe_in_a_title_is_escaped(self):
        assert a1_range("Annie's week", "A1") == "'Annie''s week'!A1"
