"""
Schedule sheet title parsing and formatting.

Single source of truth for the "Schedule <date>" tab naming scheme.
New sheets are named with DD/MM/YYYY; legacy MM/DD titles (no year) are
still parsed so rotation can match and migrate them instead of creating
duplicates. Non-date suffixes ("Schedule Template", "Schedule Config")
parse to None and are therefore excluded from rotation logic.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.utils.logging_config import get_logger

logger = get_logger("schedule_dates")

SCHEDULE_TITLE_PREFIX = "Schedule "

# Vietnam Hearts operates on Vietnam local time. Cloud Run sets no TZ, so the
# container clock is UTC; anchoring the display window to that would put the
# rotation a week behind between 00:00 and 07:00 Vietnam time.
DEFAULT_SCHEDULE_TIMEZONE = "Asia/Ho_Chi_Minh"


def current_week_monday(
    timezone_name: str = DEFAULT_SCHEDULE_TIMEZONE,
) -> datetime:
    """
    Midnight on the Monday of the week containing "now" in ``timezone_name``.

    Returned naive so it composes with the naive datetimes produced by
    ``parse_schedule_sheet_title`` - comparing aware and naive datetimes
    raises. An unknown or empty timezone falls back to the default rather
    than failing rotation outright, since a bad settings value should not
    take the schedule offline.
    """
    try:
        tz = ZoneInfo(timezone_name or DEFAULT_SCHEDULE_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        # Logged, not swallowed: a silently wrong anchor shifts the whole
        # display window by a week with no other outward symptom.
        logger.warning(
            "Unusable schedule timezone %r, falling back to %s",
            timezone_name,
            DEFAULT_SCHEDULE_TIMEZONE,
        )
        tz = ZoneInfo(DEFAULT_SCHEDULE_TIMEZONE)

    now = datetime.now(tz)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def format_schedule_sheet_title(date: datetime) -> str:
    """Format the canonical sheet title for a schedule week: Schedule DD/MM/YYYY."""
    return f"{SCHEDULE_TITLE_PREFIX}{date.strftime('%d/%m/%Y')}"


def parse_schedule_sheet_title(
    title: str, default_year: int | None = None
) -> datetime | None:
    """
    Parse a schedule sheet title into a datetime.

    Accepts the canonical "Schedule DD/MM/YYYY" format and the legacy
    "Schedule MM/DD" format (year assumed to be ``default_year`` or the
    current year). Returns None for any title that is not a dated
    schedule sheet.
    """
    if not title or not title.startswith(SCHEDULE_TITLE_PREFIX):
        return None
    date_part = title[len(SCHEDULE_TITLE_PREFIX) :].strip()
    if not date_part:
        return None

    try:
        return datetime.strptime(date_part, "%d/%m/%Y")
    except ValueError:
        pass

    try:
        parsed = datetime.strptime(date_part, "%m/%d")
    except ValueError:
        return None
    return parsed.replace(year=default_year or datetime.now().year)
