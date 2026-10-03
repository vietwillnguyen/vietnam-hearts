"""
Schedule day headers must survive the spreadsheet's locale.

The sheet is vi_VN, so ``dddd`` headers read "Thứ Hai 5/10", and the
rotation used to overwrite the header cells with bare "dd/mm" text, which an
en_US sheet read as month-first (12/10 became 10 December) and which carries
no weekday for the parser to find, so the week's reminder had no classes.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.services.google_sheets import GoogleSheetsService
from app.services.schedule_parser import discover_schedule_blocks, row_is_class_header

BLOCK_BODY = [
    ["Teacher", "", "An", "", "Need Volunteers", ""],
    ["Assistants MAX 2", "", "Need Volunteers", "", "Need Volunteers", ""],
]


def test_vietnamese_weekday_header_is_a_class_header():
    row = [
        "Grade 2\n9:30",
        "Thứ Hai 6/1",
        "Thứ Ba 6/2",
        "Thứ Tư 6/3",
        "Thứ Năm 6/4",
        "Thứ Sáu 6/5",
    ]
    assert row_is_class_header(row)
    assert len(discover_schedule_blocks([row, *BLOCK_BODY])) == 1


def test_bare_day_month_header_is_a_class_header():
    row = ["Grade 1\n9:30", "19/10", "20/10", "21/10", "22/10", "23/10"]
    assert row_is_class_header(row)
    assert len(discover_schedule_blocks([row, *BLOCK_BODY])) == 1


def test_role_rows_are_not_class_headers():
    assert not row_is_class_header(["Teacher", "", "An", "", "Need Volunteers", ""])
    assert not row_is_class_header(["Assistants MAX 2", "", "Need Volunteers"])


def _service_with_grid(grid):
    sheet = MagicMock()
    sheet.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Schedule 19/10/2026", "sheetId": 42}}]
    }
    service = GoogleSheetsService()
    service._sheet = sheet
    service._initialized = True
    service.get_range_from_sheet = MagicMock(return_value=grid)
    return service, sheet


def test_day_headers_are_written_as_dates_with_a_vietnamese_day_month_format():
    grid = [
        ["", "Schedule for Week 06/01"],
        [],
        [
            "",
            "Grade 1\n9:30",
            "Thứ Hai 6/1",
            "Thứ Ba 6/2",
            "Thứ Tư 6/3",
            "Thứ Năm 6/4",
            "Thứ Sáu 6/5",
        ],
        ["", "Teacher", "", "An"],
    ]
    service, sheet = _service_with_grid(grid)

    with patch(
        "app.services.google_sheets.ConfigHelper.get_schedule_sheet_id",
        return_value="sid",
    ):
        service.update_sheet_dates(datetime(2026, 10, 19), db=MagicMock())

    [call] = sheet.batchUpdate.call_args_list
    [request] = call.kwargs["body"]["requests"]
    cells = request["updateCells"]
    assert cells["range"] == {
        "sheetId": 42,
        "startRowIndex": 2,
        "endRowIndex": 3,
        "startColumnIndex": 2,
        "endColumnIndex": 7,
    }
    values = cells["rows"][0]["values"]
    # 19 October 2026 is serial 46314; Monday to Friday follow.
    assert [v["userEnteredValue"]["numberValue"] for v in values] == [
        46314,
        46315,
        46316,
        46317,
        46318,
    ]
    # dddd renders the weekday in the sheet's vi_VN locale: "Thứ Hai 19/10".
    assert {v["userEnteredFormat"]["numberFormat"]["pattern"] for v in values} == {
        'dddd" "d"/"m'
    }
    # No locale-dependent text write of the day headers remains.
    written_ranges = [
        c.kwargs["range"] for c in sheet.values.return_value.update.call_args_list
    ]
    assert all("C1" in r or "B1" in r for r in written_ranges)


def test_vietnamese_weekday_names_resolve_to_the_same_teaching_days():
    from app.utils.schedule_dates import is_teaching_day, weekday_token

    assert weekday_token("Thứ Hai 5/10") == "mon"
    assert weekday_token("Thứ Ba 6/10") == "tue"
    assert weekday_token("Thứ Tư 7/10") == "wed"
    assert weekday_token("Thứ Năm 8/10") == "thu"
    assert weekday_token("Thứ Sáu 9/10") == "fri"
    assert weekday_token("Thứ Bảy") == "sat"
    assert weekday_token("Chủ Nhật") == "sun"
    assert weekday_token("Thứ 3") == "tue"
    # English labels keep working.
    assert weekday_token("Thursday 6/25") == "thu"
    assert is_teaching_day("Thứ Ba 6/10", "Tuesday, Thursday") is True
    assert is_teaching_day("Thứ Tư 7/10", "Tuesday, Thursday") is False
