"""
Copy a class block out of "Schedule Template" into weekly tabs that already exist.

Rotation builds each weekly tab by duplicating the template, so a class the
captain adds to the template only reaches *future* tabs. The current week and
any future weeks already created keep the old shape, and the classes volunteers
have already signed up for live in those tabs - they cannot be recreated.

This module plans that one-off backfill as data: where the block sits in the
template, where it belongs in the target tab, and which Sheets API requests
move it there. Planning is kept separate from the API call so the exact cells a
run would touch can be printed, and asserted in tests, without a spreadsheet.

Placement is by *position*, never by title text. The same class is titled
"Grade 1B", "Grade 1" and "Grade 1B (Special Needs)" across three live tabs, so
a title match would silently mis-place the new block; its index among the
template's class blocks is unambiguous. A target tab whose block count does not
match the template's, minus the block being added, is reported as a mismatch
rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.schedule_parser import row_is_class_header
from app.utils.schedule_dates import (
    SCHEDULE_DAY_HEADER_FORMAT,
    date_to_sheets_serial,
    dated_weekday_token,
    weekday_token,
)

# Grids here are fetched from column A, so the class title is in column B.
TITLE_COLUMN_INDEX = 1

# The weekday columns, C:G, as a half-open 0-based column range.
DAY_HEADER_START_COLUMN = 2
DAY_HEADER_END_COLUMN = 7

# The block spans A:J - the margin, title, five weekdays, then the merged
# "Classroom Info" pair. Column L carries the sheet-wide "Schedule Key" note, merged down the
# first block's rows on every tab; copying it would land a second copy inside
# the target's own merge, so the copy stops at J.
BLOCK_START_COLUMN = 0
BLOCK_END_COLUMN = 10

_WEEKDAYS_PER_WEEK = DAY_HEADER_END_COLUMN - DAY_HEADER_START_COLUMN


class LayoutMismatch(Exception):
    """The target tab does not match the template closely enough to place a block."""


@dataclass(frozen=True)
class BlockSpan:
    """A class block's rows in a grid, 0-based and half-open.

    ``end`` runs to the next class header (or the end of the grid), so the span
    carries the blank separator row that follows the block. Copying that blank
    row is what keeps the blocks spaced apart after the insert.
    """

    name: str
    start: int
    end: int

    @property
    def row_count(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class BackfillPlan:
    """What one tab needs for the block to land in it."""

    sheet_title: str
    sheet_id: int
    week_monday: datetime
    source: BlockSpan
    # None when the class is already in the tab and nothing will be inserted.
    insert_at: int | None
    already_present: bool

    @property
    def row_count(self) -> int:
        return self.source.row_count

    @property
    def header_row(self) -> int:
        """0-based row the copied class header will occupy."""
        if self.insert_at is None:
            raise ValueError(f"{self.sheet_title} needs no insert")
        return self.insert_at


def _title(row: list[str]) -> str:
    if len(row) <= TITLE_COLUMN_INDEX:
        return ""
    return str(row[TITLE_COLUMN_INDEX] or "").strip()


def _class_name(row: list[str]) -> str:
    """The class name a header row carries: the first line of its title cell."""
    return _title(row).split("\n")[0].strip()


def class_header_rows(grid: list[list[str]]) -> list[tuple[int, str]]:
    """``(row_index, class_name)`` for every class header in ``grid``.

    Headers are recognised by :func:`row_is_class_header`, the same predicate
    the reminder parser and the day-header rotation use, so a row this finds is
    a row they will also treat as a class.
    """
    return [
        (index, _class_name(row))
        for index, row in enumerate(grid)
        if row_is_class_header(row or [], title_index=TITLE_COLUMN_INDEX)
    ]


def find_block(grid: list[list[str]], class_name: str) -> BlockSpan | None:
    """The span of the block named ``class_name``, or None if the grid has none."""
    headers = class_header_rows(grid)
    for position, (index, name) in enumerate(headers):
        if name != class_name:
            continue
        end = headers[position + 1][0] if position + 1 < len(headers) else len(grid)
        return BlockSpan(name=name, start=index, end=end)
    return None


# The weekday each of C:G stands for, which is what makes a copied block's
# day cells land under the right days.
_COLUMN_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")


def _misplaced_day_columns(row: list[str], week_monday: datetime) -> list[str]:
    """Day cells of ``row`` whose weekday disagrees with the column they sit in.

    Blank cells are ignored: a tab whose Monday header was never filled in is
    still aligned (13/04/2026 has one), and the copied block brings its own
    five day cells with it either way. A cell naming the wrong weekday is a
    different matter - the columns mean something else on that tab, so C:G is
    the wrong place to write Monday-to-Friday.
    """
    misplaced = []
    for offset, expected in enumerate(_COLUMN_WEEKDAYS):
        column = DAY_HEADER_START_COLUMN + offset
        label = str((row or [])[column] if column < len(row or []) else "").strip()
        if not label:
            continue
        found = weekday_token(label) or dated_weekday_token(label, week_monday)
        if found is not None and found != expected:
            misplaced.append(f"{'ABCDEFGHIJKL'[column]}={label!r} is {found}")
    return misplaced


def plan_backfill(
    template_grid: list[list[str]],
    target_grid: list[list[str]],
    class_name: str,
    sheet_title: str,
    sheet_id: int,
    week_monday: datetime,
) -> BackfillPlan:
    """Plan the insert of ``class_name`` into one weekly tab.

    Returns a plan with ``already_present`` set when the tab already carries
    the class, which makes a re-run a no-op rather than a second copy.

    Raises:
        LayoutMismatch: when the block cannot be placed unambiguously - the
            template has no such class, the target holds a different number of
            class blocks, the anchor row is not a full five-day header, or the
            row above the insert point holds content that an insert would split
            away from the block it belongs to.
    """
    source = find_block(template_grid, class_name)
    if source is None:
        raise LayoutMismatch(
            f"the template has no class block named {class_name!r}; "
            f"it has {[name for _, name in class_header_rows(template_grid)]}"
        )

    if find_block(target_grid, class_name) is not None:
        return BackfillPlan(
            sheet_title=sheet_title,
            sheet_id=sheet_id,
            week_monday=week_monday,
            source=source,
            insert_at=None,
            already_present=True,
        )

    template_headers = class_header_rows(template_grid)
    target_headers = class_header_rows(target_grid)
    expected = len(template_headers) - 1
    if len(target_headers) != expected:
        raise LayoutMismatch(
            f"{sheet_title} holds {len(target_headers)} class block(s) "
            f"({[name for _, name in target_headers]}), but the template minus "
            f"{class_name!r} has {expected} "
            f"({[name for _, name in template_headers if name != class_name]})"
        )

    position = [name for _, name in template_headers].index(class_name)
    if position >= len(target_headers):
        # The template's own block carries the blank separator that follows
        # it, which is only in the right place when the block is inserted
        # above an existing one. A class the captain appends to the END of the
        # template needs a separator row the other way round, so it is
        # reported rather than guessed at.
        raise LayoutMismatch(
            f"{class_name!r} is the last block in the template, so it would "
            f"have to be appended below {sheet_title}'s last block; this "
            f"backfill only inserts a block above an existing one"
        )

    anchor_row, anchor_name = target_headers[position]
    misplaced = _misplaced_day_columns(target_grid[anchor_row], week_monday)
    if misplaced:
        raise LayoutMismatch(
            f"{sheet_title} row {anchor_row + 1} ({anchor_name!r}) is the "
            f"anchor for {class_name!r}, but its day columns are not "
            f"Monday-to-Friday: {'; '.join(misplaced)}"
        )
    previous = target_grid[anchor_row - 1] if anchor_row > 0 else []
    if any(str(cell).strip() for cell in previous or []):
        raise LayoutMismatch(
            f"{sheet_title} row {anchor_row} (above the insert point) is not "
            f"blank: {[c for c in previous if str(c).strip()]!r}. Inserting "
            f"there would separate it from the block below it"
        )

    return BackfillPlan(
        sheet_title=sheet_title,
        sheet_id=sheet_id,
        week_monday=week_monday,
        source=source,
        insert_at=anchor_row,
        already_present=False,
    )


def day_header_cells(week_monday: datetime) -> list[dict]:
    """The five C:G day-header cells for the week starting ``week_monday``.

    Date serials with an explicit day-month format, exactly as the rotation
    writes them: the copied block arrives carrying the template's own
    month-first format and its placeholder June dates, and text headers are
    what used to let a locale change read 12/10 as 10 December.
    """
    return [
        {
            "userEnteredValue": {
                "numberValue": date_to_sheets_serial(week_monday + timedelta(days=i))
            },
            "userEnteredFormat": {
                "numberFormat": {
                    "type": "DATE",
                    "pattern": SCHEDULE_DAY_HEADER_FORMAT,
                }
            },
        }
        for i in range(_WEEKDAYS_PER_WEEK)
    ]


def build_requests(plan: BackfillPlan, template_sheet_id: int) -> list[dict]:
    """The Sheets API requests that apply ``plan``, in order.

    One batchUpdate of all three is atomic, so a tab is never left holding the
    blank rows of a copy that then failed:

    1. open ``row_count`` rows at the insert point, inheriting no formatting;
    2. copy the template block's A:J over them, which brings values, formats
       and the merged "Classroom Info" pair with it;
    3. overwrite the new header's C:G with this week's dates.
    """
    if plan.already_present:
        return []

    end_row = plan.insert_at + plan.row_count
    return [
        {
            "insertDimension": {
                "range": {
                    "sheetId": plan.sheet_id,
                    "dimension": "ROWS",
                    "startIndex": plan.insert_at,
                    "endIndex": end_row,
                },
                "inheritFromBefore": False,
            }
        },
        {
            "copyPaste": {
                "source": {
                    "sheetId": template_sheet_id,
                    "startRowIndex": plan.source.start,
                    "endRowIndex": plan.source.end,
                    "startColumnIndex": BLOCK_START_COLUMN,
                    "endColumnIndex": BLOCK_END_COLUMN,
                },
                "destination": {
                    "sheetId": plan.sheet_id,
                    "startRowIndex": plan.insert_at,
                    "endRowIndex": end_row,
                    "startColumnIndex": BLOCK_START_COLUMN,
                    "endColumnIndex": BLOCK_END_COLUMN,
                },
                "pasteType": "PASTE_NORMAL",
            }
        },
        {
            "updateCells": {
                "range": {
                    "sheetId": plan.sheet_id,
                    "startRowIndex": plan.header_row,
                    "endRowIndex": plan.header_row + 1,
                    "startColumnIndex": DAY_HEADER_START_COLUMN,
                    "endColumnIndex": DAY_HEADER_END_COLUMN,
                },
                "rows": [{"values": day_header_cells(plan.week_monday)}],
                "fields": "userEnteredValue,userEnteredFormat.numberFormat",
            }
        },
    ]


def describe_plan(plan: BackfillPlan, template_grid: list[list[str]]) -> list[str]:
    """Human-readable lines naming every cell ``plan`` would change."""
    if plan.already_present:
        return [f"{plan.sheet_title}: already has {plan.source.name}, nothing to do"]

    columns = "ABCDEFGHIJKL"
    lines = [
        f"{plan.sheet_title} (gid {plan.sheet_id}): insert {plan.row_count} rows "
        f"at row {plan.insert_at + 1}, shifting rows "
        f"{plan.insert_at + 1}+ down by {plan.row_count}",
    ]
    for offset in range(plan.row_count):
        source_row = list(template_grid[plan.source.start + offset])
        target_row = plan.insert_at + offset + 1
        if plan.source.start + offset == plan.source.start:
            # The day cells are overwritten with this week's dates in the same
            # batch, so show what the cells will end up holding rather than
            # the template's placeholder week.
            for index in range(DAY_HEADER_START_COLUMN, DAY_HEADER_END_COLUMN):
                day = plan.week_monday + timedelta(days=index - DAY_HEADER_START_COLUMN)
                while len(source_row) <= index:
                    source_row.append("")
                source_row[index] = day.strftime("%d/%m/%Y")
        written = [
            f"{columns[column]}{target_row}={str(value).splitlines()[0][:48]!r}"
            for column, value in enumerate(source_row)
            if BLOCK_START_COLUMN <= column < BLOCK_END_COLUMN and str(value).strip()
        ]
        lines.append(
            f"  row {target_row}: " + ("; ".join(written) if written else "(blank)")
        )
    lines.append(
        f"  C{plan.header_row + 1}:G{plan.header_row + 1} are written as date "
        f"serials formatted {SCHEDULE_DAY_HEADER_FORMAT}, not as text"
    )
    return lines
