"""
Signup dates must not depend on the spreadsheet's locale.

The responses sheet displays dates in whatever locale the spreadsheet is set to.
Parsing the displayed text with a fixed US pattern silently swaps day and month
once the sheet shows Vietnamese DD/MM/YYYY dates, so dates are read from the raw
serial values the Sheets API returns instead.
"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

from app.routers.admin.helpers import parse_start_date
from app.services.google_sheets import (
    SIGNUP_SHEET_HEADERS,
    GoogleSheetsService,
    sheets_serial_to_datetime,
)

TIMESTAMP_COL = SIGNUP_SHEET_HEADERS.index("timestamp")
START_DATE_COL = SIGNUP_SHEET_HEADERS.index("start_date")

# 05/10/2026 14:30:00 (5 October 2026) as a Sheets serial number.
OCT_5_2026_1430 = 46300 + (14.5 / 24)
# 03/11/2026 (3 November 2026) as a Sheets serial number.
NOV_3_2026 = 46329


def _row(timestamp, start_date):
    row = [""] * len(SIGNUP_SHEET_HEADERS)
    row[SIGNUP_SHEET_HEADERS.index("applicant_status")] = "ACCEPTED"
    row[SIGNUP_SHEET_HEADERS.index("email_address")] = "volunteer@example.com"
    row[SIGNUP_SHEET_HEADERS.index("first_name")] = "An"
    row[TIMESTAMP_COL] = timestamp
    row[START_DATE_COL] = start_date
    return row


def _service_returning(formatted_rows, raw_rows):
    """A service whose values().get() answers by valueRenderOption."""

    def fake_get(**kwargs):
        request = MagicMock()
        if kwargs.get("valueRenderOption") == "UNFORMATTED_VALUE":
            assert kwargs.get("dateTimeRenderOption") == "SERIAL_NUMBER"
            request.execute.return_value = {"values": raw_rows}
        else:
            request.execute.return_value = {"values": formatted_rows}
        return request

    sheet = MagicMock()
    sheet.values.return_value.get.side_effect = fake_get
    service = GoogleSheetsService()
    service._sheet = sheet
    service._initialized = True
    return service


def _fetch(service):
    with (
        patch(
            "app.services.google_sheets.ConfigHelper.get_new_signups_sheet_id",
            return_value="sheet-id",
        ),
        patch(
            "app.services.google_sheets.ConfigHelper.get_google_sheets_max_retries",
            return_value=1,
        ),
    ):
        return service.get_signup_form_submissions(db=MagicMock())


def test_sheets_serial_to_datetime_uses_the_sheets_epoch():
    assert sheets_serial_to_datetime(OCT_5_2026_1430) == datetime(2026, 10, 5, 14, 30)
    assert sheets_serial_to_datetime(NOV_3_2026) == datetime(2026, 11, 3)


def test_vietnamese_locale_timestamp_is_not_read_as_us_month_first():
    # What the API returns once the sheet's locale is vi_VN.
    service = _service_returning(
        formatted_rows=[_row("05/10/2026 14:30:00", "03/11/2026")],
        raw_rows=[_row(OCT_5_2026_1430, NOV_3_2026)],
    )

    [submission] = _fetch(service)

    assert submission["timestamp"] == datetime(2026, 10, 5, 14, 30)
    assert parse_start_date(submission["start_date"]) == date(2026, 11, 3)


def test_us_locale_sheet_still_reads_the_same_dates():
    service = _service_returning(
        formatted_rows=[_row("10/5/2026 14:30:00", "11/3/2026")],
        raw_rows=[_row(OCT_5_2026_1430, NOV_3_2026)],
    )

    [submission] = _fetch(service)

    assert submission["timestamp"] == datetime(2026, 10, 5, 14, 30)
    assert parse_start_date(submission["start_date"]) == date(2026, 11, 3)


def test_text_cells_keep_their_displayed_value():
    service = _service_returning(
        formatted_rows=[_row("05/10/2026 14:30:00", "ASAP")],
        raw_rows=[_row(OCT_5_2026_1430, "ASAP")],
    )

    [submission] = _fetch(service)

    assert submission["start_date"] == "ASAP"
    assert submission["first_name"] == "An"


def test_parse_start_date_reads_iso_and_vietnamese_day_first():
    assert parse_start_date("2026-11-03") == date(2026, 11, 3)
    assert parse_start_date("03/11/2026") == date(2026, 11, 3)
    assert parse_start_date("asap") == datetime.now().date()
    assert parse_start_date("not a date") is None
