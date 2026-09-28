"""Deciding what happened to a draft the bot left in a thread.

This is how the draft-acceptance metric is measured, and the metric is what
decides whether automatic sending is ever turned on. So the question "did the
captain send this unchanged?" has to be answered from what Gmail shows on a
later poll, not from anything the bot was told.

Four outcomes, and the distinctions matter:

``sent_unchanged``  the draft is gone and a message with the same text is in the
                    thread. The bot got it right, and this is the number the
                    90/80 percent gates are measured on.
``sent_edited``     gone, and a message is there with different text. The bot
                    was useful but not right; worth having, and not a pass.
``deleted``         gone, and no message followed. The bot was wrong enough that
                    the captain wrote nothing, or answered another way.
``pending``         still there. Nothing has happened yet.

The text comparison is the subtle part. A reply the captain sends from Gmail
comes back with the quoted original appended, wrapped at a different width, and
sometimes with the signature moved. Comparing raw bodies would score every
single sent draft as edited, so the comparison normalises first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A quoted reply begins at the first of these. Everything after is the original
# mail Gmail appended, not something the captain wrote.
_QUOTE_MARKERS = (
    re.compile(r"^\s*On .+ wrote:\s*$", re.MULTILINE),
    re.compile(
        r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE
    ),
    re.compile(r"^\s*_{5,}\s*$", re.MULTILINE),
    # Vietnamese Gmail's own attribution line.
    re.compile(r"^\s*Vào .+ đã viết:\s*$", re.MULTILINE),
)

_QUOTED_LINE = re.compile(r"^\s*>.*$", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")


def strip_quoted_reply(text: str) -> str:
    """Everything before the quoted original.

    Takes the earliest marker rather than the last: a thread that has been
    round-tripped carries several, and only the first one bounds what this
    message's author actually typed.
    """
    body = text or ""
    cut = len(body)
    for marker in _QUOTE_MARKERS:
        found = marker.search(body)
        if found and found.start() < cut:
            cut = found.start()
    body = body[:cut]
    return _QUOTED_LINE.sub("", body)


def normalise(text: str) -> str:
    """The form two bodies are compared in.

    Collapses every whitespace run to one space and lower-cases, because a
    difference in line wrapping is not an edit and neither is a capital letter
    a mail client changed. What survives is the words, in order, which is what
    "sent it unchanged" actually means.
    """
    return _WHITESPACE.sub(" ", strip_quoted_reply(text)).strip().lower()


def texts_match(drafted: str, sent: str) -> bool:
    """Whether the sent message is the drafted text, allowing for a signature.

    A prefix match rather than equality: the captain adding a line of his own
    at the end is common, and treating "the draft plus a sentence" as a
    rejection would understate acceptance badly. A captain who rewrote the
    opening changes the prefix and is correctly scored as an edit.
    """
    drafted_text = normalise(drafted)
    sent_text = normalise(sent)
    if not drafted_text:
        return False
    return sent_text == drafted_text or sent_text.startswith(drafted_text)


@dataclass(frozen=True)
class Reconciliation:
    """What one poll concluded about one outstanding draft."""

    outcome: str
    sent_message_id: str | None = None


def reconcile_draft(
    *,
    draft_still_exists: bool,
    drafted_text: str,
    thread_messages: list,
    inbox_address: str,
    bot_message_ids: set[str],
) -> Reconciliation:
    """Classify one outstanding draft from what the thread shows now.

    ``thread_messages`` are ``RawMail``. Only messages *from* the inbox address
    that the bot did not send itself are candidates: anything else in the thread
    is the sender writing back.
    """
    from app.services.channels.mail_builder import extract_text
    from app.services.channels.mail_guards import normalise_address

    if draft_still_exists:
        return Reconciliation("pending")

    inbox = normalise_address(inbox_address)
    candidates = [
        message
        for message in thread_messages
        if message.id not in bot_message_ids
        and normalise_address(message.header("from")) == inbox
    ]

    if not candidates:
        # The draft is gone and nobody replied from the inbox: the captain
        # deleted it.
        return Reconciliation("deleted")

    # The newest candidate is the one the captain sent in place of the draft.
    # Gmail returns a thread oldest first.
    sent = candidates[-1]
    body = extract_text(sent.payload)
    outcome = "sent_unchanged" if texts_match(drafted_text, body) else "sent_edited"
    return Reconciliation(outcome, sent_message_id=sent.id)
