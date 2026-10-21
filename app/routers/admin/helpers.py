"""
Shared helpers used across multiple admin sub-routers.
"""

import re
from datetime import UTC, datetime, timedelta

from app.models import Volunteer as VolunteerModel
from app.utils.logging_config import get_api_logger

logger = get_api_logger()


def parse_start_date(date_str):
    """Parse start date from form submission"""
    if not date_str or date_str.upper() == "ASAP":
        return datetime.now().date()
    # ISO is what the sheet reader produces from a date cell; DD/MM/YYYY is the
    # Vietnamese form a hand-typed text cell would use.
    for date_format in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, date_format).date()
        except ValueError:
            continue
    logger.warning(f"Invalid date format: {date_str}")
    return None


def get_volunteer_summary(volunteers, lapsed_ids: set[int] | None = None):
    """Extract volunteer data with confirmation status.

    ``lapsed_ids`` marks the volunteers the weekly reminder's recency window is
    excluding, so the dashboard can show it. The caller passes them in from
    select_lapsed_volunteers rather than this recomputing the cutoff, so there
    is only ever one definition of lapsed.
    """
    lapsed_ids = lapsed_ids or set()
    volunteer_data = []
    for volunteer in volunteers:
        confirmation_sent = any(
            comm.email_type == "volunteer_confirmation" and comm.status == "sent"
            for comm in volunteer.email_communications
        )
        last_confirmation = next(
            (
                comm
                for comm in volunteer.email_communications
                if comm.email_type == "volunteer_confirmation" and comm.status == "sent"
            ),
            None,
        )
        volunteer_data.append(
            {
                "id": volunteer.id,
                "name": volunteer.name,
                "email": volunteer.email,
                "is_active": volunteer.is_active,
                "weekly_reminders_subscribed": volunteer.weekly_reminders_subscribed,
                "all_emails_subscribed": volunteer.all_emails_subscribed,
                "confirmation_sent": confirmation_sent,
                "last_confirmation_date": last_confirmation.sent_at
                if last_confirmation
                else None,
                "positions": volunteer.positions or [],
                "created_at": volunteer.created_at,
                # Why a volunteer may have dropped off the weekly reminder:
                # None means the schedule-sheet name matcher never placed
                # them, so their sign-up date is used instead.
                "last_active_at": volunteer.last_active_at,
                "lapsed": volunteer.id in lapsed_ids,
            }
        )
    return volunteer_data


def get_email_summary(communications):
    """Extract email communication data"""
    email_data = []
    for comm in communications:
        email_data.append(
            {
                "id": comm.id,
                "volunteer_id": comm.volunteer_id,
                "volunteer_name": comm.volunteer.name,
                "recipient_email": comm.recipient_email,
                "email_type": comm.email_type,
                "subject": comm.subject,
                "status": comm.status,
                "dry_run": is_dry_run_email(comm),
                "sent_at": comm.sent_at,
                "delivered_at": comm.delivered_at,
            }
        )
    return email_data


def create_new_volunteer_object(submission: dict) -> VolunteerModel:
    """Create a new VolunteerModel from a form submission dict (no DB writes)."""
    first_name = submission.get("first_name", "").strip()
    last_name = submission.get("last_name", "").strip()
    full_name = f"{first_name} {last_name}".strip() or "Unknown Volunteer"

    return VolunteerModel(
        name=full_name,
        email=submission["email_address"],
        phone=submission["phone_number"],
        positions=[pos.strip() for pos in submission["position_interest"].split(",")]
        if submission.get("position_interest")
        else [],
        location=submission["location"],
        availability=[slot.strip() for slot in submission["availability"].split(",")]
        if submission.get("availability")
        else [],
        start_date=parse_start_date(submission["start_date"]),
        commitment_duration=submission["commitment_duration"],
        teaching_experience=submission["teaching_experience"],
        experience_details=submission["experience_details"],
        teaching_certificate=submission["teaching_certificate"],
        vietnamese_proficiency=submission["vietnamese_speaking"],
        additional_support=[
            support.strip() for support in submission["other_support"].split(",")
        ]
        if submission.get("other_support")
        else [],
        additional_info=f"Social Media: {submission.get('social_media_link', 'N/A')}\nReferral Source: {submission.get('referral_source', 'N/A')}",
        is_active=True,
    )


# A weekly job that missed a Sunday is more than 7 days stale; the extra day
# absorbs clock skew and a late run without crying wolf.
WEEKLY_REMINDER_STALE_AFTER_DAYS = 8

# PENDING rows are written before the batch is sent, so a run killed at the
# request deadline leaves them behind. No run can outlive Cloud Run's 300s
# request timeout, so anything PENDING this long was abandoned, not in flight.
ABANDONED_PENDING_REMINDER_AGE = timedelta(hours=6)


# The bulk run records the template it rendered; a per-volunteer manual send
# does not, which is what keeps one manual send from reading as a whole run.
WEEKLY_REMINDER_BULK_TEMPLATE = "weekly-reminder-email.html"
# A bulk run under DRY_RUN stamps this instead, on the row it writes, so the
# row never reads as a real run whatever its outcome or later DRY_RUN setting.
WEEKLY_REMINDER_DRY_RUN_TEMPLATE = "dry-run:weekly-reminder-email.html"


def is_dry_run_email(comm) -> bool:
    """Whether a row records a send made while DRY_RUN was on, whatever its outcome."""
    return (
        comm.status == "dry_run"
        or comm.template_name == WEEKLY_REMINDER_DRY_RUN_TEMPLATE
    )


# The reminder subject leads with a volatile open-slot count and ends with the
# week's date range. Only the range identifies the run, so it is read off the
# end rather than the whole subject being compared. A subject with no trailing
# "(...)" - every row written before the subject changed - keys on itself, so
# historic rows keep grouping exactly as they did.
_WEEK_KEY_RE = re.compile(r"\([^()]*\)\s*$")


def reminder_week_key_of(subject: str) -> str:
    """The run key carried by a reminder subject."""
    match = _WEEK_KEY_RE.search(subject or "")
    return match.group(0).strip() if match else (subject or "")


def get_weekly_reminder_run_health(db) -> dict:
    """Summarize the most recent bulk weekly-reminder run from its email rows.

    Cloud Scheduler's job status is otherwise the only place a failed Sunday
    shows, and it keeps just the last attempt - which is how the job stayed
    broken for four Sundays (2026-09-27..2026-10-18) without anyone noticing.

    A run that was legitimately skipped (no class needed volunteers) writes no
    rows, so it reads here as "stale" rather than "ok". That is deliberate: the
    captain still wants to know no reminder has gone out in over a week, and
    weeks_seen shows which weeks did send.

    Under DRY_RUN the bulk run reaches only the dry-run recipient and stamps
    its row with the dry-run template, which is never a run here; while
    DRY_RUN is on the verdict is "dry_run" whatever the rows say: no
    volunteer is being reminded.
    """
    from app.models import EmailCommunication as EmailCommunicationModel
    from app.utils.config_helper import ConfigHelper

    dry_run = ConfigHelper.get_dry_run(db)
    reminders = (
        db.query(EmailCommunicationModel)
        .filter(
            EmailCommunicationModel.email_type == "weekly_reminder",
            EmailCommunicationModel.template_name == WEEKLY_REMINDER_BULK_TEMPLATE,
        )
        .all()
    )
    if not reminders:
        health = {
            "status": "stale",
            "message": "No weekly reminder run has ever been recorded",
            "last_run": None,
            "days_since_last_run": None,
            "weeks_seen": 0,
            "stale_after_days": WEEKLY_REMINDER_STALE_AFTER_DAYS,
        }
        return _with_dry_run(health, dry_run)

    def row_time(row):
        return row.created_at or row.sent_at

    def row_order(row):
        return (row_time(row) or datetime.min, row.id)

    latest = max(reminders, key=row_order)
    # The subject's trailing date range identifies the run that produced a row,
    # without depending on when that row happened to be written - or on the
    # open-slot count the subject leads with, which moves between a run and its
    # resume and would otherwise split one run into two.
    latest_key = reminder_week_key_of(latest.subject)
    run_rows = [
        row for row in reminders if reminder_week_key_of(row.subject) == latest_key
    ]

    # A resumed run adds rows for the recipients an earlier attempt missed, so
    # judge each recipient rather than each row: delivered if any attempt was
    # sent, otherwise whatever their newest attempt says.
    by_recipient = {}
    for row in sorted(run_rows, key=row_order):
        if by_recipient.get(row.recipient_email) != "sent":
            by_recipient[row.recipient_email] = (row.status or "").lower()

    counts = {"sent": 0, "failed": 0, "pending": 0, "other": 0}
    for status in by_recipient.values():
        counts[status if status in counts else "other"] += 1

    run_times = [row_time(row) for row in run_rows if row_time(row)]
    last_run_at = max(run_times) if run_times else None
    days_since = (
        (datetime.now(UTC).replace(tzinfo=None) - _as_naive_utc(last_run_at)).days
        if last_run_at
        else None
    )

    unresolved = counts["failed"] + counts["pending"]
    if unresolved:
        status = "failed"
        message = (
            f"The last weekly reminder run ({latest.subject}) "
            f"sent {counts['sent']} and did not send {unresolved}"
        )
    elif days_since is not None and days_since > WEEKLY_REMINDER_STALE_AFTER_DAYS:
        status = "stale"
        message = f"No weekly reminder has been sent for {days_since} days"
    else:
        status = "ok"
        message = f"The last weekly reminder run sent {counts['sent']} emails"

    health = {
        "status": status,
        "message": message,
        "last_run": {
            "subject": latest.subject,
            "at": last_run_at.isoformat() if last_run_at else None,
            "recipients": len(by_recipient),
            **counts,
        },
        "days_since_last_run": days_since,
        "weeks_seen": len({reminder_week_key_of(row.subject) for row in reminders}),
        "stale_after_days": WEEKLY_REMINDER_STALE_AFTER_DAYS,
    }
    return _with_dry_run(health, dry_run)


def _with_dry_run(health: dict, dry_run: bool) -> dict:
    health["dry_run"] = dry_run
    if dry_run:
        health["status"] = "dry_run"
        health["message"] = (
            "DRY_RUN is on, so weekly reminders reach only the dry-run recipient"
        )
    return health


def _as_naive_utc(value: datetime) -> datetime:
    """Normalize a stored timestamp for comparison against UTC now.

    EmailCommunication writes created_at as aware UTC but the column is
    timezone-naive, so rows come back naive while some code paths set
    timestamps from a naive local clock.
    """
    return value.replace(tzinfo=None) if value.tzinfo else value
