"""Reading stored conversation text back in tests.

Stored text is encrypted, so an assertion about what was stored has to decrypt
it, and it does so through the same cipher the app uses. A test that compared
the ciphertext would pass or fail for reasons unrelated to what was kept.
"""

from __future__ import annotations

from app.utils.field_crypto import cipher_from_env


def stored(row, column: str) -> str | None:
    """The decrypted value of ``row.<column>``, e.g. ``stored(row, "body_encrypted")``."""
    token = getattr(row, column)
    if token is None:
        return None
    return cipher_from_env().decrypt(token, f"messages.{column}")


def body(row) -> str:
    """The decrypted body of a message row, or an empty string."""
    return stored(row, "body_encrypted") or ""
