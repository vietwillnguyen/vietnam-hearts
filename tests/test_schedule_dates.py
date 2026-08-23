"""Tests for schedule sheet title parsing/formatting utilities."""

from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.utils.schedule_dates import (
    _FIXED_DEFAULT_OFFSET,
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
    so a UTC-anchored rotation targets the wrong week and leaves the right
    week's sheet hidden. A weekly Friday-evening cron never entered that
    window; an hourly cron does, once a week.

    The anchor changes value exactly once a week, at Saturday 00:00 local
    (see TestWeekendRollForward), so that is the only boundary at which the
    two clocks can disagree: Friday 17:00-24:00 UTC is already Saturday in
    Vietnam.
    """

    def test_monday_just_after_local_midnight_anchors_to_that_monday(self):
        # 2026-07-26 17:30 UTC is Sunday in UTC but Monday 00:30 in Vietnam.
        with frozen_at("2026-07-26T17:30:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_midweek_anchors_to_that_weeks_monday(self):
        with frozen_at("2026-07-29T07:00:00"):
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


class TestWeekendRollForward:
    """The schedule week is Monday to Friday, so it is over once Friday is.

    Anchoring to the Monday of the week *containing* now left the tabs
    leading with a week whose classes had all already happened, for the
    whole of Saturday and Sunday, until the next Monday arrived. From
    Saturday 00:00 local the window must lead with the coming Monday.
    """

    def test_friday_still_anchors_to_that_weeks_monday(self):
        # Friday 10:00 Vietnam == Friday 03:00 UTC; the week is still running.
        with frozen_at("2026-07-31T03:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_saturday_anchors_to_the_following_monday(self):
        # Saturday 09:00 Vietnam == Saturday 02:00 UTC.
        with frozen_at("2026-08-01T02:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)

    def test_sunday_anchors_to_the_following_monday(self):
        # Sunday 23:00 Vietnam == Sunday 16:00 UTC.
        with frozen_at("2026-08-02T16:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)

    def test_monday_anchors_to_that_same_monday(self):
        # The rolled-forward anchor and the new week's own anchor agree, so
        # the window does not move again when Monday arrives.
        with frozen_at("2026-08-03T02:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)

    def test_last_second_of_friday_has_not_rolled_over_yet(self):
        # Friday 23:59:59 Vietnam == Friday 16:59:59 UTC.
        with frozen_at("2026-07-31T16:59:59"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_first_second_of_saturday_has_rolled_over(self):
        # Saturday 00:00:00 Vietnam == Friday 17:00:00 UTC: the turnover is
        # the first instant that is no longer Friday, not an end-of-classes
        # cutoff earlier on Friday evening.
        with frozen_at("2026-07-31T17:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)

    def test_roll_forward_is_evaluated_in_the_org_timezone(self):
        # Saturday 00:30 Vietnam is still Friday 17:30 UTC. The container
        # clock would keep the finished week on screen for another 7 hours.
        with frozen_at("2026-07-31T17:30:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)
            assert current_week_monday("UTC") == datetime(2026, 7, 27)


class TestMissingTimezoneDatabase:
    """The fallback must not be able to raise the error it exists to absorb.

    `tzdata` is a declared dependency so the IANA database ships inside the
    venv, but if a zone lookup ever fails outright the fallback previously
    called ZoneInfo() again on the default zone - which fails for exactly the
    same reason, re-raising out of current_week_monday and turning every
    hourly reconciliation into a 500.
    """

    def test_falls_back_to_a_fixed_offset_when_no_zone_can_be_loaded(self):
        with (
            frozen_at("2026-07-26T17:30:00"),
            patch(
                "app.utils.schedule_dates.ZoneInfo",
                side_effect=ZoneInfoNotFoundError("No time zone found"),
            ),
        ):
            # 17:30 UTC Sunday is already Monday 00:30 at UTC+7, so the fixed
            # offset must still anchor to the same Monday a real zone would.
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_fixed_offset_still_rolls_the_weekend_forward(self):
        with (
            frozen_at("2026-07-31T17:30:00"),
            patch(
                "app.utils.schedule_dates.ZoneInfo",
                side_effect=ZoneInfoNotFoundError("No time zone found"),
            ),
        ):
            # Friday 17:30 UTC is Saturday 00:30 at UTC+7, so the stand-in
            # offset must roll forward exactly as the real zone does.
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)

    def test_fixed_offset_matches_the_default_zone(self):
        # Vietnam has had no DST since 1975, so the stand-in is exact.
        assert _FIXED_DEFAULT_OFFSET.utcoffset(None) == ZoneInfo(
            DEFAULT_SCHEDULE_TIMEZONE
        ).utcoffset(datetime(2026, 7, 27))
