"""One-sentence escalation summaries, on the escalation path only.

The only place a generative model touches an escalated mail. It is worth one
call because the alternative is the captain opening every forward to find out
whether it can wait, and it is safe because the summary is never shown to the
sender and never used to decide anything - it is a convenience on a notice whose
durable content is the forwarded original.

The instruction is explicit about names and contact details, because the summary
goes to Discord, which has a wider audience than a mailbox.
"""

from __future__ import annotations

from typing import Any

from app.utils.logging_config import get_logger

logger = get_logger("email_bot_summaries")

SUMMARY_MODEL = "gemini-2.5-flash"

SUMMARY_PROMPT = (
    "Summarise this email in one short sentence, for a charity coordinator "
    "deciding how urgently to read it.\n"
    "Do not include anyone's name, email address, phone number, or any other "
    "contact detail. Do not greet, do not add a preamble, and do not quote the "
    "email. Answer with the sentence only, in English.\n"
    "\n"
    "Email:\n"
    "{text}"
)

MAX_INPUT_CHARS = 4000


def summarise_for_escalation(
    text: str, language: str, gemini_client: Any | None
) -> str | None:
    """A one-sentence summary, or None if anything at all goes wrong.

    Returning None rather than raising is deliberate: the design says the
    forward and the Discord post go out without the summary if generation
    fails. An escalation lost because a summariser was over quota would be the
    worst possible trade.
    """
    if gemini_client is None:
        return None
    body = (text or "").strip()
    if not body:
        return None

    try:
        response = gemini_client.models.generate_content(
            model=SUMMARY_MODEL,
            contents=SUMMARY_PROMPT.format(text=body[:MAX_INPUT_CHARS]),
        )
        summary = (getattr(response, "text", "") or "").strip()
    except Exception as exc:
        # Type name only: this runs on a path that has the mail in hand, and
        # a provider exception can echo the prompt back in its message.
        logger.warning("Escalation summary failed: %s", type(exc).__name__)
        return None

    if not summary:
        return None
    return " ".join(summary.split())
