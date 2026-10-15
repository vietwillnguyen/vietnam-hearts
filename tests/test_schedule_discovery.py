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

Every header fixture below carries at least three day columns, because that is
what row_is_class_header() requires - see _MIN_DAY_LABELS. A real header always
has a column per weekday; demanding a quorum is what stops a "Teacher" row of
volunteer names ("Thu Hang", "Thu Hằng") from being read as a fourth class.
"""

from app.services.schedule_parser import ClassBlock, discover_schedule_blocks


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
            ["Combined 3 & 4\n9:30 - 10:30 AM", "Mon", "Tue", "Wed"],
            ["Teacher", "Lydie", "Need Volunteers", ""],
            [
                "Head Assistant (min 1), must handle attendance and supply box",
                "Thanh Thao",
                "Michael",
                "",
            ],
            ["Assistants MAX 1", "Hai Chau, Florian", "Trang Vo", ""],
            ["Curriculum & Lesson Plan", "1-20", "[link]", ""],
        ]
        b = discover_schedule_blocks(rows)[0]
        assert b.name == "Combined 3 & 4"
        assert b.has_head_ta is True
        assert b.head_ta == ("Thanh Thao", "Michael", "")
        assert b.assistants == ("Hai Chau, Florian", "Trang Vo", "")
        assert b.max_assistants == 1

    def test_max_assistants_none_when_no_max_label(self):
        rows = [
            ["Grade X\n10 AM", "Mon", "Tue", "Wed"],
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
            ["Teacher", "A"],  # Tue, Wed trimmed away
            ["Assistants MAX 2"],  # all day values trimmed away
        ]
        b = discover_schedule_blocks(rows)[0]
        assert b.days == ("Mon", "Tue", "Wed")
        assert b.teacher == ("A", "", "")
        assert b.assistants == ("", "", "")
        assert b.max_assistants == 2

    def test_multiple_blocks_separated_by_blank_rows(self):
        rows = (
            _grade2a_rows()
            + [[]]
            + [
                ["Grade 2B\n9:30 - 10:30 AM", "Mon", "Tue", "Wed", "Thu", "Fri"],
                ["Teacher", "", "Trúc", "", "Thanh Thao", ""],
                ["Assistants MAX 2", "", "Yến, Thomas", "", "Trâm Võ; Vi", ""],
            ]
        )
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


class TestUpdatedTemplateShape:
    """The template the captain changed on 2026-10-15.

    Grade 1A is the first class whose title names a time per day, because it
    meets Tuesday morning and Thursday afternoon. Nothing in the parser
    enumerates classes, so the fourth block needs no code change - these pin
    that, and the heading the reminder builds from the multi-line title.
    """

    def _blocks(self):
        from tests.fixtures.schedule_sheets import TEMPLATE_GRID

        # Production reads B1:G100, so the parser sees the label column and
        # the five weekday columns - never the "Classroom Info" pair in I:J,
        # which it would otherwise count as two more days.
        return discover_schedule_blocks([row[1:7] for row in TEMPLATE_GRID])

    def test_the_template_parses_as_four_classes_in_sheet_order(self):
        assert [block.name for block in self._blocks()] == [
            "Grade 1A",
            "Grade 1B (Special Needs)",
            "Grade 2",
            "Grade 5",
        ]

    def test_a_per_day_time_reads_as_a_list_not_a_run_on(self):
        grade_1a = self._blocks()[0]

        assert grade_1a.time == "TUESDAY 9:30 - 10:30 AM, THURSDAY 3:30 - 4:30 PM"

    def test_every_block_carries_a_head_assistant_row_and_a_cap_of_four(self):
        blocks = self._blocks()

        assert all(block.has_head_ta for block in blocks)
        assert {block.max_assistants for block in blocks} == {4}

    def test_the_unfilled_template_asks_for_volunteers_on_teaching_days(self):
        grade_1a = self._blocks()[0]

        # C:G are Monday to Friday; the template pre-fills Tuesday and
        # Thursday, the two days Vietnam Hearts teaches.
        assert grade_1a.teacher == (
            "",
            "Need Volunteers",
            "",
            "Need Volunteers",
            "",
        )
