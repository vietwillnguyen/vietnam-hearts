"""Pure verification helpers for Meta webhooks.

Kept free of FastAPI and I/O so the security-critical logic can be tested
directly. Both functions fail closed: a missing secret or token yields a
refusal rather than an accidental accept.
"""

import hashlib
import hmac

SIGNATURE_PREFIX = "sha256="


def verify_signature(
    app_secret: str | None, raw_body: bytes, header: str | None
) -> bool:
    """Check an X-Hub-Signature-256 header against the raw request body.

    The digest must be computed over the exact bytes received. Re-serializing
    parsed JSON changes whitespace and key order, which breaks the comparison.
    """
    if not app_secret or not header or not header.startswith(SIGNATURE_PREFIX):
        return False

    received = header[len(SIGNATURE_PREFIX) :]
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def resolve_challenge(
    expected_token: str | None,
    mode: str | None,
    verify_token: str | None,
    challenge: str | None,
) -> str | None:
    """Return the challenge to echo back, or None to refuse verification.

    hub.challenge is an opaque random string, not an integer, so it is echoed
    verbatim.
    """
    if not expected_token or not challenge:
        return None
    if mode != "subscribe":
        return None
    if not hmac.compare_digest(expected_token, verify_token or ""):
        return None
    return challenge
