"""Fixed reply copy, rendered from settings. Never generated.

Both of these are fixed strings for the same three reasons the parent spec gives
for the holding message: a model asked to write an apology while it is failing is
the least reliable moment to trust it, the copy is the organisation's voice at
the moment it is asking for patience or offering a way in, and a fixed string is
testable in a way a generated one is not.

What *is* dynamic is the facts: class days, class time and the form link change,
so they come from settings and are substituted into the captain's own wording
rather than re-typed into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.config import EMAIL_TEMPLATES_PATH
from app.utils.schedule_dates import parse_teaching_days

BOT_TEMPLATE_DIR = Path(EMAIL_TEMPLATES_PATH) / "bot"

SIGNUP_TEMPLATES = {"en": "signup-reply.en.txt", "vi": "signup-reply.vi.txt"}
HOLDING_TEMPLATES = {"en": "holding.en.txt", "vi": "holding.vi.txt"}

# Senders in any other language get the English copy, and derive_tier has
# already routed them to needs_admin, so a person will follow up.
FALLBACK_LANGUAGE = "en"

WEEKDAY_ORDER = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

VIETNAMESE_DAYS = {
    "Monday": "thứ Hai",
    "Tuesday": "thứ Ba",
    "Wednesday": "thứ Tư",
    "Thursday": "thứ Năm",
    "Friday": "thứ Sáu",
    "Saturday": "thứ Bảy",
    "Sunday": "Chủ Nhật",
}


class SignupReplyUnavailable(RuntimeError):
    """The sign-up template cannot be rendered truthfully yet.

    Raised when ``VOLUNTEER_SIGNUP_FORM_LINK`` is empty. The whole point of the
    reply is the link; sending the copy without it would tell someone to "fill
    out the form below" and then show them nothing, which is worse than
    escalating to a person.
    """


@dataclass(frozen=True)
class SignupFacts:
    """The settings-derived facts the sign-up copy substitutes."""

    teaching_days: str
    class_start: str
    class_end: str
    signup_form_link: str


@lru_cache(maxsize=1)
def _environment() -> Environment:
    """Jinja over the bot template directory, with undefined as an error.

    ``StrictUndefined`` because a typo in a placeholder name must not render as
    an empty string. A sign-up reply that silently lost its class times would
    look perfectly fine in review and be wrong in the inbox.
    """
    return Environment(
        loader=FileSystemLoader(str(BOT_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def resolve_language(language: str | None) -> str:
    return language if language in SIGNUP_TEMPLATES else FALLBACK_LANGUAGE


def localise_days(days: str | None, language: str) -> str:
    """Teaching days as the copy needs to read them, in the sender's language.

    Parsed through ``schedule_dates.parse_teaching_days`` rather than split
    here, so the reply can never disagree with the days the schedule sheet and
    the weekly reminder are working from, and re-ordered into week order because
    a set has none.
    """
    tokens = parse_teaching_days(days)
    ordered = [name for name in WEEKDAY_ORDER if name.lower()[:3] in tokens]
    if not ordered:
        ordered = ["Tuesday", "Thursday"]

    if language == "vi":
        return " và ".join(VIETNAMESE_DAYS[name] for name in ordered)

    # "Tuesdays and Thursdays" reads as a recurring arrangement, which is what
    # the captain's own copy says; "Tuesday and Thursday" reads as one week.
    plural = [f"{name}s" for name in ordered]
    if len(plural) == 1:
        return plural[0]
    return f"{', '.join(plural[:-1])} and {plural[-1]}"


def format_class_time(start: str, end: str, language: str) -> str:
    """The class window, in the form each language's copy expects.

    English keeps the captain's own ``9:30-10:30am`` shape. Vietnamese uses the
    24-hour clock, which is how times are written there.
    """
    if language == "vi":
        return f"{start} đến {end}"
    return f"{_to_12_hour(start)}-{_to_12_hour(end, with_meridiem=True)}"


def _to_12_hour(value: str, with_meridiem: bool = False) -> str:
    try:
        hour_text, minute_text = value.strip().split(":")[:2]
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, ValueError):
        return value

    meridiem = "am" if hour < 12 else "pm"
    display_hour = hour % 12 or 12
    rendered = f"{display_hour}:{minute:02d}"
    return f"{rendered}{meridiem}" if with_meridiem else rendered


def render_signup_reply(language: str, facts: SignupFacts) -> str:
    """The captain's sign-up copy, verbatim apart from the three placeholders."""
    if not facts.signup_form_link.strip():
        raise SignupReplyUnavailable(
            "VOLUNTEER_SIGNUP_FORM_LINK is empty; refusing to render a sign-up "
            "reply that points nowhere"
        )

    resolved = resolve_language(language)
    template = _environment().get_template(SIGNUP_TEMPLATES[resolved])
    return template.render(
        teaching_days=localise_days(facts.teaching_days, resolved),
        class_time=format_class_time(facts.class_start, facts.class_end, resolved),
        signup_form_link=facts.signup_form_link.strip(),
    ).strip()


def render_holding_message(language: str) -> str:
    """The parent spec's holding copy, unchanged, in the sender's language."""
    resolved = resolve_language(language)
    template = _environment().get_template(HOLDING_TEMPLATES[resolved])
    return template.render().strip()


def signature(language: str) -> str:
    """The one line appended to every bot reply.

    Present because the holding message discloses that it is automated and the
    sign-up reply does not; a reader deserves to know which of the two they
    have in either case.
    """
    if resolve_language(language) == "vi":
        return "- Trợ lý tự động của Vietnam Hearts"
    return "- Vietnam Hearts automated assistant"
