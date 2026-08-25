"""
Schedule week semantics: which Monday the displayed week starts on, which
weekdays classes run on, and the "Schedule <date>" tab naming scheme.

Single source of truth for all three, so the week anchor, the teaching-day
check and the title format cannot drift apart across their callers.

New sheets are named with DD/MM/YYYY; legacy MM/DD titles (no year) are
still parsed so rotation can match and migrate them instead of creating
duplicates. Non-date suffixes ("Schedule Template", "Schedule Config")
parse to None and are therefore excluded from rotation logic.
"""

import re
from collections.abc import Iterable
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

# Vietnam Hearts teaches on Tuesday and Thursday only. The schedule grid still
# carries a column for every weekday, and days with no class are left blank
# rather than written out as "No Class", so a blank teacher cell on a Monday is
# not an unfilled teaching slot - it is a day the organization does not teach.
# Overridable per deployment (the SCHEDULE_TEACHING_DAYS setting) because a
# timetable is exactly the kind of thing that changes.
DEFAULT_TEACHING_DAYS = ("Tuesday", "Thursday")

# The same default in the form the SCHEDULE_TEACHING_DAYS setting stores, so the
# seeded value and the code default can never drift apart.
DEFAULT_TEACHING_DAYS_SETTING = ", ".join(DEFAULT_TEACHING_DAYS)

# Sheet day labels vary in form and usually carry a date - "Tue", "Monday 6/22".
# Only the leading weekday token identifies the day.
_WEEKDAY_TOKEN_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)", re.IGNORECASE)


def weekday_tokens(label: str) -> list[str]:
    """Every lowercase three-letter weekday token in ``label``, in order."""
    if not label:
        return []
    return [match.lower() for match in _WEEKDAY_TOKEN_RE.findall(str(label))]


def weekday_token(label: str) -> str | None:
    """The first lowercase three-letter weekday token in ``label``, or None."""
    tokens = weekday_tokens(label)
    return tokens[0] if tokens else None


def parse_teaching_days(raw: str | Iterable[str] | None) -> frozenset[str]:
    """
    Weekday tokens for the days classes actually run on.

    Accepts either a separated string as stored in settings ("Tuesday,
    Thursday") or an iterable of day names. Every weekday an entry names
    counts, not just its first: "Tuesday and Thursday" is as plausible a way
    to fill the setting in as "Tuesday, Thursday", and keeping only the first
    would silently stop the reminder reporting the dropped day's unfilled
    slots while leaving the set non-empty, so the guard below never fires.
    Entries naming no weekday are dropped; a value naming none at all falls
    back to the default, because an empty teaching week would mark every day
    as non-teaching and so suppress the weekly reminder entirely.
    """
    parts = re.split(r"[,;/|]+", raw) if isinstance(raw, str) else list(raw or ())
    tokens = frozenset(t for part in parts for t in weekday_tokens(part))
    if tokens:
        return tokens
    if isinstance(raw, str) and raw.strip():
        logger.warning(
            "Teaching days %r name no weekday, falling back to %s",
            raw,
            ", ".join(DEFAULT_TEACHING_DAYS),
        )
    return frozenset(weekday_token(day) for day in DEFAULT_TEACHING_DAYS)


def is_teaching_day(day_label: str, teaching_days: Iterable[str] | None = None) -> bool:
    """
    True if ``day_label`` names one of the days classes run on.

    A label naming no recognizable weekday counts as a teaching day: failing
    open keeps a genuinely unfilled slot visible in the reminder, where failing
    closed would silently drop it.
    """
    token = weekday_token(day_label)
    if token is None:
        return True
    return token in parse_teaching_days(teaching_days)


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
