"""
Tests for email service functionality.

build_class_table now renders from a parsed ClassBlock (single source of truth =
the Schedule tab). The Head Teaching Assistant column is only rendered for classes
that actually have a Head Assistant row.

Tests cover:
- Max assistants enforcement / counting
- Status logic (missing teacher / head TA / assistants, optional, no class, no limit)
- Head TA column dropped when the class has no head TA row
"""

from app.services.email_service import EmailService
from app.services.schedule_parser import ClassBlock


def _block(
    *,
    name="Test Class",
    time="9:30 - 10:30 AM",
    max_assistants=3,
    has_head_ta=False,
    days,
    teacher,
    assistants,
    head_ta=(),
):
    """Build a ClassBlock for tests, padding rows to the day count."""
    n = len(days)

    def pad(seq):
        seq = tuple(seq)
        return seq + ("",) * (n - len(seq))

    return ClassBlock(
        name=name,
        time=time,
        max_assistants=max_assistants,
        has_head_ta=has_head_ta,
        days=tuple(days),
        teacher=pad(teacher),
        head_ta=pad(head_ta) if has_head_ta else (),
        assistants=pad(assistants),
    )


class TestBuildClassTable:
    """Test build_class_table rendering from a ClassBlock"""

    def test_max_assistants_enforcement(self):
        block = _block(
            max_assistants=2,
            days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            teacher=[
                "John Doe",
                "Jane Smith",
                "Bob Wilson",
                "Alice Brown",
                "Charlie Davis",
            ],
            assistants=["TA1, TA2, TA3", "TA1", "TA1, TA2", "", "TA1, TA2, TA3, TA4"],
        )
        result = EmailService().build_class_table(block)

        assert result["has_data"] is True
        assert result["class_name"] == "Test Class"
        html = result["table_html"]
        assert "Max 2 Assistants" in html
        assert "Fully Covered (3/2 assistants)" in html
        assert "Partially Covered (1/2 assistants)" in html
        assert "Fully Covered (2/2 assistants)" in html
        assert "Partially Covered (0/2 assistants)" in html
        assert "Fully Covered (4/2 assistants)" in html

    def test_assistant_counting_logic(self):
        block = _block(
            max_assistants=5,
            days=["Monday", "Tuesday", "Wednesday"],
            teacher=["John", "Jane", "Bob"],
            assistants=[
                "A, B, C",
                "  ",
                "A,B,,C",
            ],  # whitespace / empty entries ignored
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "Partially Covered (3/5 assistants)" in html  # Monday: 3
        assert "Partially Covered (0/5 assistants)" in html  # Tuesday: 0
        # Wednesday: 3 non-empty entries despite the double comma

    def test_empty_assistants_count_as_zero(self):
        block = _block(
            max_assistants=2,
            days=["Monday", "Tuesday"],
            teacher=["John Doe", "Jane Smith"],
            assistants=["", "   "],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "Partially Covered (0/2 assistants)" in html

    def test_no_head_ta_column_when_absent(self):
        block = _block(
            has_head_ta=False,
            days=["Monday"],
            teacher=["John"],
            assistants=["A"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "Head Teaching Assistant" not in html
        assert "Head Assistant" not in html

    def test_head_ta_column_present_when_class_has_head_ta(self):
        block = _block(
            has_head_ta=True,
            days=["Monday", "Tuesday"],
            teacher=["Lydie", "Need Volunteers"],
            head_ta=["Thanh Thao", "Michael"],
            assistants=["Hai Chau", "Trang Vo"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "Head Assistant" in html
        assert "Thanh Thao" in html
        assert "Michael" in html

    def test_missing_teacher_status(self):
        block = _block(
            max_assistants=3,
            days=["Monday", "Tuesday"],
            teacher=["Need Volunteers", "John Doe"],
            assistants=["TA1, TA2", "TA1"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "❌ Missing Teacher" in html
        assert "Partially Covered (1/3 assistants)" in html

    def test_blank_teacher_defaults_to_missing_teacher(self):
        # A blank teacher cell defaults to "Need Volunteers". Genuinely-off days
        # are written explicitly as "No Class {reason}" and handled separately.
        block = _block(
            max_assistants=2,
            days=["Monday", "Tuesday"],
            teacher=["", "Trúc"],
            assistants=["", "Yến, Thomas"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert html.count("❌ Missing Teacher") == 1  # Monday only
        assert "Fully Covered (2/2 assistants)" in html  # Tuesday unaffected
        # A blank day must NOT be reported as covered/partially covered
        assert "Partially Covered (0/2 assistants)" not in html

    def test_missing_head_ta_status(self):
        block = _block(
            max_assistants=3,
            has_head_ta=True,
            days=["Monday", "Tuesday"],
            teacher=["John Doe", "Jane Smith"],
            head_ta=["", "Need Volunteers"],
            assistants=["TA1, TA2", "TA1"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert html.count("❌ Missing Head TA") == 2

    def test_missing_head_ta_status_never_shown_when_no_head_ta_row(self):
        # Classes without a Head TA row must never report a missing head TA.
        block = _block(
            max_assistants=3,
            has_head_ta=False,
            days=["Monday", "Tuesday"],
            teacher=["John Doe", "Jane Smith"],
            assistants=["TA1, TA2", "TA1"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "Missing Head TA" not in html

    def test_missing_assistants_status(self):
        block = _block(
            max_assistants=3,
            days=["Monday", "Tuesday"],
            teacher=["John Doe", "Jane Smith"],
            assistants=["Need Volunteers", "TA1"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "❌ Missing TA's" in html
        assert "Partially Covered (1/3 assistants)" in html

    def test_optional_day_status(self):
        block = _block(
            max_assistants=3,
            days=["Monday", "Tuesday"],
            teacher=["Optional Day", "John Doe"],
            assistants=["TA1", "TA1, TA2"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "optional day, volunteers welcome to support existing classes" in html
        assert "Partially Covered (2/3 assistants)" in html

    def test_no_class_status(self):
        block = _block(
            max_assistants=3,
            days=["Monday", "Tuesday"],
            teacher=["No Class", "No Class - Holiday"],
            assistants=["TA1", "TA1"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "No class" in html
        assert "No class: holiday" in html

    def test_no_limit_assistants(self):
        block = _block(
            max_assistants=None,
            days=["Monday", "Tuesday"],
            teacher=["John Doe", "Jane Smith"],
            assistants=["TA1, TA2, TA3", "TA1"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "3 assistant(s) signed up - No limit, TA's welcome to join" in html
        assert "1 assistant(s) signed up - No limit, TA's welcome to join" in html

    def test_status_priority_order(self):
        # Teacher missing takes priority over missing head TA / assistants.
        block = _block(
            max_assistants=3,
            has_head_ta=True,
            days=["Monday"],
            teacher=["Need Volunteers"],
            head_ta=["Need Volunteers"],
            assistants=["Need Volunteers"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "❌ Missing Teacher" in html
        assert "❌ Missing Head TA" not in html
        assert "❌ Missing TA's" not in html

    def test_case_insensitive_status_detection(self):
        block = _block(
            max_assistants=3,
            days=["Monday", "Tuesday", "Wednesday"],
            teacher=["NEED VOLUNTEERS", "optional day", "NO CLASS"],
            assistants=["TA1", "TA1", "TA1"],
        )
        html = EmailService().build_class_table(block)["table_html"]
        assert "❌ Missing Teacher" in html
        assert "optional day, volunteers welcome to support existing classes" in html
        assert "No class" in html

    def test_needs_volunteers_true_when_teacher_missing(self):
        block = _block(
            max_assistants=3,
            days=["Monday", "Tuesday"],
            teacher=["Need Volunteers", "John Doe"],
            assistants=["TA1, TA2", "TA1"],
        )
        result = EmailService().build_class_table(block)
        assert result["needs_volunteers"] is True

    def test_needs_volunteers_true_when_head_ta_missing(self):
        block = _block(
            max_assistants=3,
            has_head_ta=True,
            days=["Monday"],
            teacher=["John Doe"],
            head_ta=["Need Volunteers"],
            assistants=["TA1, TA2"],
        )
        result = EmailService().build_class_table(block)
        assert result["needs_volunteers"] is True

    def test_needs_volunteers_true_when_assistants_missing(self):
        block = _block(
            max_assistants=3,
            days=["Monday"],
            teacher=["John Doe"],
            assistants=["Need Volunteers"],
        )
        result = EmailService().build_class_table(block)
        assert result["needs_volunteers"] is True

    def test_needs_volunteers_false_when_fully_covered(self):
        block = _block(
            max_assistants=2,
            days=["Monday", "Tuesday"],
            teacher=["John Doe", "Jane Smith"],
            assistants=["TA1, TA2", "TA1, TA2"],
        )
        result = EmailService().build_class_table(block)
        assert result["needs_volunteers"] is False

    def test_needs_volunteers_false_for_optional_and_no_class_days(self):
        block = _block(
            max_assistants=3,
            days=["Monday", "Tuesday"],
            teacher=["Optional Day", "No Class - Holiday"],
            assistants=["TA1", ""],
        )
        result = EmailService().build_class_table(block)
        assert result["needs_volunteers"] is False
