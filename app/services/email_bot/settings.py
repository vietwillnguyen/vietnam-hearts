"""``EmailBotSettings``: every dashboard-editable knob, read once per run.

Read at the start of the run and never cached for the process lifetime, because
the kill switch is worthless otherwise. Flipping ``EMAIL_BOT_MODE`` to ``off``
on the dashboard has to stop the next poll, with no deploy and no restart, and
that only works if the value is fetched rather than remembered.

Every numeric field is parsed defensively. These are free-text fields on an
admin form, so a blank or mistyped value is a realistic operator mistake, and
the safe reading of "I cannot understand the cap" is the design's default rather
than an exception that aborts the poll.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.services.settings_service import get_setting
from app.utils.logging_config import get_logger
from app.utils.schedule_dates import DEFAULT_SCHEDULE_TIMEZONE

logger = get_logger("email_bot_settings")

SETTING_MODE = "EMAIL_BOT_MODE"
SETTING_AUTO_LANGUAGES = "EMAIL_BOT_AUTO_LANGUAGES"
SETTING_LAST_ERROR = "EMAIL_BOT_LAST_ERROR"
SETTING_KNOWLEDGE_BASE_DOC_ID = "KNOWLEDGE_BASE_DOC_ID"
SETTING_ESCALATION_OWNER_EMAIL = "ESCALATION_OWNER_EMAIL"
SETTING_TRIAGE_CLASSIFIER = "TRIAGE_CLASSIFIER"
SETTING_TRIAGE_FALLBACK_MODEL = "TRIAGE_FALLBACK_MODEL"
SETTING_TRIAGE_CONFIDENCE_THRESHOLD = "TRIAGE_CONFIDENCE_THRESHOLD"
SETTING_ANSWER_THRESHOLD = "ANSWER_THRESHOLD"
SETTING_PER_RUN_CAP = "EMAIL_BOT_PER_RUN_CAP"
SETTING_DAILY_SEND_CAP = "EMAIL_BOT_DAILY_SEND_CAP"
SETTING_PER_SENDER_DAILY_CAP = "EMAIL_BOT_PER_SENDER_DAILY_CAP"
SETTING_SIGNUP_FORM_LINK = "VOLUNTEER_SIGNUP_FORM_LINK"
SETTING_CLASS_START_TIME = "CLASS_START_TIME"
SETTING_CLASS_END_TIME = "CLASS_END_TIME"
SETTING_TEACHING_DAYS = "SCHEDULE_TEACHING_DAYS"
SETTING_TIMEZONE = "SCHEDULE_TIMEZONE"

# The listing window. Not a setting: it is coupled to the hand-triage query in
# the re-consent runbook, and the two must not be able to drift apart.
LISTING_WINDOW_DAYS = 7


@dataclass(frozen=True)
class EmailBotSettings:
    """One immutable snapshot of the configuration a run operates under."""

    mode: str = "off"
    auto_languages: frozenset[str] = frozenset({"en"})
    escalation_owner_email: str = ""
    knowledge_base_doc_id: str = ""
    triage_classifier: str = "jev"
    triage_fallback_model: str = "gemini/gemini-3.5-flash-lite"
    triage_confidence_threshold: float = 0.6
    answer_threshold: float = 0.5
    per_run_cap: int = 20
    daily_send_cap: int = 30
    per_sender_daily_cap: int = 2
    signup_form_link: str = ""
    class_start_time: str = "09:30"
    class_end_time: str = "10:30"
    teaching_days: str = "Tuesday, Thursday"
    timezone: str = DEFAULT_SCHEDULE_TIMEZONE
    raw: dict[str, str] = field(default_factory=dict, compare=False)

    @classmethod
    def load(cls, db: Session) -> EmailBotSettings:
        def text(key: str, default: str) -> str:
            value = get_setting(db, key, default)
            return default if value is None else str(value)

        return cls(
            mode=text(SETTING_MODE, "off").strip().lower(),
            auto_languages=_parse_languages(text(SETTING_AUTO_LANGUAGES, "en")),
            escalation_owner_email=text(SETTING_ESCALATION_OWNER_EMAIL, "").strip(),
            knowledge_base_doc_id=text(SETTING_KNOWLEDGE_BASE_DOC_ID, "").strip(),
            triage_classifier=text(SETTING_TRIAGE_CLASSIFIER, "jev").strip().lower(),
            triage_fallback_model=text(
                SETTING_TRIAGE_FALLBACK_MODEL, "gemini/gemini-3.5-flash-lite"
            ).strip(),
            triage_confidence_threshold=_parse_ratio(
                text(SETTING_TRIAGE_CONFIDENCE_THRESHOLD, "0.6"),
                0.6,
                SETTING_TRIAGE_CONFIDENCE_THRESHOLD,
            ),
            answer_threshold=_parse_ratio(
                text(SETTING_ANSWER_THRESHOLD, "0.5"), 0.5, SETTING_ANSWER_THRESHOLD
            ),
            per_run_cap=_parse_positive_int(
                text(SETTING_PER_RUN_CAP, "20"), 20, SETTING_PER_RUN_CAP
            ),
            daily_send_cap=_parse_positive_int(
                text(SETTING_DAILY_SEND_CAP, "30"), 30, SETTING_DAILY_SEND_CAP
            ),
            per_sender_daily_cap=_parse_positive_int(
                text(SETTING_PER_SENDER_DAILY_CAP, "2"),
                2,
                SETTING_PER_SENDER_DAILY_CAP,
            ),
            signup_form_link=text(SETTING_SIGNUP_FORM_LINK, "").strip(),
            class_start_time=text(SETTING_CLASS_START_TIME, "09:30").strip(),
            class_end_time=text(SETTING_CLASS_END_TIME, "10:30").strip(),
            teaching_days=text(SETTING_TEACHING_DAYS, "Tuesday, Thursday"),
            timezone=text(SETTING_TIMEZONE, DEFAULT_SCHEDULE_TIMEZONE).strip(),
        )

    def may_send_language(self, language: str) -> bool:
        """Whether this language is cleared for automatic sending.

        Vietnamese joins the list only after native-speaker sign-off, which is
        why the gate is per language rather than one global switch: the
        Vietnamese templates can ship and be reviewed as drafts while English
        is already sending.
        """
        return language in self.auto_languages


def _parse_languages(raw: str) -> frozenset[str]:
    tokens = frozenset(
        token.strip().lower() for token in (raw or "").split(",") if token.strip()
    )
    # An empty list would silently mean "send nothing automatically", which
    # reads as a bug rather than a decision. English is the documented default.
    return tokens or frozenset({"en"})


def _parse_ratio(raw: str, default: float, key: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; falling back to %s", key, raw, default)
        return default
    if not 0.0 <= value <= 1.0:
        logger.warning("%s=%r is outside 0..1; falling back to %s", key, raw, default)
        return default
    return value


def _parse_positive_int(raw: str, default: int, key: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; falling back to %s", key, raw, default)
        return default
    if value < 1:
        logger.warning("%s=%r is below 1; falling back to %s", key, raw, default)
        return default
    return value
