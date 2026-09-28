"""
Settings service for managing dynamic configuration values

This service provides functions to get and set configuration settings
that are stored in the database rather than environment variables.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Setting
from app.utils.schedule_dates import (
    DEFAULT_SCHEDULE_TIMEZONE,
    DEFAULT_TEACHING_DAYS_SETTING,
)


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    """
    Get a setting value from the database

    Args:
        db: Database session
        key: Setting key to retrieve
        default: Default value if setting doesn't exist

    Returns:
        The setting value or default if not found
    """
    if db is None:
        return default
    setting = db.query(Setting).filter(Setting.key == key).first()
    return setting.value if setting else default


def set_setting(
    db: Session, key: str, value: str, description: str | None = None
) -> Setting:
    """
    Set a setting value in the database

    Args:
        db: Database session
        key: Setting key to set
        value: Value to set
        description: Optional description of the setting

    Returns:
        The Setting object that was created or updated
    """
    setting = db.query(Setting).filter(Setting.key == key).first()

    if setting:
        setting.value = value
        setting.updated_at = datetime.now(UTC)
        if description:
            setting.description = description
    else:
        setting = Setting(key=key, value=value, description=description)
        db.add(setting)

    db.commit()
    db.refresh(setting)
    return setting


def delete_setting(db: Session, key: str) -> bool:
    """
    Delete a setting from the database

    Args:
        db: Database session
        key: Setting key to delete

    Returns:
        True if setting was deleted, False if it didn't exist
    """
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting:
        db.delete(setting)
        db.commit()
        return True
    return False


def get_all_settings(db: Session) -> list[Setting]:
    """
    Get all settings from the database

    Args:
        db: Database session

    Returns:
        List of all Setting objects
    """
    return db.query(Setting).all()


def get_settings_dict(db: Session) -> dict[str, str]:
    """
    Get all settings as a dictionary

    Args:
        db: Database session

    Returns:
        Dictionary mapping setting keys to values
    """
    settings = db.query(Setting).all()
    return {setting.key: setting.value for setting in settings}


def initialize_default_settings(db: Session) -> None:
    """
    Initialize default settings in the database if they don't exist

    This should be called during application startup to ensure
    all required settings have default values.
    """
    default_settings = {
        "DRY_RUN": {
            "value": "false",
            "description": "If true, the system will only send emails to the dry run email recipient",
        },
        "DRY_RUN_EMAIL_RECIPIENT": {
            "value": "",
            "description": "Email address to send dry run emails to",
        },
        "WEEKLY_REMINDERS_ENABLED": {
            "value": "true",
            "description": "If false, weekly reminder emails will be disabled globally",
        },
        "INVITE_LINK_ZALO": {
            "value": "https://zalo.me/g/gcmgkowx6gvotsghvsji",
            "description": "Zalo group chat invite link",
        },
        "ONBOARDING_GUIDE_LINK": {
            "value": "",
            "description": "Link to the onboarding guide for new volunteers",
        },
        "INSTAGRAM_LINK": {"value": "", "description": "Link to Instagram profile"},
        "FACEBOOK_PAGE_LINK": {"value": "", "description": "Link to Facebook page"},
        "SCHEDULE_SIGNUP_LINK": {
            "value": "",
            "description": "Google Sheets URL for the schedule spreadsheet. You can paste the full URL (e.g. https://docs.google.com/spreadsheets/d/1234567890/edit) or just the sheet ID (1234567890)",
        },
        "NEW_SIGNUPS_RESPONSES_LINK": {
            "value": "",
            "description": "Google Sheets URL for new volunteer signups. You can paste the full URL (e.g. https://docs.google.com/spreadsheets/d/1234567890/edit) or just the sheet ID (1234567890)",
        },
        "SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT": {
            "value": "4",
            "description": "How many weeks of schedule sheets stay visible, counting from the current week",
        },
        "SCHEDULE_TIMEZONE": {
            "value": DEFAULT_SCHEDULE_TIMEZONE,
            "description": (
                "IANA timezone the schedule week is anchored to, e.g. Asia/Ho_Chi_Minh. "
                "Cloud Run containers run on UTC, so without this the display window "
                "would shift a week early between midnight and 7am Vietnam time. Also "
                "used as the timezone of the Cloud Scheduler cron jobs below."
            ),
        },
        "SCHEDULE_TEACHING_DAYS": {
            "value": DEFAULT_TEACHING_DAYS_SETTING,
            "description": (
                "Comma-separated weekdays classes actually run on, e.g. "
                "Tuesday, Thursday. The schedule sheet keeps a column for every "
                "weekday and leaves the days with no class blank, so without "
                "this the weekly reminder reports every one of them to "
                "volunteers as a teaching slot missing a teacher."
            ),
        },
        "CRON_SYNC_VOLUNTEERS": {
            "value": "0 */2 * * *",
            "description": "Cron schedule for syncing volunteers from Google Sheets (default: every 2 hours)",
        },
        "CRON_SEND_WEEKLY_REMINDERS": {
            "value": "0 12 * * 0",
            "description": "Cron schedule for sending weekly reminder emails (default: every Sunday at 12:00 PM)",
        },
        "CRON_ROTATE_SCHEDULE": {
            "value": "0 * * * *",
            "description": (
                "Cron schedule for reconciling schedule sheets to the current week "
                "(default: hourly). The operation is idempotent, so running it often "
                "just means a missed run, a manual edit, or a week boundary is "
                "corrected within the hour instead of days later."
            ),
        },
        "CRON_POLL_INBOX": {
            "value": "0 8,18 * * *",
            "description": (
                "Cron schedule for polling the volunteer inbox (default: 08:00 and "
                "18:00 Vietnam time). Two runs a day is deliberate: the bot only "
                "ever replies to mail that is already waiting, so minutes of "
                "latency cost nothing and a rarer poll keeps the model spend and "
                "the blast radius of a bad run both small."
            ),
        },
        "EMAIL_BOT_MODE": {
            "value": "off",
            "description": (
                "Inbox bot mode: off, draft, or auto. off does nothing at all. "
                "draft labels every mail and leaves answers as Gmail drafts for "
                "review. auto sends them, and only exists from phase E3. Any "
                "unrecognised value reads as off, and this is read at the start of "
                "every run, so changing it here stops or starts the bot at the next "
                "poll with no deploy."
            ),
        },
        "EMAIL_BOT_AUTO_LANGUAGES": {
            "value": "en",
            "description": (
                "Comma-separated languages the bot may send automatically in auto "
                "mode (en, vi). A reply in any other language is left as a draft "
                "even in auto, so the Vietnamese copy can ship and be reviewed "
                "before a native speaker has signed it off."
            ),
        },
        "EMAIL_BOT_LAST_ERROR": {
            "value": "",
            "description": (
                "Written by the inbox bot when a run aborts or the Gmail grant is "
                "revoked, and shown as a dashboard banner. Cleared automatically by "
                "the next clean run, so a stale banner means the problem is still "
                "there."
            ),
        },
        "EMAIL_BOT_PER_RUN_CAP": {
            "value": "20",
            "description": (
                "Most messages the inbox bot will process in one run, in any mode. "
                "Bounds the model calls a single poll can make; anything left over "
                "stays unlabelled and is picked up by the next run."
            ),
        },
        "ESCALATION_OWNER_EMAIL": {
            "value": "",
            "description": (
                "The one address escalated mail is forwarded to. While it is empty "
                "the inbox bot refuses to run, because an escalation with nowhere "
                "to go would be silently dropped."
            ),
        },
        "KNOWLEDGE_BASE_DOC_ID": {
            "value": "",
            "description": (
                "Google Doc id of the curated knowledge base the bot answers FAQ "
                "questions from. Only this doc: the signup responses sheet, the "
                "schedule sheet and the volunteers table hold personal data and are "
                "never a retrieval source."
            ),
        },
        "TRIAGE_CLASSIFIER": {
            "value": "jev",
            "description": (
                "Which classifier decides the category: jev or litellm. The other "
                "one runs in shadow on every message and its answer is recorded for "
                "comparison, so this can be switched on evidence without a deploy."
            ),
        },
        "TRIAGE_FALLBACK_MODEL": {
            "value": "gemini/gemini-3.5-flash-lite",
            "description": (
                "LiteLLM model string for the shadow classifier, e.g. "
                "gemini/gemini-3.5-flash-lite or "
                "anthropic/claude-haiku-4-5-20251001. A Claude model needs "
                "ANTHROPIC_API_KEY set on the service."
            ),
        },
        "TRIAGE_CONFIDENCE_THRESHOLD": {
            "value": "0.6",
            "description": (
                "Below this classification confidence the bot hands the mail to a "
                "person instead of answering it. Applied after the category gate, "
                "so a low-confidence safeguarding guess still escalates."
            ),
        },
        "ANSWER_THRESHOLD": {
            "value": "0.5",
            "description": (
                "Minimum retrieval similarity before a generated FAQ answer is "
                "offered at all. Below it the sender gets the holding message and "
                "the mail goes to a person."
            ),
        },
        "VOLUNTEER_SIGNUP_FORM_LINK": {
            "value": "",
            "description": (
                "The volunteer signup Google Form. The sign-up reply is the one "
                "answer the bot gives from a fixed template, and it points only "
                "here; while this is empty the template is not rendered and the "
                "mail goes to a person instead."
            ),
        },
        "CLASS_START_TIME": {
            "value": "09:30",
            "description": (
                "When a class starts, 24-hour HH:MM. Rendered into the sign-up "
                "reply in each language rather than written into the copy, because "
                "it is a fact that will change."
            ),
        },
        "CLASS_END_TIME": {
            "value": "10:30",
            "description": "When a class ends, 24-hour HH:MM.",
        },
    }

    for key, config in default_settings.items():
        existing = db.query(Setting).filter(Setting.key == key).first()
        if not existing:
            setting = Setting(
                key=key, value=config["value"], description=config["description"]
            )
            db.add(setting)
        elif existing.description != config["description"]:
            # Values belong to whoever last edited them, but descriptions are
            # this code's own documentation of the key: without this, rewording
            # one leaves every already-deployed database showing the old text
            # forever. updated_at is deliberately left alone so a doc refresh
            # is not mistaken for a configuration change.
            existing.description = config["description"]

    db.commit()
