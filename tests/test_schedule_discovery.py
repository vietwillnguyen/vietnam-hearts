"""
Tests for schedule auto-discovery parser.

The Schedule tab is the single source of truth. These tests pin down the parser
that turns the raw sheet grid into structured class blocks, covering the real
sheet shapes that broke the weekly reminder email:

- classes that no longer have a "Head TA" row (just Teacher + Assistants)
- classes that DO have a "Head Assistant" row
- "Assistants MAX N" labels (and the no-MAX -> no-limit case)
- Google Sheets trailing-empty-cell/row trimming (ragged rows)
- blank separator rows between class blocks
- non-class header/announcement rows that must be ignored
"""

import pytest
from app.services.schedule_parser import discover_schedule_blocks, ClassBlock


def _grade2a_rows():
    # Class WITHOUT a Head TA row; assistants capped at 1; blank separator + curriculum.
    # Values are fetched starting at column B, so index 0 is the label/title column.
    return [
        ["Grade 2A\n9:30 - 10:30 AM", "Mon", "Tue", "Wed", "Thu", "Fri"],
        ["Teacher", "", "Truc Quynh", "", "Truc Quynh", ""],
        ["Assistants MAX 1", "", "Trí", "", "Thomas", ""],
        [],
        ["Curriculum & Lesson Plan", "", "Review", "", "Test", ""],
    ]


class TestDiscoverScheduleBlocks:
    def test_single_block_without_head_ta(self):
        blocks = discover_schedule_blocks(_grade2a_rows())
        assert len(blocks) == 1
        b = blocks[0]
        assert isinstance(b, ClassBlock)
        assert b.name == "Grade 2A"
        assert b.time == "9:30 - 10:30 AM"
        assert b.max_assistants == 1
        assert b.has_head_ta is False
        assert b.days == ("Mon", "Tue", "Wed", "Thu", "Fri")
        assert b.teacher == ("", "Truc Quynh", "", "Truc Quynh", "")
        assert b.assistants == ("", "Trí", "", "Thomas", "")
        assert b.head_ta == ()

    def test_block_with_head_assistant_row(self):
        rows = [
            ["Combined 3 & 4\n9:30 - 10:30 AM", "Mon", "Tue"],
            ["Teacher", "Lydie", "Need Volunteers"],
            ["Head Assistant (min 1), must handle attendance and supply box", "Thanh Thao", "Michael"],
            ["Assistants MAX 1", "Hai Chau, Florian", "Trang Vo"],
            ["Curriculum & Lesson Plan", "1-20", "[link]"],
        ]
        b = discover_schedule_blocks(rows)[0]
        assert b.name == "Combined 3 & 4"
        assert b.has_head_ta is True
        assert b.head_ta == ("Thanh Thao", "Michael")
        assert b.assistants == ("Hai Chau, Florian", "Trang Vo")
        assert b.max_assistants == 1

    def test_max_assistants_none_when_no_max_label(self):
        rows = [
            ["Grade X\n10 AM", "Mon"],
            ["Teacher", "A"],
            ["Assistants", "B"],  # no MAX token -> no limit
        ]
        b = discover_schedule_blocks(rows)[0]
        assert b.max_assistants is None

    def test_trailing_trimmed_rows_are_padded(self):
        # Google Sheets trims trailing empty cells, so rows arrive shorter than the
        # header. Padding must happen on the RIGHT to keep day alignment correct.
        rows = [
            ["Grade Y\n10 AM", "Mon", "Tue", "Wed"],
            ["Teacher", "A"],          # Tue, Wed trimmed away
            ["Assistants MAX 2"],      # all day values trimmed away
        ]
        b = discover_schedule_blocks(rows)[0]
        assert b.days == ("Mon", "Tue", "Wed")
        assert b.teacher == ("A", "", "")
        assert b.assistants == ("", "", "")
        assert b.max_assistants == 2

    def test_multiple_blocks_separated_by_blank_rows(self):
        rows = _grade2a_rows() + [[]] + [
            ["Grade 2B\n9:30 - 10:30 AM", "Mon", "Tue", "Wed", "Thu", "Fri"],
            ["Teacher", "", "Trúc", "", "Thanh Thao", ""],
            ["Assistants MAX 2", "", "Yến, Thomas", "", "Trâm Võ; Vi", ""],
        ]
        blocks = discover_schedule_blocks(rows)
        assert [b.name for b in blocks] == ["Grade 2A", "Grade 2B"]
        assert blocks[1].max_assistants == 2
        assert blocks[1].has_head_ta is False

    def test_ignores_non_class_rows(self):
        rows = [
            ["", "Schedule for Week 06/22"],
            [],
            ["", "Contact Us on FB", "Volunteer Sign Up Form"],
            ["How To Use: 1. PLEASE USE YOUR FULL NAME below to sign in"],
        ] + _grade2a_rows()
        blocks = discover_schedule_blocks(rows)
        assert len(blocks) == 1
        assert blocks[0].name == "Grade 2A"

    def test_empty_input_returns_empty(self):
        assert discover_schedule_blocks([]) == []
        assert discover_schedule_blocks([[], [""], [""]]) == []
