"""Tests for schedule sheet title parsing/formatting utilities."""

from datetime import UTC, datetime
from unittest.mock import patch

from app.utils.schedule_dates import (
    DEFAULT_SCHEDULE_TIMEZONE,
    current_week_monday,
    format_schedule_sheet_title,
    parse_schedule_sheet_title,
)


class FrozenDatetime(datetime):
    """datetime whose now() reports a fixed instant, converting tz for real.

    Patching ``datetime.now`` with a plain MagicMock would ignore the tzinfo
    argument entirely, so the conversion under test would never actually run.
    This subclass keeps the real astimezone() maths and only freezes the clock.
    """

    frozen_utc = datetime(1970, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.frozen_utc.replace(tzinfo=None)
        return cls.frozen_utc.astimezone(tz)


def frozen_at(iso_utc: str):
    """Patch the schedule_dates clock to a fixed UTC instant."""
    frozen = type(
        "Frozen",
        (FrozenDatetime,),
        {"frozen_utc": datetime.fromisoformat(iso_utc).replace(tzinfo=UTC)},
    )
    return patch("app.utils.schedule_dates.datetime", frozen)


class TestFormatScheduleSheetTitle:
    def test_formats_as_ddmmyyyy(self):
        assert (
            format_schedule_sheet_title(datetime(2026, 7, 13)) == "Schedule 13/07/2026"
        )

    def test_zero_pads_day_and_month(self):
        assert (
            format_schedule_sheet_title(datetime(2026, 1, 5)) == "Schedule 05/01/2026"
        )


class TestParseScheduleSheetTitle:
    def test_parses_new_ddmmyyyy_format(self):
        assert parse_schedule_sheet_title("Schedule 13/07/2026") == datetime(
            2026, 7, 13
        )

    def test_round_trip(self):
        date = datetime(2026, 7, 20)
        assert parse_schedule_sheet_title(format_schedule_sheet_title(date)) == date

    def test_parses_legacy_mmdd_with_default_year(self):
        assert parse_schedule_sheet_title(
            "Schedule 07/13", default_year=2026
        ) == datetime(2026, 7, 13)

    def test_legacy_mmdd_defaults_to_current_year(self):
        parsed = parse_schedule_sheet_title("Schedule 07/13")
        assert parsed is not None
        assert parsed.year == datetime.now().year
        assert (parsed.month, parsed.day) == (7, 13)

    def test_template_returns_none(self):
        assert parse_schedule_sheet_title("Schedule Template") is None

    def test_config_returns_none(self):
        assert parse_schedule_sheet_title("Schedule Config") is None

    def test_non_schedule_prefix_returns_none(self):
        assert parse_schedule_sheet_title("Signups 07/13") is None

    def test_empty_and_none_safe(self):
        assert parse_schedule_sheet_title("") is None

    def test_garbage_date_returns_none(self):
        assert parse_schedule_sheet_title("Schedule 99/99") is None

    def test_prefix_only_returns_none(self):
        assert parse_schedule_sheet_title("Schedule ") is None


class TestCurrentWeekMonday:
    """The rotation anchor must follow the organization's local week.

    Cloud Run containers have no TZ set, so a naive datetime.now() reports
    UTC while Vietnam Hearts operates on Asia/Ho_Chi_Minh (UTC+7). Between
    00:00 and 07:00 Vietnam time the UTC clock is still on the previous day,
    so on Monday mornings a UTC-anchored rotation would target the previous
    week and leave the current week's sheet hidden. A weekly Friday-evening
    cron never entered that window; an hourly cron does, every Monday.
    """

    def test_monday_just_after_local_midnight_anchors_to_that_monday(self):
        # 2026-07-26 17:30 UTC is Sunday in UTC but Monday 00:30 in Vietnam.
        with frozen_at("2026-07-26T17:30:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_same_instant_in_utc_anchors_a_week_earlier(self):
        # Pins the exact bug: same instant, UTC anchor lands a week behind.
        with frozen_at("2026-07-26T17:30:00"):
            assert current_week_monday("UTC") == datetime(2026, 7, 20)

    def test_midweek_anchors_to_that_weeks_monday(self):
        with frozen_at("2026-07-29T07:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_sunday_evening_local_still_anchors_to_that_weeks_monday(self):
        # Sunday 23:00 Vietnam == Sunday 16:00 UTC; still the same local week.
        with frozen_at("2026-08-02T16:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_result_is_naive_and_midnight_normalized(self):
        # Titles are built from naive datetimes elsewhere; mixing in an aware
        # value would raise on comparison.
        with frozen_at("2026-07-29T07:34:56"):
            monday = current_week_monday("Asia/Ho_Chi_Minh")
        assert monday.tzinfo is None
        assert (monday.hour, monday.minute, monday.second, monday.microsecond) == (
            0,
            0,
            0,
            0,
        )

    def test_unknown_timezone_falls_back_to_default(self):
        with frozen_at("2026-07-26T17:30:00"):
            assert current_week_monday("Not/AZone") == current_week_monday(
                DEFAULT_SCHEDULE_TIMEZONE
            )

    def test_empty_timezone_falls_back_to_default(self):
        with frozen_at("2026-07-26T17:30:00"):
            assert current_week_monday("") == datetime(2026, 7, 27)
