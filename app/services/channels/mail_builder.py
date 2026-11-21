"""Pure MIME construction and extraction. No Gmail, no settings, no I/O.

Threading an email correctly is entirely a matter of three headers, and getting
them wrong is invisible until a real client renders the reply outside the
conversation. ``In-Reply-To`` names the message being answered, ``References``
carries the whole chain with that message appended, and the subject gains one
``Re:`` and never a second.

Every reply the bot builds also carries the RFC 3834 auto-response headers, so
a well-behaved responder on the other side stays silent instead of answering
back. The inbound guards in ``mail_guards.py`` catch the rest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from email.header import Header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html.parser import HTMLParser
from typing import Any

from app.services.channels.base import OutboundReply

# RFC 3834 section 5 and Microsoft's X-Auto-Response-Suppress. Both say the
# same thing to two different families of responder: this is a machine reply,
# do not answer it.
AUTO_RESPONSE_HEADERS = {
    "Auto-Submitted": "auto-replied",
    "X-Auto-Response-Suppress": "All",
}

_RE_PREFIX = re.compile(r"^\s*re\s*:\s*", re.IGNORECASE)
_WHITESPACE_RUN = re.compile(r"[ \t]*\n[ \t]*")


def reply_subject(subject: str) -> str:
    """``Re:`` the subject exactly once.

    A thread that has been round-tripped a few times arrives as
    ``Re: Re: RE: ...``; collapsing the run keeps the bot from adding a fourth
    and keeps the header comparable in tests.
    """
    stripped = (subject or "").strip()
    while _RE_PREFIX.match(stripped):
        stripped = _RE_PREFIX.sub("", stripped, count=1).strip()
    if not stripped:
        return "Re:"
    return f"Re: {stripped}"


def reference_chain(references: tuple[str, ...], in_reply_to: str) -> str:
    """The ``References`` value for a reply: the inherited chain plus this message.

    Order is preserved because it is the chain's meaning, and duplicates are
    dropped because a client that sees the same id twice may split the thread.
    """
    chain: list[str] = []
    for message_id in (*references, in_reply_to):
        candidate = (message_id or "").strip()
        if candidate and candidate not in chain:
            chain.append(candidate)
    return " ".join(chain)


def build_reply(
    reply: OutboundReply,
    from_address: str,
    from_name: str = "Vietnam Hearts",
    message_id: str | None = None,
    date: str | None = None,
) -> bytes:
    """The reply as raw RFC 5322 bytes, ready for a Gmail draft or send.

    ``text/plain`` only, UTF-8. The volunteer inbox is a conversation, not a
    newsletter, and a plain-text reply is what a human typing in Gmail would
    produce - it also round-trips Vietnamese diacritics without the quoted
    -printable surprises an HTML part invites.

    Takes the body exactly as given, signature included. The copy - which
    line signs off a Vietnamese reply, whether a holding message needs one at
    all - belongs to ``email_bot/replies.py``; this module's job ends at
    turning a body into correctly threaded bytes.

    ``message_id`` and ``date`` are injectable so the bytes are deterministic
    under test; in production both default to freshly generated values.
    """
    message = EmailMessage()
    message["From"] = f"{Header(from_name, 'utf-8').encode()} <{from_address}>"
    message["To"] = reply.to_address
    message["Subject"] = reply_subject(reply.subject)
    message["Date"] = date or formatdate(localtime=True)
    message["Message-ID"] = message_id or make_msgid()

    if reply.in_reply_to:
        message["In-Reply-To"] = reply.in_reply_to
    chain = reference_chain(reply.references, reply.in_reply_to)
    if chain:
        message["References"] = chain

    for name, value in AUTO_RESPONSE_HEADERS.items():
        message[name] = value

    message.set_content(reply.text.rstrip() + "\n", subtype="plain", charset="utf-8")

    return message.as_bytes()


class _TextFromHtml(HTMLParser):
    """Minimal tag-stripper for the html-only fallback.

    Not a renderer and not trying to be: the classifier and the retrieval
    query need the words, not the layout. ``script`` and ``style`` contents are
    dropped because otherwise a tracking snippet becomes the "body" of a mail
    whose visible text was one sentence.
    """

    _SKIP = frozenset({"script", "style", "head"})
    _BREAK = frozenset({"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        collapsed = _WHITESPACE_RUN.sub("\n", joined)
        return re.sub(r"\n{3,}", "\n\n", collapsed).strip()


def strip_html(html: str) -> str:
    parser = _TextFromHtml()
    parser.feed(html or "")
    parser.close()
    return parser.text()


def extract_text(resource: Mapping[str, Any]) -> str:
    """The best plain-text reading of a Gmail message resource.

    ``text/plain`` wins wherever it appears in the MIME tree. A mail with only
    an HTML part - which plenty of real mail clients send - is stripped to text
    rather than treated as empty, because an empty body would otherwise route a
    perfectly ordinary question to the wrong tier. An honestly empty result is
    left empty and the pipeline escalates it.
    """
    root = resource.get("payload") or {}
    plain = _collect_part(root, "text/plain")
    if plain.strip():
        return plain.strip()
    html = _collect_part(root, "text/html")
    if html.strip():
        return strip_html(html)
    return ""


def _collect_part(part: Mapping[str, Any], mime_type: str) -> str:
    """Depth-first concatenation of every part of one MIME type.

    Concatenating rather than taking the first match matters for the
    multipart/mixed mails some clients produce, where the sentence the person
    wrote and their quoted context arrive as separate parts of the same type.
    """
    collected: list[str] = []

    if (part.get("mimeType") or "").lower() == mime_type:
        decoded = _decode_body(part.get("body") or {})
        if decoded:
            collected.append(decoded)

    for child in part.get("parts") or []:
        nested = _collect_part(child, mime_type)
        if nested:
            collected.append(nested)

    return "\n".join(collected)


def _decode_body(body: Mapping[str, Any]) -> str:
    """Decode one part's base64url body, tolerating whatever charset it used.

    A body with an ``attachmentId`` and no ``data`` is a separate fetch we
    deliberately do not make: the bot answers from the message text, and
    downloading attachments from strangers is not something this pipeline
    should ever do.
    """
    import base64
    import binascii

    data = body.get("data")
    if not data:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    return decoded.decode("utf-8", errors="replace")
