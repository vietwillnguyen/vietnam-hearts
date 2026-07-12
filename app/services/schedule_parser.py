"""
Schedule auto-discovery parser.

The Google Sheet "Schedule" tab is the single source of truth for the weekly
reminder email. This module turns the raw sheet grid (as returned by the Sheets
API) into structured, immutable ``ClassBlock`` objects.

Why parse instead of using a separate "Schedule Config" tab with hardcoded cell
ranges (e.g. ``B7:G11``):

- The Sheets API trims trailing empty rows/cells, so fixed ranges silently drop
  rows and misalign days week to week.
- Inserting/removing a row in the sheet breaks every hardcoded range.
- Class structure now varies: some classes have a "Head Assistant" row, most do
  not. ``max_assistants`` already lives in the sheet ("Assistants MAX 1").

Discovery keys off row labels in the first column, which tolerates all of the
above. Values are expected starting at column B, so index 0 of each row is the
label/title column and index 1+ are the per-day values.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# Matches a weekday at a word boundary, full or 3-letter abbreviation
# (e.g. "Monday 6/22" or "Mon"), so header detection is robust to either format.
_WEEKDAY_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)", re.IGNORECASE)
_MAX_RE = re.compile(r"max\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ClassBlock:
    """One class block parsed from the schedule grid.

    ``teacher``, ``head_ta`` and ``assistants`` are aligned to ``days`` (padded
    on the right to the same length). ``head_ta`` is empty when the class has no
    Head Assistant row. ``max_assistants`` is ``None`` when the sheet imposes no
    limit (label has no "MAX N" token).
    """

    name: str
    time: str
    max_assistants: int | None
    has_head_ta: bool
    days: tuple[str, ...]
    teacher: tuple[str, ...]
    head_ta: tuple[str, ...]
    assistants: tuple[str, ...]


def _cell(row: Sequence[str], idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) else ""


def row_is_class_header(row: Sequence[str], title_index: int = 0) -> bool:
    """True if ``row`` is a class header: a non-empty title cell followed by
    weekday labels.

    ``title_index`` is the column holding the class title (0 when the grid is
    fetched from column B, 1 when fetched from column A).
    """
    if not row or not _cell(row, title_index):
        return False
    return any(_WEEKDAY_RE.search(str(c)) for c in row[title_index + 1 :])


def _is_header_row(row: Sequence[str]) -> bool:
    return row_is_class_header(row, title_index=0)


def _pad(values: Sequence[str], length: int) -> tuple[str, ...]:
    """Right-pad/truncate day values to align with the header day count."""
    cleaned = [str(v).strip() for v in values]
    if len(cleaned) < length:
        cleaned += [""] * (length - len(cleaned))
    return tuple(cleaned[:length])


def discover_schedule_blocks(rows: list[list[str]]) -> list[ClassBlock]:
    """Parse the raw schedule grid into a list of :class:`ClassBlock`.

    Args:
        rows: 2D grid from the Schedule tab, fetched starting at column B
            (index 0 = label/title column, index 1+ = per-day values).

    Returns:
        Class blocks in sheet order. Non-class rows (titles, announcements,
        curriculum, blank separators) are ignored.
    """
    blocks: list[ClassBlock] = []
    i = 0
    n = len(rows)

    while i < n:
        row = rows[i] if rows[i] is not None else []
        if not _is_header_row(row):
            i += 1
            continue

        # Start a new block from this header row.
        title = _cell(row, 0)
        title_parts = [p.strip() for p in title.split("\n")]
        name = title_parts[0]
        time = " ".join(p for p in title_parts[1:] if p)
        days = tuple(str(c).strip() for c in row[1:])

        teacher: tuple[str, ...] = ()
        head_ta: tuple[str, ...] = ()
        assistants: tuple[str, ...] = ()
        has_head_ta = False
        max_assistants: int | None = None

        # Consume rows until the next header row or end of grid.
        i += 1
        while i < n and not _is_header_row(rows[i] or []):
            label_row = rows[i] or []
            label = _cell(label_row, 0)
            i += 1
            if not label:
                continue  # blank separator row
            low = label.lower()
            values = _pad(label_row[1:], len(days))
            if low.startswith("teacher"):
                teacher = values
            elif "head" in low:
                head_ta = values
                has_head_ta = True
            elif low.startswith("assistant"):
                assistants = values
                match = _MAX_RE.search(label)
                max_assistants = int(match.group(1)) if match else None
            # curriculum / lesson plan / anything else: ignored

        blocks.append(
            ClassBlock(
                name=name,
                time=time,
                max_assistants=max_assistants,
                has_head_ta=has_head_ta,
                days=days,
                teacher=teacher or _pad((), len(days)),
                head_ta=head_ta,
                assistants=assistants or _pad((), len(days)),
            )
        )

    return blocks
