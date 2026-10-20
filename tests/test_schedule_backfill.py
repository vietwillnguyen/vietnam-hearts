"""
Backfilling a template class block into weekly tabs that already exist.

Rotation only applies a template change to tabs it has yet to create, so the
class the captain adds reaches next month's sheets and never the week
volunteers are signing up on right now. These tests pin the one-off backfill
that closes that gap, using grids shaped like the live spreadsheet:

- it goes in at the template's position, not next to a title that looks like
  it, because the same class is titled three different ways across three
  live tabs;
- it never runs twice on the same tab;
- it refuses, with specifics, any tab it cannot place the block in - the
  alternative is a block silently landing inside somebody's signups.
"""

from datetime import datetime

import pytest

from app.services.schedule_backfill import (
    BLOCK_END_COLUMN,
    BLOCK_START_COLUMN,
    BackfillPlan,
    BlockSpan,
    LayoutMismatch,
    build_requests,
    class_header_rows,
    day_header_cells,
    describe_plan,
    find_block,
    plan_backfill,
)
from app.services.schedule_parser import discover_schedule_blocks
from app.utils.schedule_dates import SCHEDULE_DAY_HEADER_FORMAT, date_to_sheets_serial
from tests.fixtures.schedule_sheets import (
    CURRENT_WEEK_GRID,
    TEMPLATE_GRID,
    WEEK_12_10_DAY_HEADERS,
)

WEEK_MONDAY = datetime(2026, 10, 12)
WEEK_DAY_LABELS = WEEK_12_10_DAY_HEADERS

# A class the captain might one day append below Grade 5.
GRADE_9_BLOCK = [
    ["", "Grade 9\n9:30 - 10:30 AM", *WEEK_12_10_DAY_HEADERS],
    ["", "Teacher", "", "Need Volunteers", "", "Need Volunteers"],
    ["", "Assistants MAX 4", "", "Need Volunteers", "", "Need Volunteers"],
]
TEMPLATE_SHEET_ID = 1911285501
TARGET_SHEET_ID = 1775524467
TARGET_TITLE = "Schedule 12/10/2026"


def plan(target_grid=None, class_name="Grade 1A", template_grid=None):
    return plan_backfill(
        template_grid=TEMPLATE_GRID if template_grid is None else template_grid,
        target_grid=CURRENT_WEEK_GRID if target_grid is None else target_grid,
        class_name=class_name,
        sheet_title=TARGET_TITLE,
        sheet_id=TARGET_SHEET_ID,
        week_monday=WEEK_MONDAY,
    )


class TestBlockDiscovery:
    def test_template_carries_four_blocks_with_grade_1a_first(self):
        assert [name for _, name in class_header_rows(TEMPLATE_GRID)] == [
            "Grade 1A",
            "Grade 1B (Special Needs)",
            "Grade 2",
            "Grade 5",
        ]

    def test_a_block_span_runs_to_the_next_header_so_it_carries_its_separator(self):
        span = find_block(TEMPLATE_GRID, "Grade 1A")

        # Rows 7-11 of the sheet are the block, row 12 is the blank row that
        # keeps it apart from Grade 1B. Copying that blank row is what keeps
        # the blocks spaced after an insert.
        assert (span.start, span.end, span.row_count) == (6, 12, 6)
        assert not any(str(cell).strip() for cell in TEMPLATE_GRID[span.end - 1])

    def test_the_last_block_span_runs_to_the_end_of_the_grid(self):
        span = find_block(TEMPLATE_GRID, "Grade 5")
        assert span.end == len(TEMPLATE_GRID)

    def test_a_class_the_template_does_not_have_is_not_found(self):
        assert find_block(TEMPLATE_GRID, "Grade 9") is None


class TestPlacement:
    def test_grade_1a_goes_above_the_tabs_first_block(self):
        result = plan()

        # The live tab's first class header is row 9, two rows below the
        # template's, so the insert point has to be read from the tab.
        assert result.insert_at == 8
        assert result.row_count == 6
        assert not result.already_present

    def test_placement_ignores_the_title_the_tab_gives_that_class(self):
        """The anchor class is "Grade 1B" here, "Grade 1" and "Grade 1B
        (Special Needs)" on the two future tabs. Position is the only thing
        all three agree on."""
        renamed = [list(row) for row in CURRENT_WEEK_GRID]
        renamed[8][1] = "Grade 1\n9:30 - 10:30 AM"

        assert plan(renamed).insert_at == 8

    def test_rerunning_on_a_backfilled_tab_changes_nothing(self):
        once = plan()
        applied = apply_plan(once, CURRENT_WEEK_GRID)

        twice = plan(applied)

        assert twice.already_present
        assert build_requests(twice, TEMPLATE_SHEET_ID) == []
        assert describe_plan(twice, TEMPLATE_GRID) == [
            "Schedule 12/10/2026: already has Grade 1A, nothing to do"
        ]

    def test_the_backfilled_tab_parses_as_four_classes_in_template_order(self):
        applied = apply_plan(plan(), CURRENT_WEEK_GRID)

        blocks = discover_schedule_blocks([row[1:7] for row in applied])

        assert [block.name for block in blocks] == [
            "Grade 1A",
            "Grade 1B",
            "Grade 2",
            "Grade 5",
        ]

    def test_existing_signups_are_untouched_and_keep_their_order(self):
        result = plan()
        applied = apply_plan(result, CURRENT_WEEK_GRID)

        # Everything outside the six inserted rows, in order, is what the tab
        # held before - no signup is dropped, rewritten or reordered.
        kept = (
            applied[: result.insert_at] + applied[result.insert_at + result.row_count :]
        )
        assert kept == [list(row) for row in CURRENT_WEEK_GRID]


class TestRefusals:
    def test_a_tab_with_an_unexpected_block_count_is_refused(self):
        short = [row for row in CURRENT_WEEK_GRID[:14]]

        with pytest.raises(LayoutMismatch) as refusal:
            plan(short)

        assert "holds 1 class block(s)" in str(refusal.value)
        assert "has 3" in str(refusal.value)

    def test_a_class_missing_from_the_template_is_refused(self):
        with pytest.raises(LayoutMismatch) as refusal:
            plan(class_name="Grade 9")

        assert "no class block named 'Grade 9'" in str(refusal.value)

    def test_an_anchor_whose_day_columns_are_shifted_is_refused(self):
        """The copied block writes Monday-to-Friday into C:G, so a tab whose
        C:G mean something else cannot take it."""
        shifted = [list(row) for row in CURRENT_WEEK_GRID]
        shifted[8][2:7] = ["", *WEEK_DAY_LABELS[:4]]

        with pytest.raises(LayoutMismatch) as refusal:
            plan(shifted)

        assert "day columns are not Monday-to-Friday" in str(refusal.value)
        assert "D='Thứ Hai 12/10' is mon" in str(refusal.value)

    def test_an_anchor_with_an_unfilled_monday_is_still_accepted(self):
        """13/04/2026 has a class header with a blank Monday cell; a gap is
        not a shift."""
        gapped = [list(row) for row in CURRENT_WEEK_GRID]
        gapped[8][2] = ""

        assert plan(gapped).insert_at == 8

    def test_content_directly_above_the_insert_point_is_refused(self):
        occupied = [list(row) for row in CURRENT_WEEK_GRID]
        occupied[7] = ["", "", "", "Ha My"]

        with pytest.raises(LayoutMismatch) as refusal:
            plan(occupied)

        assert "above the insert point) is not blank" in str(refusal.value)
        assert "Ha My" in str(refusal.value)

    def test_a_block_the_template_appends_last_is_refused(self):
        """A class added to the END of the template needs its separator row
        above it rather than below, so this backfill refuses it instead of
        butting it against the block before it."""
        extended = TEMPLATE_GRID + [[], *GRADE_9_BLOCK]
        backfilled = apply_plan(plan(), CURRENT_WEEK_GRID)

        with pytest.raises(LayoutMismatch) as refusal:
            plan(backfilled, class_name="Grade 9", template_grid=extended)

        assert "last block in the template" in str(refusal.value)


class TestRequests:
    def test_the_three_requests_open_rows_copy_the_block_and_date_it(self):
        requests = build_requests(plan(), TEMPLATE_SHEET_ID)

        insert, copy, dates = requests
        assert insert["insertDimension"] == {
            "range": {
                "sheetId": TARGET_SHEET_ID,
                "dimension": "ROWS",
                "startIndex": 8,
                "endIndex": 14,
            },
            "inheritFromBefore": False,
        }
        assert copy["copyPaste"]["source"] == {
            "sheetId": TEMPLATE_SHEET_ID,
            "startRowIndex": 6,
            "endRowIndex": 12,
            "startColumnIndex": BLOCK_START_COLUMN,
            "endColumnIndex": BLOCK_END_COLUMN,
        }
        assert copy["copyPaste"]["destination"]["startRowIndex"] == 8
        assert copy["copyPaste"]["destination"]["endRowIndex"] == 14
        assert copy["copyPaste"]["pasteType"] == "PASTE_NORMAL"
        assert dates["updateCells"]["range"] == {
            "sheetId": TARGET_SHEET_ID,
            "startRowIndex": 8,
            "endRowIndex": 9,
            "startColumnIndex": 2,
            "endColumnIndex": 7,
        }

    def test_the_copy_stops_at_column_j_so_it_misses_the_schedule_key(self):
        """Column L holds one merged "Schedule Key" note per tab. Copying the
        template's copy of it into the new rows would drop a second merge
        inside the target's own."""
        copy = build_requests(plan(), TEMPLATE_SHEET_ID)[1]["copyPaste"]

        assert copy["source"]["endColumnIndex"] == 10
        assert copy["destination"]["endColumnIndex"] == 10

    def test_day_headers_are_written_as_this_weeks_dated_serials(self):
        cells = day_header_cells(WEEK_MONDAY)

        assert [cell["userEnteredValue"]["numberValue"] for cell in cells] == [
            date_to_sheets_serial(datetime(2026, 10, day)) for day in range(12, 17)
        ]
        assert {
            cell["userEnteredFormat"]["numberFormat"]["pattern"] for cell in cells
        } == {SCHEDULE_DAY_HEADER_FORMAT}

    def test_a_header_row_cannot_be_asked_for_when_nothing_is_inserted(self):
        noop = BackfillPlan(
            sheet_title=TARGET_TITLE,
            sheet_id=TARGET_SHEET_ID,
            week_monday=WEEK_MONDAY,
            source=BlockSpan("Grade 1A", 6, 12),
            insert_at=None,
            already_present=True,
        )

        with pytest.raises(ValueError):
            _ = noop.header_row


class TestDryRunOutput:
    def test_every_cell_the_run_would_write_is_named(self):
        lines = describe_plan(plan(), TEMPLATE_GRID)

        assert "insert 6 rows at row 9" in lines[0]
        assert "B9='Grade 1A'" in lines[1]
        assert "I9='Classroom Info'" in lines[1]
        assert "B10='Teacher'" in lines[2]
        assert "D10='Need Volunteers'" in lines[2]
        assert lines[-2].endswith("(blank)")
        assert "C9:G9 are written as date serials" in lines[-1]

    def test_the_header_row_is_shown_with_this_weeks_dates_not_the_templates(self):
        """The copy brings the template's placeholder week with it and the
        same batch overwrites it, so the plan shows the end state."""
        header_line = describe_plan(plan(), TEMPLATE_GRID)[1]

        assert "C9='12/10/2026'" in header_line
        assert "G9='16/10/2026'" in header_line
        assert "Thứ Hai" not in header_line


def apply_plan(plan, target_grid):
    """``target_grid`` as it looks after ``plan`` is applied.

    A local stand-in for what the Sheets API does with the three requests:
    open the rows, copy the template's block over them, and date the header.
    """
    block = [
        list(row[BLOCK_START_COLUMN:BLOCK_END_COLUMN])
        for row in TEMPLATE_GRID[plan.source.start : plan.source.end]
    ]
    dated = [
        (WEEK_MONDAY.strftime("Thứ Hai %-d/%-m")),
        "Thứ Ba 13/10",
        "Thứ Tư 14/10",
        "Thứ Năm 15/10",
        "Thứ Sáu 16/10",
    ]
    block[0][2:7] = dated
    return [
        *[list(row) for row in target_grid[: plan.insert_at]],
        *block,
        *[list(row) for row in target_grid[plan.insert_at :]],
    ]
