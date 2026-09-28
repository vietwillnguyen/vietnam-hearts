"""Builders for Gmail ``users.messages.get(format="full")`` resources.

Shaped to the published contract rather than to what the adapter happens to
read, which is the point: the parent spec's defect 2 was a test suite that
asserted against payloads invented to match the code. These builders produce the
base64url-encoded, nested-parts structure Gmail actually returns, so a change to
``extract_text`` that only works on a flattened shape fails here.

The recorded JSON under ``gmail_recorded/`` is the other half of the same
remedy. Nothing in either place contains a real volunteer's words, name or
address; every body here is written for the fixture.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

# Fictional addresses on example.com, which RFC 2606 reserves for exactly this.
TEST_INBOX = "vh-bot-test-inbox@example.com"
TEST_SENDER = "sender@example.com"
TEST_OWNER = "owner@example.com"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def header_list(headers: Mapping[str, str]) -> list[dict[str, str]]:
    """Headers in Gmail's ``[{"name": ..., "value": ...}]`` form.

    Names keep the casing a real mail would carry, because the adapter is
    supposed to lower-case them itself and a fixture that pre-lower-cased them
    would hide a bug in that.
    """
    return [{"name": name, "value": value} for name, value in headers.items()]


def plain_part(text: str) -> dict[str, Any]:
    encoded = b64(text)
    return {
        "partId": "0",
        "mimeType": "text/plain",
        "filename": "",
        "headers": header_list({"Content-Type": 'text/plain; charset="UTF-8"'}),
        "body": {"size": len(encoded), "data": encoded},
    }


def html_part(html: str) -> dict[str, Any]:
    encoded = b64(html)
    return {
        "partId": "1",
        "mimeType": "text/html",
        "filename": "",
        "headers": header_list({"Content-Type": 'text/html; charset="UTF-8"'}),
        "body": {"size": len(encoded), "data": encoded},
    }


def message(
    *,
    message_id: str = "msg-1",
    thread_id: str = "thread-1",
    subject: str = "Volunteering",
    from_address: str = TEST_SENDER,
    to_address: str = TEST_INBOX,
    text: str | None = "I would like to volunteer.",
    html: str | None = None,
    rfc_message_id: str | None = "<abc123@mail.example.com>",
    references: str | None = None,
    in_reply_to: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
    label_ids: tuple[str, ...] = ("INBOX", "UNREAD"),
    internal_date_ms: int = 1_759_000_000_000,
    multipart: bool | None = None,
) -> dict[str, Any]:
    """One message resource.

    ``text=None`` with ``html`` set produces the html-only mail that the
    ``extract_text`` fallback exists for; both None produces the genuinely empty
    body, which the pipeline must escalate rather than crash on.
    """
    headers: dict[str, str] = {
        "Delivered-To": to_address,
        "From": from_address,
        "To": to_address,
        "Subject": subject,
        "Date": "Mon, 29 Sep 2026 09:12:03 +0700",
        "MIME-Version": "1.0",
    }
    if rfc_message_id:
        headers["Message-ID"] = rfc_message_id
    if references:
        headers["References"] = references
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
    if extra_headers:
        headers.update(extra_headers)

    parts: list[dict[str, Any]] = []
    if text is not None:
        parts.append(plain_part(text))
    if html is not None:
        parts.append(html_part(html))

    use_multipart = multipart if multipart is not None else len(parts) != 1

    if use_multipart:
        payload: dict[str, Any] = {
            "partId": "",
            "mimeType": "multipart/alternative"
            if len(parts) > 1
            else "multipart/mixed",
            "filename": "",
            "headers": header_list(headers),
            "body": {"size": 0},
            "parts": parts,
        }
    else:
        only = dict(parts[0])
        only["headers"] = header_list({**headers, **_header_map(only["headers"])})
        only["partId"] = ""
        payload = only

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": list(label_ids),
        "snippet": (text or "")[:80],
        "internalDate": str(internal_date_ms),
        "sizeEstimate": 2048,
        "historyId": "990099",
        "payload": payload,
    }


def _header_map(entries: list[dict[str, str]]) -> dict[str, str]:
    return {entry["name"]: entry["value"] for entry in entries}


def thread(*messages: Mapping[str, Any], thread_id: str = "thread-1") -> dict[str, Any]:
    return {
        "id": thread_id,
        "historyId": "990099",
        "messages": list(messages),
    }


def list_response(*message_resources: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "messages": [
            {"id": resource["id"], "threadId": resource["threadId"]}
            for resource in message_resources
        ],
        "resultSizeEstimate": len(message_resources),
    }


def labels_response(*names: str) -> dict[str, Any]:
    """A ``users.labels.list`` response.

    System labels are included because a real mailbox has them and the
    transport must not mistake one for a label it created.
    """
    system = ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "UNREAD")
    labels = [{"id": name, "name": name, "type": "system"} for name in system]
    labels.extend(
        {"id": f"Label_{index + 100}", "name": name, "type": "user"}
        for index, name in enumerate(names)
    )
    return {"labels": labels}
