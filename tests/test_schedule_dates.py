"""Tests for schedule sheet title parsing/formatting utilities."""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.utils.schedule_dates import (
    _FIXED_DEFAULT_OFFSET,
    DEFAULT_SCHEDULE_TIMEZONE,
    DEFAULT_TEACHING_DAYS,
    DEFAULT_TEACHING_DAYS_SETTING,
    current_week_monday,
    format_schedule_sheet_title,
    is_teaching_day,
    parse_schedule_sheet_title,
    parse_teaching_days,
)
from tests.fixtures.clock import frozen_at


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

    The anchor changes value exactly once a week, at Friday 00:00 local
    (see TestRollForwardToTheComingWeek), so that is the only boundary at
    which the two clocks can disagree: Thursday 17:00-24:00 UTC is already
    Friday in Vietnam.
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


class TestRollForwardToTheComingWeek:
    """The displayed week turns over on Friday, a day before classes end.

    Rotation exists so volunteers can sign up for the coming week ahead of
    time, so from Friday 00:00 local the leading tab is next week's and
    Friday's own classes are no longer led with. Anchoring to the Monday of
    the week *containing* now instead left the tabs leading with a nearly
    finished week for three days, until the next Monday arrived.
    """

    def test_thursday_still_anchors_to_that_weeks_monday(self):
        # Thursday 10:00 Vietnam == Thursday 03:00 UTC; still the leading week.
        with frozen_at("2026-07-30T03:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_friday_anchors_to_the_following_monday(self):
        # Friday 10:00 Vietnam == Friday 03:00 UTC.
        with frozen_at("2026-07-31T03:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)

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

    def test_last_second_of_thursday_has_not_rolled_over_yet(self):
        # Thursday 23:59:59 Vietnam == Thursday 16:59:59 UTC.
        with frozen_at("2026-07-30T16:59:59"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 7, 27)

    def test_first_second_of_friday_has_rolled_over(self):
        # Friday 00:00:00 Vietnam == Thursday 17:00:00 UTC: the turnover is
        # the first instant that is no longer Thursday, not a cutoff at some
        # hour of Friday.
        with frozen_at("2026-07-30T17:00:00"):
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)

    def test_roll_forward_is_evaluated_in_the_org_timezone(self):
        # Friday 00:30 Vietnam is still Thursday 17:30 UTC. The container
        # clock would keep the outgoing week leading for another 7 hours.
        with frozen_at("2026-07-30T17:30:00"):
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

    def test_fixed_offset_still_rolls_the_week_forward(self):
        with (
            frozen_at("2026-07-30T17:30:00"),
            patch(
                "app.utils.schedule_dates.ZoneInfo",
                side_effect=ZoneInfoNotFoundError("No time zone found"),
            ),
        ):
            # Thursday 17:30 UTC is Friday 00:30 at UTC+7, so the stand-in
            # offset must roll forward exactly as the real zone does.
            assert current_week_monday("Asia/Ho_Chi_Minh") == datetime(2026, 8, 3)

    def test_fixed_offset_matches_the_default_zone(self):
        # Vietnam has had no DST since 1975, so the stand-in is exact.
        assert _FIXED_DEFAULT_OFFSET.utcoffset(None) == ZoneInfo(
            DEFAULT_SCHEDULE_TIMEZONE
        ).utcoffset(datetime(2026, 7, 27))


class TestTeachingDays:
    """The days classes actually run on, matched against sheet day labels.

    Vietnam Hearts teaches on Tuesday and Thursday; the schedule grid keeps a
    column for every weekday and leaves the rest blank. build_class_table uses
    this to tell a day with no class apart from an unfilled teaching slot.
    """

    def test_default_is_tuesday_and_thursday(self):
        assert parse_teaching_days(None) == {"tue", "thu"}

    def test_parses_a_settings_string(self):
        assert parse_teaching_days("Monday, Wednesday, Friday") == {
            "mon",
            "wed",
            "fri",
        }

    def test_accepts_abbreviations_and_odd_separators(self):
        assert parse_teaching_days("Tue; Thu / Sat") == {"tue", "thu", "sat"}

    def test_keeps_every_day_named_in_one_unseparated_entry(self):
        # "Tuesday and Thursday" is a plausible way to fill the setting in.
        # Keeping only the first day would leave the set non-empty, so the
        # fallback below never fires: every blank Thursday cell would render
        # as "No class" and the reminder would stop being sent at all.
        assert parse_teaching_days("Tuesday and Thursday") == {"tue", "thu"}
        assert parse_teaching_days("Tue Thu") == {"tue", "thu"}
        assert parse_teaching_days(["Tuesday and Thursday"]) == {"tue", "thu"}

    def test_a_day_named_second_in_an_entry_is_still_a_teaching_day(self):
        assert is_teaching_day("Thursday 6/25", "Tuesday and Thursday") is True
        assert is_teaching_day("Wednesday 6/24", "Tuesday and Thursday") is False

    def test_accepts_an_iterable_of_day_names(self):
        assert parse_teaching_days(["Tuesday", "Thursday"]) == {"tue", "thu"}

    def test_blank_value_falls_back_to_the_default(self):
        assert parse_teaching_days("") == parse_teaching_days(None)

    def test_value_naming_no_weekday_falls_back_rather_than_emptying(self):
        # An empty teaching week would mark every day non-teaching and so
        # suppress the weekly reminder entirely.
        assert parse_teaching_days("whenever") == {"tue", "thu"}

    def test_matches_a_bare_weekday_name(self):
        assert is_teaching_day("Tuesday") is True
        assert is_teaching_day("Monday") is False

    def test_matches_a_label_carrying_a_date(self):
        assert is_teaching_day("Thursday 6/25") is True
        assert is_teaching_day("Wed 6/24") is False

    def test_honours_an_explicit_teaching_week(self):
        assert is_teaching_day("Monday", ["Monday"]) is True
        assert is_teaching_day("Tuesday", ["Monday"]) is False

    def test_unrecognized_label_counts_as_a_teaching_day(self):
        # Failing open keeps a genuinely unfilled slot visible; failing closed
        # would drop it from the reminder with no other symptom.
        assert is_teaching_day("Week 3 session") is True
        assert is_teaching_day("") is True

    def test_seeded_setting_value_parses_to_the_code_default(self):
        assert parse_teaching_days(
            DEFAULT_TEACHING_DAYS_SETTING
        ) == parse_teaching_days(DEFAULT_TEACHING_DAYS)
