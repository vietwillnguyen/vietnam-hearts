"""Volunteer activity attribution and weekly-reminder recipient selection.

The sign-up form has no opt-in question, so every accepted volunteer is
auto-subscribed to the weekly reminder and stays subscribed until they
unsubscribe themselves. Volunteers who try one term and move on therefore keep
receiving a weekly email forever, which is both a nuisance and the fastest way
to train a whole mailing list to mark the sender as spam.

There is no attendance field anywhere in the app, so the only record of who
still turns up is the schedule spreadsheet - and it holds a free-text name a
volunteer typed into a cell, never an email address. Attribution is therefore a
name-matching problem, and the live sheet makes it a hard one: the same people
appear as "Truc Quynh Dang", "Mỹ Hạnh Trần", "Jasmine (Quỳnh Như)", "Nico" and
"Anthony T".

Two rules follow from that, and every function here obeys them:

1. **Ambiguity keeps people subscribed.** A name that could be either of two
   volunteers credits both. Guessing wrong would silently unsubscribe a real
   volunteer, which is far worse than one extra reminder.
2. **An unplaceable name is reported, never dropped silently.** Scans return
   the cells they could not match so the captain can fix the volunteer record
   or ask the volunteer to use their full name, rather than discovering months
   later that someone stopped being emailed.

A volunteer who has never been matched keeps their sign-up date as the
recency floor (see :func:`select_weekly_reminder_recipients`), so a newly
accepted volunteer who has not had a chance to sign up yet is never excluded.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Volunteer as VolunteerModel
from app.services.schedule_parser import ClassBlock
from app.utils.config_helper import ConfigHelper
from app.utils.logging_config import get_logger

logger = get_logger(__name__)

# Separators volunteers use between two names in one cell. "/" is included
# because the sheet's own "POC" cells are written that way and the habit leaks
# into the role rows.
_NAME_SEPARATORS = re.compile(r"[,;/\n]+")

# A parenthetical in a role cell is an alias, not an annotation: "Jasmine
# (Quỳnh Như)" is one person under two names, and the volunteer record could
# have been created under either.
_PARENTHETICAL = re.compile(r"\(([^)]*)\)")

# Everything that is not a letter, a digit or a space, once diacritics have
# been decomposed away. Keeping digits matters for names like "Anthony T 2".
_NON_NAME_CHARS = re.compile(r"[^0-9a-z ]+")

# Cell values that stand for an empty slot rather than a person. Each wording
# opens the placeholder cells the sheet uses, so they are matched as a prefix
# of the normalized value: "No class: holiday" and "NEED VOLUNTEERS!" are
# caught, while "Juno Classen", which merely contains "no class", is a name.
# The list stays narrow on purpose: a wording that catches a real name would
# silently stop crediting that volunteer, whereas a placeholder it misses is
# only reported as an unmatched name.
_PLACEHOLDER_FRAGMENTS = (
    "need volunteer",
    "no class",
    "holiday",
    "optional",
)

# Placeholders too short to search for inside a cell. "n a" is what "N/A"
# normalizes to, and it is also the middle of "Tuan Anh" and "Lan Anh", so
# these only count when they are the whole normalized value.
_PLACEHOLDER_VALUES = frozenset({"tba", "tbd", "n a"})

# A single character is never enough to identify anyone, and splitting a cell
# can leave stray initials behind.
_MIN_NAME_LENGTH = 2


@dataclass(frozen=True)
class ActivityScanResult:
    """What one week's scan did: volunteers stamped, and cells left unplaced."""

    matched: int
    unmatched: list[str]


def normalize_name(name: str) -> str:
    """Fold a typed name down to the form two spellings of it share.

    Diacritics are stripped because the same volunteer is written "Mỹ Hạnh
    Trần" one week and "My Hanh Tran" the next, and punctuation goes because
    "Anthony T." and "Anthony T" are one person.
    """
    decomposed = unicodedata.normalize("NFD", name or "")
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    folded = _NON_NAME_CHARS.sub(" ", without_marks.lower())
    return " ".join(folded.split())


def _is_placeholder(normalized: str) -> bool:
    return normalized in _PLACEHOLDER_VALUES or normalized.startswith(
        _PLACEHOLDER_FRAGMENTS
    )


def signup_name_candidates(cell: str) -> list[str]:
    """Split one role cell into the names it actually claims.

    A cell may hold nothing, a placeholder, one name, several names, or a
    nickname with the volunteer's Vietnamese name in parentheses. Both halves
    of that last case are returned, because either could be the name the
    volunteer record carries.

    The whole cell is tested for placeholders before it is split, so "N/A"
    is recognised rather than becoming the two initials "N" and "A".
    """
    if not cell or not cell.strip():
        return []
    if _is_placeholder(normalize_name(cell)):
        return []

    names: list[str] = []
    for part in _NAME_SEPARATORS.split(cell):
        part = part.strip()
        if not part:
            continue
        aliases = [alias.strip() for alias in _PARENTHETICAL.findall(part)]
        outside = _PARENTHETICAL.sub(" ", part).strip()
        for candidate in [outside, *aliases]:
            normalized = normalize_name(candidate)
            if len(normalized) < _MIN_NAME_LENGTH or _is_placeholder(normalized):
                continue
            if candidate not in names:
                names.append(candidate)
    return names


def extract_signup_names(blocks: Iterable[ClassBlock]) -> list[str]:
    """Every name signed up to a role in the given class blocks, in sheet order.

    Only the teacher, head-assistant and assistant rows are read. That is the
    whole point of going through the parser rather than the raw grid: the live
    sheet also holds names in separator rows and announcement banners, and
    those are not signups for any slot.
    """
    names: list[str] = []
    for block in blocks:
        for cell in (*block.teacher, *block.head_ta, *block.assistants):
            for candidate in signup_name_candidates(cell):
                if candidate not in names:
                    names.append(candidate)
    return names


def _name_words(normalized: str) -> list[str]:
    return normalized.split()


def _is_same_person(
    signup_words: Sequence[str], volunteer_words: Sequence[str]
) -> bool:
    """Whether a typed name and a volunteer's recorded name are the same person.

    Three ways, any of which is enough:

    - the normalized names are identical;
    - they hold the same words in a different order, which is how a Vietnamese
      name written family-name-first ("Trần Mỹ Hạnh") matches the same name
      written the other way round;
    - the typed name is a shortening of the recorded one *and opens with the
      same word* ("Nico" for "Nico Nguyen", "Ha My" for "Ha My Nguyen").

    That last anchor is what stops a family name from matching everyone who
    shares it: "Tran" must not credit "Mỹ Hạnh Trần", because a cell holding
    one family name tells us nothing about which Tran signed up.
    """
    if not signup_words or not volunteer_words:
        return False
    if signup_words == volunteer_words:
        return True
    if sorted(signup_words) == sorted(volunteer_words):
        return True
    return signup_words[0] == volunteer_words[0] and set(signup_words).issubset(
        volunteer_words
    )


def _volunteer_name_words(db: Session) -> list[tuple[int, list[str]]]:
    """Every volunteer's id with their normalized name words, read once per scan."""
    return [
        (volunteer.id, _name_words(normalize_name(volunteer.name)))
        for volunteer in db.query(VolunteerModel).all()
    ]


def _match_name_words(
    name: str, volunteers: Sequence[tuple[int, Sequence[str]]]
) -> list[int]:
    signup_words = _name_words(normalize_name(name))
    if not signup_words:
        return []
    return sorted(
        volunteer_id
        for volunteer_id, volunteer_words in volunteers
        if _is_same_person(signup_words, volunteer_words)
    )


def match_signup_name(db: Session, name: str) -> list[int]:
    """Volunteer ids the typed ``name`` could belong to, as a sorted list.

    Returns every candidate rather than picking one: see the ambiguity rule in
    this module's docstring. Inactive volunteers are included, so someone the
    sunset policy already deactivated is recognised the moment they appear on
    the sheet again.
    """
    return _match_name_words(name, _volunteer_name_words(db))


def record_week_activity(
    db: Session,
    names: Iterable[str],
    week_monday: datetime,
    dry_run: bool = False,
) -> ActivityScanResult:
    """Credit ``week_monday`` to every volunteer named in that week's grid.

    ``last_active_at`` only ever moves forward, so scanning the weeks in any
    order - or scanning the same week twice - gives the same answer.

    A future week counts as activity, and deliberately so: signing up for next
    week is the most engaged thing a volunteer does, and the schedule keeps
    several weeks visible for exactly that.
    """
    stamped: set[int] = set()
    unmatched: list[str] = []
    volunteer_names = _volunteer_name_words(db)

    for name in names:
        volunteer_ids = _match_name_words(name, volunteer_names)
        if not volunteer_ids:
            unmatched.append(name)
            continue
        stamped.update(volunteer_ids)

    if not dry_run and stamped:
        volunteers = (
            db.query(VolunteerModel).filter(VolunteerModel.id.in_(stamped)).all()
        )
        for volunteer in volunteers:
            if (
                volunteer.last_active_at is None
                or volunteer.last_active_at < week_monday
            ):
                volunteer.last_active_at = week_monday
        db.commit()

    if unmatched:
        logger.warning(
            # Names stay out of the log; the endpoint response lists them for
            # the admin who has to fix the records.
            "Schedule week %s: %d signup name(s) matched no volunteer record",
            week_monday.date().isoformat(),
            len(unmatched),
        )

    return ActivityScanResult(matched=len(stamped), unmatched=sorted(unmatched))


def _sheets():
    """The Sheets service singleton, resolved late.

    google_sheets pulls in the whole Google client stack at import time, and
    the name-matching half of this module is useful (and tested) without it.
    """
    from app.services.google_sheets import sheets_service

    return sheets_service


def record_activity_from_schedule_sheets(
    db: Session,
    include_hidden: bool = False,
    dry_run: bool = False,
) -> list[tuple[str, ActivityScanResult]]:
    """Scan schedule tabs and credit each volunteer with the weeks they appear in.

    Only *dated* tabs are read. Every helper here matches tabs by the
    "Schedule " prefix, which also catches "Schedule Template" - and the
    template is visible whenever a volunteer is editing it, so its "Need
    Volunteers" placeholder rows would otherwise be scanned as a week.

    Hidden tabs are skipped by default: rotation hides past weeks, those weeks
    never change again, and re-reading the whole archive every two hours would
    spend a Sheets read per tab for no new information. The one-off backfill
    (``scripts/backfill_volunteer_activity.py``) passes ``include_hidden`` to
    pick up the history.

    Each range is qualified with its tab title. Unqualified, the API reads the
    first sheet in the spreadsheet, which rotation pins to the template.

    Returns one ``(tab title, ActivityScanResult)`` pair per tab scanned. A
    Sheets failure propagates rather than being swallowed: a scan that quietly
    does nothing leaves ``last_active_at`` to go stale, and stale recency
    unsubscribes real volunteers.
    """
    from app.services.google_sheets import SCHEDULE_CLASS_AREA_RANGE
    from app.services.schedule_parser import discover_schedule_blocks
    from app.utils.schedule_dates import parse_schedule_sheet_title
    from app.utils.sheet_utils import a1_range

    sheets = _sheets()
    results: list[tuple[str, ActivityScanResult]] = []

    for sheet in sheets.get_schedule_sheets(db):
        properties = sheet["properties"]
        title = properties["title"]
        week_monday = parse_schedule_sheet_title(title)
        if week_monday is None:
            continue
        if properties.get("hidden", False) and not include_hidden:
            continue

        rows = sheets.get_schedule_range(db, a1_range(title, SCHEDULE_CLASS_AREA_RANGE))
        names = extract_signup_names(discover_schedule_blocks(rows))
        results.append(
            (title, record_week_activity(db, names, week_monday, dry_run=dry_run))
        )

    logger.info(
        "Scanned %d schedule tab(s) for volunteer activity (include_hidden=%s, dry_run=%s)",
        len(results),
        include_hidden,
        dry_run,
    )
    return results


def select_lapsed_volunteers(db: Session, now: datetime | None = None) -> dict:
    """Subscribed volunteers the recency window is now excluding.

    This is a report, not an action. The captain's ruling on 2026-10-22 was to
    flag lapsed volunteers for review and never deactivate them automatically,
    because attribution runs on name matching: of one live week's 21 signup
    names, 6 were nickname-only and 4 of those had no alias to fall back on, so
    an automatic sunset would retire volunteers who are still turning up every
    week. Nothing here writes to a volunteer.

    ``ever_matched_on_schedule`` is the distinction worth acting on. False
    means the matcher has never placed this volunteer on the sheet at all, so
    they are being measured from their sign-up date and the right fix is
    usually their name record - not a conversation about whether they have
    stopped volunteering.

    Volunteers who unsubscribed themselves are left out: they are not being
    dropped by this policy, and they are not the captain's to chase.
    """
    reference = now or datetime.now()
    active_weeks = ConfigHelper.get_reminder_active_weeks(db)
    cutoff = reference - timedelta(weeks=active_weeks)

    if active_weeks <= 0:
        return {"active_weeks": 0, "cutoff": None, "volunteers": []}

    lapsed = (
        db.query(VolunteerModel)
        .filter(VolunteerModel.is_active.is_(True))
        .filter(VolunteerModel.weekly_reminders_subscribed.is_(True))
        .filter(
            func.coalesce(VolunteerModel.last_active_at, VolunteerModel.created_at)
            < cutoff
        )
        .order_by(VolunteerModel.name)
        .all()
    )

    def weeks_since(volunteer: VolunteerModel) -> int | None:
        measured_from = volunteer.last_active_at or volunteer.created_at
        if measured_from is None:
            return None
        return (reference - measured_from).days // 7

    return {
        "active_weeks": active_weeks,
        "cutoff": cutoff.date().isoformat(),
        "volunteers": [
            {
                "id": volunteer.id,
                "name": volunteer.name,
                "email": volunteer.email,
                "last_active_at": volunteer.last_active_at.isoformat()
                if volunteer.last_active_at
                else None,
                "created_at": volunteer.created_at.isoformat()
                if volunteer.created_at
                else None,
                "ever_matched_on_schedule": volunteer.last_active_at is not None,
                "weeks_since_active": weeks_since(volunteer),
            }
            for volunteer in lapsed
        ],
    }


def select_weekly_reminder_recipients(
    db: Session, now: datetime | None = None
) -> list[VolunteerModel]:
    """The volunteers this week's reminder should go to.

    On top of the subscription flags the reminder has always honoured, a
    volunteer must have been active within ``REMINDER_ACTIVE_WEEKS``. Recency
    falls back to the sign-up date when the volunteer has never been matched on
    the schedule, which covers both a volunteer accepted days ago and one whose
    name the matcher cannot place - neither should be dropped for a data
    problem they cannot see.

    Setting ``REMINDER_ACTIVE_WEEKS`` to 0 turns the recency filter off and
    restores the previous behaviour of emailing every subscribed volunteer.
    """
    query = (
        db.query(VolunteerModel)
        .filter(VolunteerModel.is_active.is_(True))
        .filter(VolunteerModel.weekly_reminders_subscribed.is_(True))
    )

    active_weeks = ConfigHelper.get_reminder_active_weeks(db)
    if active_weeks <= 0:
        logger.info("REMINDER_ACTIVE_WEEKS is 0, sending to every subscribed volunteer")
        return query.order_by(VolunteerModel.name).all()

    reference = now or datetime.now()
    cutoff = reference - timedelta(weeks=active_weeks)
    recipients = (
        query.filter(
            func.coalesce(VolunteerModel.last_active_at, VolunteerModel.created_at)
            >= cutoff
        )
        .order_by(VolunteerModel.name)
        .all()
    )

    subscribed = query.count()
    logger.info(
        "Weekly reminder recipients: %d of %d subscribed volunteers active since %s "
        "(REMINDER_ACTIVE_WEEKS=%d)",
        len(recipients),
        subscribed,
        cutoff.date().isoformat(),
        active_weeks,
    )
    return recipients
