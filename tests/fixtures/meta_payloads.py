"""Meta webhook payloads matching the published Messenger Platform contract.

Hand-written dictionaries are what let the hub.* binding defect ship green, so
these live in one place and every webhook test builds from them. During phase 3
App Review testing, capture a real delivery and diff it against these shapes
before trusting any new field.

Reference: https://developers.facebook.com/documentation/business-messaging/messenger-platform/overview
"""

from typing import Any

PAGE_ID = "PAGE_ID"

# Meta sends these as query parameters with literal dots in the names.
VERIFY_QUERY: dict[str, str] = {
    "hub.mode": "subscribe",
    "hub.challenge": "1158201444",
    "hub.verify_token": "test_verify_token",
}


def _envelope(messaging: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "page",
        "entry": [
            {
                "id": PAGE_ID,
                "time": 1764547200000,
                "messaging": messaging,
            }
        ],
    }


def page_text_message(
    sender_id: str = "USER_PSID",
    text: str = "How do I volunteer?",
    mid: str = "m_AG5Hz2U",
) -> dict[str, Any]:
    """An inbound text message from a person to the Page."""
    return _envelope(
        [
            {
                "sender": {"id": sender_id},
                "recipient": {"id": PAGE_ID},
                "timestamp": 1764547200000,
                "message": {"mid": mid, "text": text},
            }
        ]
    )


def page_postback(
    sender_id: str = "USER_PSID",
    payload: str = "GET_STARTED",
) -> dict[str, Any]:
    """A postback from a button or the Get Started action."""
    return _envelope(
        [
            {
                "sender": {"id": sender_id},
                "recipient": {"id": PAGE_ID},
                "timestamp": 1764547200000,
                "postback": {
                    "mid": "m_postback1",
                    "title": "Get Started",
                    "payload": payload,
                },
            }
        ]
    )


def page_echo(sender_id: str = PAGE_ID) -> dict[str, Any]:
    """A message the Page itself sent, echoed back.

    The bot must ignore these. Replying to its own echo is an infinite loop.
    """
    return _envelope(
        [
            {
                "sender": {"id": sender_id},
                "recipient": {"id": "USER_PSID"},
                "timestamp": 1764547200000,
                "message": {
                    "mid": "m_echo1",
                    "text": "Thanks for your question!",
                    "is_echo": True,
                    "app_id": 123456789,
                },
            }
        ]
    )
