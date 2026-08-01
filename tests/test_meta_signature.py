"""Unit tests for Meta webhook signature verification and the verify handshake."""

import hashlib
import hmac

from app.services.channels.meta_signature import resolve_challenge, verify_signature

APP_SECRET = "test_app_secret"
BODY = b'{"object":"page","entry":[]}'


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_accepts_a_correct_signature(self):
        assert verify_signature(APP_SECRET, BODY, _sign(APP_SECRET, BODY)) is True

    def test_rejects_a_signature_made_with_the_wrong_secret(self):
        assert verify_signature(APP_SECRET, BODY, _sign("wrong_secret", BODY)) is False

    def test_rejects_a_correct_signature_over_different_bytes(self):
        assert (
            verify_signature(APP_SECRET, b'{"object":"page"}', _sign(APP_SECRET, BODY))
            is False
        )

    def test_rejects_a_missing_header(self):
        assert verify_signature(APP_SECRET, BODY, None) is False

    def test_rejects_a_header_without_the_sha256_prefix(self):
        bare = hmac.new(APP_SECRET.encode(), BODY, hashlib.sha256).hexdigest()
        assert verify_signature(APP_SECRET, BODY, bare) is False

    def test_rejects_a_non_hex_header(self):
        assert verify_signature(APP_SECRET, BODY, "sha256=nothexatall") is False

    def test_fails_closed_when_no_app_secret_is_configured(self):
        assert verify_signature(None, BODY, _sign(APP_SECRET, BODY)) is False
        assert verify_signature("", BODY, _sign(APP_SECRET, BODY)) is False

    def test_rejects_a_non_ascii_header_without_raising(self):
        assert verify_signature(APP_SECRET, BODY, "sha256=café") is False


class TestResolveChallenge:
    def test_returns_the_challenge_when_mode_and_token_match(self):
        assert resolve_challenge("tok", "subscribe", "tok", "abc123") == "abc123"

    def test_returns_a_non_numeric_challenge_unchanged(self):
        # Meta sends an opaque random string, not an integer.
        assert resolve_challenge("tok", "subscribe", "tok", "a1b2-c3d4") == "a1b2-c3d4"

    def test_refuses_a_wrong_verify_token(self):
        assert resolve_challenge("tok", "subscribe", "nope", "abc123") is None

    def test_refuses_a_wrong_mode(self):
        assert resolve_challenge("tok", "unsubscribe", "tok", "abc123") is None

    def test_refuses_when_no_token_is_configured(self):
        assert resolve_challenge(None, "subscribe", "tok", "abc123") is None
        assert resolve_challenge("", "subscribe", "", "abc123") is None

    def test_refuses_a_missing_challenge(self):
        assert resolve_challenge("tok", "subscribe", "tok", None) is None

    def test_rejects_a_non_ascii_verify_token_without_raising(self):
        assert resolve_challenge("tok", "subscribe", "café", "abc123") is None

    def test_handles_a_non_ascii_configured_token(self):
        assert resolve_challenge("tök", "subscribe", "tok", "abc123") is None
        assert resolve_challenge("tök", "subscribe", "tök", "abc123") == "abc123"
