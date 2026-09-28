"""Deterministic guards that run before any model and before any row is written.

These are the first line of defence and they cost nothing: a mailing list
digest, a bounce, a vacation responder or a Gmail promotion is recognised from
its headers and labels alone, so it is labelled and dropped without a
classifier call and, crucially, without a reply. An auto-reply that gets a
holding message back is how a two-machine mail loop starts.

Pure by design, like ``meta_signature.py``: no I/O, no settings lookup, no
logging. Everything here is a function of the headers, the Gmail label ids and
a handful of configured addresses, which is what makes the table-driven test
over every rule in the design possible.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from email.utils import getaddresses, parseaddr
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.channels.gmail_transport import RawMail

# Gmail's own inbox categorisation. A mail Gmail filed under any of these is
# bulk by Gmail's judgement, which is better informed about the sender's
# reputation than anything we can read off one message.
BULK_GMAIL_LABELS = frozenset(
    {
        "CATEGORY_PROMOTIONS",
        "CATEGORY_SOCIAL",
        "CATEGORY_UPDATES",
        "CATEGORY_FORUMS",
    }
)

DRAFT_LABEL = "DRAFT"

# RFC 3834 section 5: a responder must not reply to anything whose
# Auto-Submitted is other than "no". RFC 2076 Precedence and the List-*
# headers cover mailing lists, and X-Auto-Response-Suppress is Microsoft's
# equivalent request.
_PRECEDENCE_BULK = frozenset({"bulk", "list", "junk"})

# Local parts that never belong to a person who wants an answer.
NO_REPLY_LOCAL_PARTS = frozenset(
    {
        "no-reply",
        "noreply",
        "no_reply",
        "donotreply",
        "do-not-reply",
        "mailer-daemon",
        "postmaster",
        "bounce",
        "bounces",
    }
)


def normalise_address(raw: str | None) -> str:
    """The bare, lower-cased address out of a From-style header value.

    ``"Some Name" <A.Person@Example.COM>`` becomes ``a.person@example.com``.
    Returns an empty string when there is no address to find, which every
    caller treats as "not this sender" rather than as a match.

    The ``@`` check is load-bearing. ``parseaddr`` is lenient and hands back the
    first token of an unparseable header - ``"not an address"`` yields ``"not"`` -
    which would otherwise become a sender key, a From comparison and, worst of
    all, the ``To`` of a reply. An address with no ``@`` is not an address.
    """
    if not raw:
        return ""
    candidate = parseaddr(raw)[1].strip().lower()
    return candidate if "@" in candidate else ""


def sender_key(address: str | None) -> str:
    """The durable, non-identifying form of a sender address.

    SHA-256 of the normalised address. Enough to count threads per sender and
    to group a conversation; not enough to recover who wrote in, which is why
    the address itself never reaches the database.
    """
    return hashlib.sha256(normalise_address(address).encode("utf-8")).hexdigest()


def _header(headers: Mapping[str, str], name: str) -> str:
    """Header lookup that does not care how the caller cased the keys."""
    if name in headers:
        return (headers[name] or "").strip()
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return (value or "").strip()
    return ""


def is_automated(
    headers: Mapping[str, str], label_ids: Iterable[str] = ()
) -> str | None:
    """The name of the first rule that marks this mail as machine-generated.

    Returns ``None`` when no rule matches, so the caller reads as
    ``if reason := is_automated(...)``. Returning the rule name rather than a
    bool is deliberate: the skip is recorded on the audit row, and "which rule
    fired" is the only thing that makes a wrong skip diagnosable without the
    body.
    """
    auto_submitted = _header(headers, "auto-submitted").lower()
    if auto_submitted and auto_submitted.split(";")[0].strip() != "no":
        return "auto-submitted"

    precedence = _header(headers, "precedence").lower()
    if precedence in _PRECEDENCE_BULK:
        return "precedence"

    if _header(headers, "list-id"):
        return "list-id"
    if _header(headers, "list-unsubscribe"):
        return "list-unsubscribe"
    if _header(headers, "x-auto-response-suppress"):
        return "x-auto-response-suppress"

    local_part = normalise_address(_header(headers, "from")).split("@")[0]
    if local_part in NO_REPLY_LOCAL_PARTS:
        return "no-reply-sender"

    for label_id in label_ids:
        if label_id in BULK_GMAIL_LABELS:
            return f"gmail-{label_id.removeprefix('CATEGORY_').lower()}"

    return None


def is_internal_sender(
    address: str | None,
    inbox: str | None,
    owner: str | None = None,
    admins: Iterable[str] | None = None,
) -> bool:
    """True when the writer is us rather than a member of the public.

    Three cases, all of which must never be triaged. The inbox writing to
    itself is either the bot's own outbound mail or the captain using the
    account, so answering it would be the bot talking to itself. The
    escalation owner and the admin addresses are the people the bot forwards
    *to*: a forward that came back through the inbox, or a coordinator's own
    mail, must not become a conversation with a reply waiting in it.
    """
    sender = normalise_address(address)
    if not sender:
        return False

    known = {normalise_address(inbox), normalise_address(owner)}
    known.update(normalise_address(admin) for admin in admins or ())
    known.discard("")
    return sender in known


def human_replied(
    thread: Sequence[RawMail],
    inbox_address: str | None,
    bot_message_ids: set[str] | None = None,
) -> bool:
    """True when somebody at Vietnam Hearts has already answered this thread.

    The test is "a message *from* the inbox address that the bot did not
    send", by recorded Gmail id. That makes a captain who sends one of the
    bot's own drafts count as a human reply, which is the intended reading:
    once he has touched the thread it is his, and the bot must not add to it.

    An unsent draft is not a reply. ``threads.get`` returns drafts as thread
    messages From the inbox, the bot's own pending draft included, and that
    draft's message id is never recorded; without skipping ``DRAFT`` a
    follow-up would read the bot's own draft as a person having answered.

    An unknown inbox address has no safe answer here, so the pipeline refuses
    to poll at all when it cannot resolve one
    (``EmailBotPipeline._inbox_address_refusal``). False would let the bot
    reply over a human; True pauses every thread, and because the inbound row
    is committed before the pause, that permanently drops mail the next run
    dedupes as done. This branch is therefore unreachable in the pipeline and
    exists only so that a direct caller fails safe rather than silently
    treating an unidentifiable mailbox as nobody.
    """
    inbox = normalise_address(inbox_address)
    if not inbox:
        return True

    sent_by_bot = bot_message_ids or set()
    for raw in thread:
        if raw.id in sent_by_bot or DRAFT_LABEL in raw.label_ids:
            continue
        senders = {
            normalise_address(value)
            for _, value in getaddresses([raw.header("from")])
            if value
        }
        if inbox in senders:
            return True
    return False
