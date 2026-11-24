"""Application-level encryption for stored inbox conversation text.

Every subject, body and sender address the inbox bot keeps goes through here
before it reaches the database, so a copy of the database on its own - a leaked
backup, a mis-shared export - cannot be read. Anyone holding both the database
and the service's environment can, which is why the key lives in the Cloud Run
environment and never in the ``settings`` table.

The scheme is AES-256-GCM from ``cryptography``: authenticated, so a wrong key,
a flipped byte or a token copied into another column fails loudly rather than
decrypting to garbage. The column a value belongs to is bound in as associated
data for the same reason.

Stored form, one ASCII string per value::

    vhenc:v1:<key id>:<base64url(12-byte nonce || ciphertext || 16-byte tag)>

``v1`` is the scheme version and ``<key id>`` names the key that wrote it.

Key setting ``CONVERSATION_ENCRYPTION_KEYS`` is a comma-separated list of
``<key id>:<base64url of 32 random bytes>`` entries. The first entry encrypts
everything new; every entry can decrypt. Rotating is therefore: generate a key
(``python -m app.utils.field_crypto generate-key --key-id k2``), put it *in
front of* the old entry, deploy. Old rows keep reading with the old key, new
rows use the new one, and nothing has to be rewritten. Drop an old entry only
once no row names it any more (``FieldCipher.reencrypt`` moves a value onto the
primary key if that is ever wanted).

Nothing here logs. Errors name the setting, the key id or the caller's row ids,
and never key material or plaintext, because exception messages reach Sentry
and the ``system_logs`` table.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import re
import secrets
import sys
from collections.abc import Mapping, Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYS_SETTING = "CONVERSATION_ENCRYPTION_KEYS"

SCHEME = "vhenc"
VERSION = "v1"
KEY_BYTES = 32
NONCE_BYTES = 12

_KEY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}$")

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_MALFORMED = "malformed"


class EncryptionKeyError(ValueError):
    """The key setting is missing or malformed. Names the setting, never the value."""


class DecryptionError(Exception):
    """A stored value could not be decrypted.

    The message carries the column, the key id and whatever row ids the caller
    supplied. It never carries the token, the plaintext or key material.
    """


class FieldCipher:
    """Encrypts and decrypts single column values. Holds the keys; never shows them."""

    def __init__(self, keys: Mapping[str, bytes], primary_key_id: str) -> None:
        if primary_key_id not in keys:
            raise EncryptionKeyError(
                f"{KEYS_SETTING} has no entry for its primary key id"
            )
        self._keys = {key_id: AESGCM(key) for key_id, key in keys.items()}
        self.primary_key_id = primary_key_id

    def __repr__(self) -> str:
        return (
            f"FieldCipher(primary_key_id={self.primary_key_id!r}, "
            f"key_ids={sorted(self._keys)!r})"
        )

    __str__ = __repr__

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._keys[self.primary_key_id].encrypt(
            nonce, plaintext.encode("utf-8"), context.encode("utf-8")
        )
        payload = base64.urlsafe_b64encode(nonce + sealed).decode("ascii")
        return f"{SCHEME}:{VERSION}:{self.primary_key_id}:{payload}"

    def decrypt(self, token: str, context: str, *, ids: str = "") -> str:
        """The plaintext of ``token``, or ``DecryptionError`` naming ``ids``."""
        where = f"{context}{f' ({ids})' if ids else ''}"
        parts = (token or "").split(":", 3)
        if len(parts) != 4 or parts[0] != SCHEME:
            raise DecryptionError(f"{where} is not an encrypted value")
        _scheme, version, key_id, payload = parts
        if version != VERSION:
            raise DecryptionError(f"{where} uses unknown scheme version {version!r}")
        aead = self._keys.get(key_id)
        if aead is None:
            raise DecryptionError(
                f"{where} was encrypted with key id {key_id!r}, which is not in "
                f"{KEYS_SETTING}"
            )
        try:
            raw = _b64decode(payload)
        except (binascii.Error, ValueError, UnicodeEncodeError):
            raise DecryptionError(f"{where} is not valid base64") from None
        if len(raw) <= NONCE_BYTES:
            raise DecryptionError(f"{where} is truncated")
        try:
            opened = aead.decrypt(
                raw[:NONCE_BYTES], raw[NONCE_BYTES:], context.encode("utf-8")
            )
        except InvalidTag:
            # ``from None``: the original carries nothing useful and the chain
            # is what Sentry would render.
            raise DecryptionError(
                f"{where} failed authentication with key id {key_id!r}: wrong key, "
                "tampered value, or a value moved from another column"
            ) from None
        return opened.decode("utf-8")

    def encrypt_optional(self, plaintext: str | None, context: str) -> str | None:
        return None if plaintext is None else self.encrypt(plaintext, context)

    def decrypt_optional(
        self, token: str | None, context: str, *, ids: str = ""
    ) -> str | None:
        return None if token is None else self.decrypt(token, context, ids=ids)

    def reencrypt(self, token: str, context: str, *, ids: str = "") -> str:
        """``token`` re-sealed under the primary key, for a rotation clean-up."""
        return self.encrypt(self.decrypt(token, context, ids=ids), context)


def parse_keys(raw: str | None) -> FieldCipher:
    """A cipher from the setting's value, or ``EncryptionKeyError`` naming it."""
    if raw is None or not raw.strip():
        raise EncryptionKeyError(
            f"{KEYS_SETTING} is not set. Generate one with "
            "`python -m app.utils.field_crypto generate-key` and set it in the "
            "service environment."
        )

    keys: dict[str, bytes] = {}
    order: list[str] = []
    for position, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            raise EncryptionKeyError(f"{KEYS_SETTING} entry {position} is empty")
        key_id, separator, encoded = entry.partition(":")
        key_id = key_id.strip()
        if not separator or not _KEY_ID.match(key_id):
            raise EncryptionKeyError(
                f"{KEYS_SETTING} entry {position} must look like "
                "<key id>:<base64url key>, with a lower-case key id of at most "
                "16 characters"
            )
        try:
            key = _b64decode(encoded.strip())
        except (binascii.Error, ValueError, UnicodeEncodeError):
            raise EncryptionKeyError(
                f"{KEYS_SETTING} entry {position} (key id {key_id!r}) is not "
                "valid base64url"
            ) from None
        if len(key) != KEY_BYTES:
            raise EncryptionKeyError(
                f"{KEYS_SETTING} entry {position} (key id {key_id!r}) must decode "
                f"to {KEY_BYTES} bytes"
            )
        if key_id in keys:
            raise EncryptionKeyError(
                f"{KEYS_SETTING} names key id {key_id!r} more than once"
            )
        keys[key_id] = key
        order.append(key_id)

    return FieldCipher(keys, order[0])


def _b64decode(encoded: str) -> bytes:
    """Strict base64url: anything outside the alphabet is an error, not skipped."""
    padded = encoded + "=" * (-len(encoded) % 4)
    return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)


def cipher_from_env() -> FieldCipher:
    """The cipher the running service uses, read fresh from the environment."""
    return parse_keys(os.environ.get(KEYS_SETTING))


def key_status(raw: str | None) -> str:
    """``ok``, ``missing`` or ``malformed``, without raising."""
    try:
        parse_keys(raw)
    except EncryptionKeyError:
        return STATUS_MISSING if raw is None or not raw.strip() else STATUS_MALFORMED
    return STATUS_OK


def check_storage_key_at_startup(
    *,
    environment: str,
    bot_enabled: bool,
    mode: str | None,
    raw_keys: str | None,
) -> str:
    """Refuse to start a production service that would store mail unreadably or not at all.

    Production refuses when the key is malformed, whatever the mode: a value
    somebody set and got wrong is a mistake to surface on deploy, not on the
    first poll. It also refuses when the key is missing *and* the bot could run
    (``EMAIL_BOT_ENABLED`` true and ``EMAIL_BOT_MODE`` not ``off``); a mode that
    could not be read counts as on. With the bot off a missing key is
    reported and the service starts, so merging this before the key is set is
    safe. Outside production nothing refuses; the pipeline still refuses to
    poll without a key, in every environment.

    Returns the key status so the caller can log it.
    """
    from inbox_core.delivery import DeliveryMode, parse_mode

    status = key_status(raw_keys)
    if environment != "production" or status == STATUS_OK:
        return status

    if status == STATUS_MALFORMED:
        parse_keys(raw_keys)  # raises with the specific reason

    could_run = bot_enabled and (
        mode is None or parse_mode(mode) is not DeliveryMode.OFF
    )
    if could_run:
        raise EncryptionKeyError(
            f"{KEYS_SETTING} is not set, and the inbox bot is enabled "
            f"(EMAIL_BOT_ENABLED=true, EMAIL_BOT_MODE={mode or 'unreadable'}). "
            "It stores every conversation encrypted and cannot run without the "
            "key. Set the key, or set EMAIL_BOT_MODE to off, before deploying."
        )
    return status


def generate_key_entry(key_id: str) -> str:
    """A fresh ``<key id>:<key>`` entry for the setting."""
    if not _KEY_ID.match(key_id):
        raise EncryptionKeyError(
            "a key id must be lower-case letters, digits, '-' or '_', at most 16"
        )
    key = base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")
    return f"{key_id}:{key}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.utils.field_crypto",
        description="Manage the inbox conversation encryption key.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser(
        "generate-key", help=f"print a new {KEYS_SETTING} entry"
    )
    generate.add_argument("--key-id", default="k1")
    args = parser.parse_args(argv)

    if args.command == "generate-key":
        print(generate_key_entry(args.key_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
