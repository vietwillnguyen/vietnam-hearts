"""
Schedule sheet title parsing and formatting.

Single source of truth for the "Schedule <date>" tab naming scheme.
New sheets are named with DD/MM/YYYY; legacy MM/DD titles (no year) are
still parsed so rotation can match and migrate them instead of creating
duplicates. Non-date suffixes ("Schedule Template", "Schedule Config")
parse to None and are therefore excluded from rotation logic.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.utils.logging_config import get_logger

logger = get_logger("schedule_dates")

SCHEDULE_TITLE_PREFIX = "Schedule "

# Vietnam Hearts operates on Vietnam local time. Cloud Run sets no TZ, so the
# container clock is UTC; anchoring the display window to that would put the
# rotation a week behind between 00:00 and 07:00 Vietnam time.
DEFAULT_SCHEDULE_TIMEZONE = "Asia/Ho_Chi_Minh"

# Vietnam has observed UTC+7 with no DST since 1975, so a fixed offset is an
# exact stand-in for Asia/Ho_Chi_Minh. Used only when the IANA database itself
# cannot be loaded, which would otherwise make the fallback path raise the very
# error it exists to absorb.
_FIXED_DEFAULT_OFFSET = timezone(timedelta(hours=7), "UTC+07:00")

# The last weekday on which the current week still LEADS the display. Classes
# still run Monday to Friday; the display turns over a day earlier because
# rotation exists to let volunteers sign up for the coming week ahead of time,
# so on Friday the next week takes the leading tab and Friday's own classes are
# no longer led with. datetime.weekday() numbers Monday 0 ... Sunday 6.
_LAST_LEADING_WEEKDAY = 3  # Thursday
_DAYS_IN_WEEK = 7


def _default_timezone() -> timezone | ZoneInfo:
    """The default zone, degrading to a fixed offset if tzdata is missing."""
    try:
        return ZoneInfo(DEFAULT_SCHEDULE_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        logger.warning(
            "IANA timezone database unavailable, falling back to a fixed %s offset "
            "for %s",
            _FIXED_DEFAULT_OFFSET.tzname(None),
            DEFAULT_SCHEDULE_TIMEZONE,
        )
        return _FIXED_DEFAULT_OFFSET


def schedule_week_monday(now: datetime) -> datetime:
    """
    Midnight on the Monday of the schedule week ``now`` belongs to.

    The display turns over on Friday: from Friday 00:00 the week containing
    ``now`` no longer leads and the one that matters is the next, so Friday,
    Saturday and Sunday all roll the anchor forward to the coming Monday.
    Monday through Thursday resolve to their own Monday, which is the same
    value the roll forward produces, so the anchor moves exactly once a week
    - at Friday 00:00 - and does not move again when Monday arrives.

    ``now`` is read as a local wall clock; the caller owns the conversion.
    Returned naive so it composes with the naive datetimes produced by
    ``parse_schedule_sheet_title`` - comparing aware and naive datetimes
    raises.
    """
    monday = now - timedelta(days=now.weekday())
    if now.weekday() > _LAST_LEADING_WEEKDAY:
        monday += timedelta(days=_DAYS_IN_WEEK)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def current_week_monday(
    timezone_name: str = DEFAULT_SCHEDULE_TIMEZONE,
) -> datetime:
    """
    Midnight on the Monday of the current schedule week in ``timezone_name``.

    That is the Monday of the week containing "now" from Monday to Thursday,
    and the following Monday from Friday onwards - see
    ``schedule_week_monday`` for why. Evaluating "now" in the organization's
    zone rather than the container's matters twice over: Cloud Run sets no
    TZ, so a naive clock reads UTC, which is still on the previous day
    between 00:00 and 07:00 Vietnam time - including across the Friday
    turnover. An unknown or empty timezone falls back to the default rather
    than failing rotation outright, since a bad settings value should not
    take the schedule offline.
    """
    try:
        tz = ZoneInfo(timezone_name or DEFAULT_SCHEDULE_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        # Logged, not swallowed: a silently wrong anchor shifts the whole
        # display window by a week with no other outward symptom.
        logger.warning(
            "Unusable schedule timezone %r, falling back to %s",
            timezone_name,
            DEFAULT_SCHEDULE_TIMEZONE,
        )
        tz = _default_timezone()

    return schedule_week_monday(datetime.now(tz))


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
