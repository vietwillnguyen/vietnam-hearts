"""
Grids shaped like the live schedule spreadsheet, as the Sheets API returns them.

Both grids are read from column A, so index 0 is column A, index 1 (column B)
holds the row labels, and C:G are the five weekdays. Rows the API finds empty
come back as ``[]`` and trailing empty cells are trimmed, which is why the rows
here are ragged - a fixture of neatly padded rows would not exercise the same
code paths as the sheet does.

``TEMPLATE_GRID`` mirrors "Schedule Template" after the captain added Grade 1A
on 2026-10-15: four class blocks, Grade 1A first, each five rows followed by a
blank separator. The day headers carry the template's own placeholder week and
its month-first rendering, which rotation overwrites.

``CURRENT_WEEK_GRID`` mirrors the live "Schedule 12/10/2026" tab before the
backfill, with the quirks that tab really has: three class blocks starting two
rows lower than the template's, a stray value in A1, and a volunteer's name
parked in the separator row between two blocks. Volunteer names are stand-ins
except for the ones the other schedule tests already use.
"""

HOW_TO_USE = (
    "How To Use:  \n"
    "1. PLEASE USE YOUR FULL NAME below to sign in for a slot.\n"
    "2. Respect the TA limits on each class, signups get first preference\n"
    "Announcements:  "
)

RESOURCES = "TV/AV, HDMI Port + Wire \nWifi, Board + Chalk"

# The template's placeholder week: Monday 1 June to Friday 5 June, rendered by
# the sheet's vi_VN locale through the template's own month-first format.
TEMPLATE_DAY_HEADERS = [
    "Thứ Hai 6/1",
    "Thứ Ba 6/2",
    "Thứ Tư 6/3",
    "Thứ Năm 6/4",
    "Thứ Sáu 6/5",
]


def _template_block(title, poc, size, age, proficiency):
    """One unfilled class block as the template holds it, plus its separator row."""
    return [
        ["", title, *TEMPLATE_DAY_HEADERS, "", f"Classroom Info\nPOC: {poc}"],
        [
            "",
            "Teacher",
            "",
            "Need Volunteers",
            "",
            "Need Volunteers",
            "",
            "",
            "Size",
            size,
        ],
        [
            "",
            "Head Assistant (min 1), must handle attendance and supply box",
            "",
            "Need Volunteers",
            "",
            "Need Volunteers",
            "",
            "",
            "In Class Resources",
            RESOURCES,
        ],
        [
            "",
            "Assistants MAX 4",
            "",
            "Need Volunteers",
            "",
            "Need Volunteers",
            "",
            "",
            "Age",
            age,
        ],
        [
            "",
            "Curriculum & Lesson Plan",
            "",
            "[link curriculum]",
            "",
            "[link curriculum]",
            "",
            "",
            "English Proficiency",
            proficiency,
        ],
        [],
    ]


# Grade 1A is the first class whose title names a time per day: it meets
# Tuesday morning and Thursday afternoon.
GRADE_1A_TITLE = "Grade 1A\nTUESDAY 9:30 - 10:30 AM\nTHURSDAY 3:30 - 4:30 PM"

TEMPLATE_GRID = [
    ["", "Schedule for Week 06/01"],
    [],
    [
        "",
        "Contact Us on FB",
        "Volunteer Sign Up Form",
        "",
        "Curriculum:",
        "please contact POC for each class",
        "",
        "Google Drive",
    ],
    [],
    ["", HOW_TO_USE],
    [],
    *_template_block(
        GRADE_1A_TITLE, "Thomas", "~ 30 students", "6-10 Years Old", "Pre-A1"
    ),
    *_template_block(
        "Grade 1B (Special Needs)\n9:30 - 10:30 AM",
        "Trúc Quỳnh / Mỹ Hạnh",
        "~ 12 students",
        "6-10 Years Old",
        "Pre-A1",
    ),
    *_template_block(
        "Grade 2\n9:30 - 10:30 AM",
        "Jasmine / Lydie",
        "36 students",
        "6-10 Years Old",
        "A1 Starters",
    ),
    *_template_block(
        "Grade 5\n9:30 - 10:30 AM",
        "Annie / Thomas",
        "36 students",
        "10-18 Years Old",
        "Struggling A1",
    ),
]
# The template's trailing separator row is an artefact of building the grid
# block by block; the sheet itself ends on the last curriculum row.
TEMPLATE_GRID = TEMPLATE_GRID[:-1]

# Monday 12 to Friday 16 October 2026, as that tab's day headers render.
WEEK_12_10_DAY_HEADERS = [
    "Thứ Hai 12/10",
    "Thứ Ba 13/10",
    "Thứ Tư 14/10",
    "Thứ Năm 15/10",
    "Thứ Sáu 16/10",
]


def _signed_up_block(title, poc, size, age, proficiency, teacher, head_ta, assistants):
    """One class block with signups in the Tuesday and Thursday columns."""
    return [
        ["", title, *WEEK_12_10_DAY_HEADERS, "", f"Classroom Info\nPOC: {poc}"],
        ["", "Teacher", "", teacher[0], "", teacher[1], "", "", "Size", size],
        [
            "",
            "Head Assistant (min 1), must handle attendance and supply box",
            "",
            head_ta[0],
            "",
            head_ta[1],
            "",
            "",
            "In Class Resources",
            RESOURCES,
        ],
        [
            "",
            "Assistants MAX 4",
            "",
            assistants[0],
            "",
            assistants[1],
            "",
            "",
            "Age",
            age,
        ],
        [
            "",
            "Curriculum & Lesson Plan",
            "",
            "[link curriculum]",
            "",
            "[link curriculum]",
            "",
            "",
            "English Proficiency",
            proficiency,
        ],
    ]


CURRENT_WEEK_GRID = [
    # A stray value a volunteer left in A1; the title banner is B1.
    ["I", "Schedule for Week 12/10/2026"],
    [],
    [
        "",
        "Contact Us on FB",
        "Volunteer Sign Up Form",
        "",
        "Curriculum:",
        "Grade 2",
        "Grade 5",
        "Google Drive",
    ],
    [],
    ["", HOW_TO_USE],
    [],
    [],
    [],
    # This tab calls the class "Grade 1B"; 19/10 calls the same class
    # "Grade 1" and 26/10 "Grade 1B (Special Needs)".
    *_signed_up_block(
        "Grade 1B\n9:30 - 10:30 AM",
        "Trúc Quỳnh / Mỹ Hạnh",
        "~ 12 students",
        "6-10 Years Old",
        "Pre-A1",
        teacher=("Truc Quynh Dang", "Mỹ Hạnh Trần"),
        head_ta=("Nico", "Nico"),
        assistants=("Tran Bui, Joe Tran", "Tran Bui, John Pham"),
    ),
    [],
    *_signed_up_block(
        "Grade 2\n9:30 - 10:30 AM",
        "Jasmine / Lydie",
        "36 students",
        "6-10 Years Old",
        "A1 Starters",
        teacher=("Lydie Phuong Ly", "Jasmine (Quỳnh Như) "),
        head_ta=("", "Need Volunteers"),
        assistants=("Ha My", "Ha My, Joe Tran, Laure, John Le"),
    ),
    # A name typed into the separator row below Grade 2's block.
    ["", "", "", "", "", "Toan Tran"],
    *_signed_up_block(
        "Grade 5\n9:30 - 10:30 AM",
        "Annie / Thomas",
        "36 students",
        "10-18 Years Old",
        "Struggling A1",
        teacher=("Annie (Thu Hằng) ", "Thomas Jacobs"),
        head_ta=("Thomas Jacobs", "Nguyen Thuy trắng"),
        assistants=("Mary Tran, Cao Ngan", "Michael, Anthony T, Yer"),
    ),
]
