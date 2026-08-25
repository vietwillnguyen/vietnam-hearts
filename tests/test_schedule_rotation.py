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
  to the Monday of the current schedule week, keep display sheets in
  chronological order, show exactly `display_weeks_count` of them, and
  backfill legacy titles on sheets that are visible or entering the display
  range this run. A legacy "Schedule MM/DD" title carries no year, so
  backfilling one that stays hidden and outside the display range would
  guess a year that could be wrong and later cause a stale sheet to be
  reused as if it were the current week's sheet - such sheets are left
  untouched until they next become relevant.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.google_sheets import (
    SCHEDULE_SHEET_PROTECTION_DESCRIPTION,
    GoogleSheetsService,
)
from app.utils.schedule_dates import current_week_monday, format_schedule_sheet_title
from tests.fixtures.clock import frozen_at


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
    """The Monday rotation will anchor to right now, on the org's clock.

    Delegated rather than recomputed from a naive datetime.now(): the two
    diverge from every Friday through Sunday (and in the 00:00-07:00 Vietnam
    window), which would make the unfrozen tests below fail on the days that
    matter most.
    """
    return current_week_monday()


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


class TestCurrentScheduleDatesFallback:
    """The no-sheet fallbacks must name the same week rotation displays.

    These dates become the weekly reminder's subject line, and that job runs
    at Sunday noon by default - squarely inside the weekend, where a
    containing-week fallback would announce a week whose classes are over.
    They also went through a naive datetime.now(), i.e. UTC on Cloud Run.
    """

    # Saturday 00:30 Vietnam, still Friday 17:30 UTC.
    WEEKEND_INSTANT = "2026-07-31T17:30:00"
    ROLLED_FORWARD_MONDAY = datetime(2026, 8, 3)

    def _assert_rolled_forward(self, service):
        with (
            frozen_at(self.WEEKEND_INSTANT),
            patch(
                "app.services.google_sheets.ConfigHelper.get_schedule_timezone",
                return_value="Asia/Ho_Chi_Minh",
            ),
        ):
            monday, friday = service.get_current_schedule_dates(MagicMock())

        assert monday == self.ROLLED_FORWARD_MONDAY
        assert friday == self.ROLLED_FORWARD_MONDAY + timedelta(days=4)

    def test_no_visible_sheet_falls_back_to_the_rotation_anchor(self, service):
        service.get_schedule_sheets = MagicMock(
            return_value=[sheet_props("Schedule 20/07/2026", sheet_id=2, hidden=True)]
        )
        self._assert_rolled_forward(service)

    def test_unparseable_title_falls_back_to_the_rotation_anchor(self, service):
        service.get_schedule_sheets = MagicMock(
            return_value=[sheet_props("Schedule 99/99", sheet_id=2, hidden=False)]
        )
        self._assert_rolled_forward(service)

    def test_sheet_lookup_failure_falls_back_to_the_rotation_anchor(self, service):
        service.get_schedule_sheets = MagicMock(
            side_effect=Exception("HttpError 503: backend error")
        )
        self._assert_rolled_forward(service)

    def test_fallback_survives_an_unreadable_timezone_setting(self, service):
        """The outer fallback is reached when the db session itself is broken."""
        service.get_schedule_sheets = MagicMock(
            side_effect=Exception("HttpError 503: backend error")
        )

        with (
            frozen_at(self.WEEKEND_INSTANT),
            patch(
                "app.services.google_sheets.ConfigHelper.get_schedule_timezone",
                side_effect=Exception("no database session"),
            ),
        ):
            monday, friday = service.get_current_schedule_dates(MagicMock())

        assert monday == self.ROLLED_FORWARD_MONDAY
        assert friday == self.ROLLED_FORWARD_MONDAY + timedelta(days=4)


class TestRotationAnchorDate:
    """Regression tests for the "current week gets skipped" bug.

    Previously the display window always started at
    `current_monday + timedelta(days=7)`, unconditionally skipping the week
    containing "now". That's correct only if rotation always runs the moment
    the current week ends; it's wrong for an ad-hoc/manual trigger on any
    other day (e.g. the Monday the new week begins), which dropped that
    week's sheet entirely instead of displaying it.

    The skip is right from Friday onwards, though, and only then - the
    displayed week turns over a day before classes end so volunteers can
    sign up early. That case is the last test here, and the anchor rule
    producing it lives in test_schedule_dates.py.
    """

    def test_display_starts_on_current_week_when_run_on_monday(self, service):
        monday = datetime(2026, 7, 20)
        assert monday.weekday() == 0  # sanity check: this really is a Monday

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            result = rotate(service, [], weeks=2)

        assert result["display_dates"][0] == monday.strftime("%d/%m/%Y")
        assert result["display_dates"][1] == (monday + timedelta(days=7)).strftime(
            "%d/%m/%Y"
        )

    def test_display_starts_on_current_weeks_monday_when_run_midweek(self, service):
        # current_week_monday() already collapses any day of the week to that
        # week's Monday (covered in test_schedule_dates.py); what matters here
        # is that rotation builds its window from that anchor rather than from
        # the raw call time.
        expected_monday = datetime(2026, 7, 20)

        with patch(
            "app.services.google_sheets.current_week_monday",
            return_value=expected_monday,
        ):
            result = rotate(service, [], weeks=1)

        assert result["display_dates"][0] == expected_monday.strftime("%d/%m/%Y")

    def test_anchor_is_resolved_in_the_configured_timezone(self, service):
        # Rotation must ask for the org's timezone, not use the container clock.
        with (
            patch(
                "app.services.google_sheets.ConfigHelper.get_schedule_timezone",
                return_value="Asia/Ho_Chi_Minh",
            ) as mock_tz,
            patch(
                "app.services.google_sheets.current_week_monday",
                return_value=datetime(2026, 7, 20),
            ) as mock_anchor,
        ):
            rotate(service, [], weeks=1)

        mock_tz.assert_called_once()
        mock_anchor.assert_called_once_with("Asia/Ho_Chi_Minh")

    def test_display_leads_with_next_monday_when_run_on_friday(self, service):
        """The displayed week turns over on Friday, so the window moves on.

        Unlike its neighbours this exercises the real current_week_monday()
        rather than patching it: the point under test is the anchor rule
        itself reaching the display window, pinned at the exact turnover.
        """
        # Friday 00:30 Vietnam, still Thursday 17:30 UTC - so this also pins
        # that the roll-forward is judged on the org's clock, not the box's.
        with (
            frozen_at("2026-07-30T17:30:00"),
            patch(
                "app.services.google_sheets.ConfigHelper.get_schedule_timezone",
                return_value="Asia/Ho_Chi_Minh",
            ),
        ):
            result = rotate(service, [], weeks=2)

        assert result["display_dates"] == ["03/08/2026", "10/08/2026"]


class TestRotationBackfill:
    """Regression tests: legacy MM/DD titles must be backfilled to DD/MM/YYYY
    for sheets that fall outside the display window and only get hidden,
    but only while they are still visible when rotation encounters them.

    Previously only sheets still inside the display window were renamed
    (in the "show" pass), so a legacy-titled sheet that rotated out of view
    kept its old-format title forever.

    2026-07-21: backfilling was briefly made unconditional (regardless of
    visibility), which meant an already-hidden legacy sheet from a past year
    would be renamed using a guessed current year, baking in a wrong date
    that could later be matched and reused as if it were this year's sheet.
    Backfill is now restricted to sheets that are visible, or that are about
    to become visible this run because their (guessed-year) date falls in
    the display range - a sheet that stays hidden and out of range is left
    untouched instead of having a year guessed for it.
    """

    def test_legacy_sheet_outside_display_range_is_backfilled_when_visible(
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

    def test_already_hidden_legacy_sheet_outside_display_range_is_left_untouched(
        self, service
    ):
        monday = datetime(2026, 7, 20)
        past_date = datetime(2026, 7, 6)  # two weeks before the display window
        legacy_title = f"Schedule {past_date.strftime('%m/%d')}"
        legacy = sheet_props(legacy_title, sheet_id=89, hidden=True)

        with (
            patch("app.services.google_sheets.datetime") as mock_dt,
            patch("app.utils.schedule_dates.datetime") as mock_dates_dt,
        ):
            mock_dt.now.return_value = monday
            mock_dates_dt.now.return_value = monday
            mock_dates_dt.strptime = datetime.strptime
            result = rotate(service, [legacy], weeks=1)

        service.rename_sheet.assert_not_called()
        assert result["sheets_renamed"] == []


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


class TestRotationWriteEconomy:
    """Reconciliation must be a true no-op when nothing has drifted.

    The cadence moved from weekly to hourly, so an unconditional
    show + move on every display sheet went from ~8 pointless batchUpdate
    writes a week to ~192 a day, each one adding a revision-history entry and
    reattributing the spreadsheet's last edit to the service account.
    """

    @pytest.fixture
    def aligned_service(self, service):
        service.ensure_sheet_protected = MagicMock(return_value=False)
        return service

    def _window(self, monday, weeks, hidden=False, indexes=None):
        return [
            sheet_props(
                format_schedule_sheet_title(monday + timedelta(days=7 * i)),
                sheet_id=10 + i,
                index=indexes[i] if indexes else i + 1,
                hidden=hidden,
            )
            for i in range(weeks)
        ]

    def test_no_writes_when_window_is_already_correct(self, aligned_service):
        monday = datetime(2026, 7, 20)
        sheets = [sheet_props("Schedule Template", sheet_id=1, hidden=True)]
        sheets += self._window(monday, weeks=3)

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            result = rotate(aligned_service, sheets, weeks=3)

        aligned_service.set_sheet_visibility.assert_not_called()
        aligned_service.move_sheet.assert_not_called()
        aligned_service.rename_sheet.assert_not_called()
        aligned_service.create_sheet_from_template.assert_not_called()
        assert result["sheets_failed"] == []

    def test_hidden_display_sheet_is_still_unhidden(self, aligned_service):
        monday = datetime(2026, 7, 20)
        sheets = [sheet_props("Schedule Template", sheet_id=1, hidden=True)]
        sheets += self._window(monday, weeks=1, hidden=True)

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            rotate(aligned_service, sheets, weeks=1)

        assert [
            (c.args[0], c.args[1])
            for c in aligned_service.set_sheet_visibility.call_args_list
        ] == [(10, False)]
        aligned_service.move_sheet.assert_not_called()

    def test_out_of_position_display_sheet_is_still_moved(self, aligned_service):
        monday = datetime(2026, 7, 20)
        sheets = [sheet_props("Schedule Template", sheet_id=1, hidden=True)]
        sheets += self._window(monday, weeks=1, indexes=[4])

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            rotate(aligned_service, sheets, weeks=1)

        aligned_service.set_sheet_visibility.assert_not_called()
        assert [
            (c.args[0], c.args[1]) for c in aligned_service.move_sheet.call_args_list
        ] == [(10, 1)]

    def test_newly_created_sheet_still_gets_shown_and_positioned(self, aligned_service):
        monday = datetime(2026, 7, 20)
        aligned_service.create_sheet_from_template = MagicMock(return_value=999)

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            rotate(aligned_service, [], weeks=1)

        assert (999, False) in [
            (c.args[0], c.args[1])
            for c in aligned_service.set_sheet_visibility.call_args_list
        ]
        assert (999, 1) in [
            (c.args[0], c.args[1]) for c in aligned_service.move_sheet.call_args_list
        ]

    def test_scrambled_window_is_reordered_despite_stale_snapshot_indexes(
        self, aligned_service
    ):
        """Skipping a move must be decided on live positions, not the snapshot.

        Positions are read once, up front, but each move shifts every sheet it
        passes. Here moving week 1 into place pushes week 2 off the index its
        snapshot claims, so a snapshot-based comparison would conclude week 2
        was already positioned and leave the window out of order - the exact
        symptom this reconciliation exists to correct.
        """
        monday = datetime(2026, 7, 20)
        sheets = [
            sheet_props("Schedule Template", sheet_id=1, index=0, hidden=True),
            # Not a dated sheet, so rotation never moves it - it just occupies
            # the slot week 1 has to land on.
            sheet_props("Schedule Config", sheet_id=2, index=1),
            sheet_props(
                format_schedule_sheet_title(monday + timedelta(days=7)),
                sheet_id=11,
                index=2,
            ),
            sheet_props(format_schedule_sheet_title(monday), sheet_id=10, index=3),
            sheet_props(
                format_schedule_sheet_title(monday + timedelta(days=14)),
                sheet_id=12,
                index=4,
            ),
        ]

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            rotate(aligned_service, sheets, weeks=3)

        assert [
            (c.args[0], c.args[1]) for c in aligned_service.move_sheet.call_args_list
        ] == [(10, 1), (11, 2), (12, 3)]


class TestSheetProtection:
    """Sheets the app creates must carry their own protection.

    The Google Sheets `duplicateSheet` request copies a tab's content and
    formatting but NOT its protected ranges, so every schedule sheet cloned
    from "Schedule Template" landed unprotected. The spreadsheet is shared
    as link-editable, so an anonymous visitor could edit or delete those
    tabs untraceably - which is how "Schedule 07/06" disappeared twice in
    June/July 2026 with no corresponding app log.

    Protection is warningOnly: volunteers must still be able to fill in
    their own signups, so this is a confirm-prompt speed bump against
    accidents, not a hard lock.
    """

    def test_adds_warning_only_whole_sheet_protection(self, service):
        service.ensure_sheet_protected(4242, MagicMock(), existing_protected_ranges=[])

        body = service.sheet.batchUpdate.call_args.kwargs["body"]
        protected = body["requests"][0]["addProtectedRange"]["protectedRange"]
        assert protected["range"] == {"sheetId": 4242}
        assert protected["warningOnly"] is True

    def test_warning_only_protection_declares_no_editors(self, service):
        # The Sheets API rejects a protectedRange that sets both warningOnly
        # and editors; sending editors here would fail every creation.
        service.ensure_sheet_protected(4242, MagicMock(), existing_protected_ranges=[])

        body = service.sheet.batchUpdate.call_args.kwargs["body"]
        protected = body["requests"][0]["addProtectedRange"]["protectedRange"]
        assert "editors" not in protected

    def test_already_protected_sheet_is_not_protected_again(self, service):
        # Rotation runs hourly; re-adding would stack a new protected range
        # on every run until the sheet is buried in duplicates.
        added = service.ensure_sheet_protected(
            4242,
            MagicMock(),
            existing_protected_ranges=[{"range": {"sheetId": 4242}}],
        )

        assert added is False
        service.sheet.batchUpdate.assert_not_called()

    def test_sheet_carrying_only_a_narrow_protection_still_gets_whole_sheet_cover(
        self, service
    ):
        # A manually protected header row is not whole-sheet coverage. Treating
        # any protectedRange as "done" would skip such a sheet forever while
        # reporting no failure at all.
        added = service.ensure_sheet_protected(
            4242,
            MagicMock(),
            existing_protected_ranges=[
                {
                    "range": {
                        "sheetId": 4242,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                    },
                    "description": "header row",
                }
            ],
        )

        assert added is True
        protected = service.sheet.batchUpdate.call_args.kwargs["body"]["requests"][0][
            "addProtectedRange"
        ]["protectedRange"]
        assert protected["range"] == {"sheetId": 4242}

    def test_protection_recognized_by_its_own_description(self, service):
        # Matches even if the API echoes the range back in a different shape.
        added = service.ensure_sheet_protected(
            4242,
            MagicMock(),
            existing_protected_ranges=[
                {
                    "protectedRangeId": 7,
                    "description": SCHEDULE_SHEET_PROTECTION_DESCRIPTION,
                }
            ],
        )

        assert added is False
        service.sheet.batchUpdate.assert_not_called()

    def test_protection_for_a_different_sheet_does_not_count(self, service):
        added = service.ensure_sheet_protected(
            4242,
            MagicMock(),
            existing_protected_ranges=[{"range": {"sheetId": 9999}}],
        )

        assert added is True

    def test_rotation_backfills_protection_on_unprotected_display_sheets(self, service):
        # Sheets already in the window but created before protection existed
        # (03/08/2026, 10/08/2026, 17/08/2026 in production) must be repaired
        # in place, not left for a human to notice.
        monday = datetime(2026, 7, 20)
        service.ensure_sheet_protected = MagicMock(return_value=True)
        sheets = [sheet_props(format_schedule_sheet_title(monday), sheet_id=70)]

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            rotate(service, sheets, weeks=1)

        protected_ids = [
            c.args[0] for c in service.ensure_sheet_protected.call_args_list
        ]
        assert 70 in protected_ids

    def test_rotation_protects_newly_created_sheet(self, service):
        monday = datetime(2026, 7, 20)
        service.ensure_sheet_protected = MagicMock(return_value=True)
        service.create_sheet_from_template = MagicMock(return_value=999)

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            rotate(service, [], weeks=1)

        protected_ids = [
            c.args[0] for c in service.ensure_sheet_protected.call_args_list
        ]
        assert 999 in protected_ids

    def test_protection_failure_does_not_abort_rotation(self, service):
        # Same lesson as the 2026-07-03 incident: one sheet's failure must
        # never take the whole reconciliation down.
        monday = datetime(2026, 7, 20)
        service.ensure_sheet_protected = MagicMock(
            side_effect=Exception("permission denied")
        )
        sheets = [
            sheet_props(format_schedule_sheet_title(monday), sheet_id=70),
            sheet_props(
                format_schedule_sheet_title(monday + timedelta(days=7)), sheet_id=71
            ),
        ]

        with patch(
            "app.services.google_sheets.current_week_monday", return_value=monday
        ):
            result = rotate(service, sheets, weeks=2)

        # Both sheets still got positioned; only the protection step is recorded
        # failed. Asserted on move rather than on show because these sheets are
        # already visible, and reconciliation no longer rewrites state that is
        # already correct.
        assert [c.args[0] for c in service.move_sheet.call_args_list] == [70, 71]
        assert [f["action"] for f in result["sheets_failed"]] == ["protect", "protect"]
