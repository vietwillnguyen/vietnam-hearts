"""Application-level encryption for stored inbox conversation text.

Every subject, body and sender address the inbox bot keeps goes through here
before it reaches the database, so a copy of the database on its own - a leaked
backup, a mis-shared export - cannot be read. Anyone holding both the database
and the service's environment can, which is why the key lives in the Cloud Run
environment and never in the ``settings`` table.

The cipher is inbox-core's ``FieldCipher``: AES-256-GCM, authenticated, so a
wrong key, a flipped byte or a token copied into another column fails loudly
rather than decrypting to garbage, with the column bound in as associated data
for the same reason. This module binds it to this app: the setting name, the
``vhenc`` scheme every stored token already carries, and the rule for when the
service may start without a key.

Stored form, one ASCII string per value::

    vhenc:v1:<key id>:<base64url(12-byte nonce || ciphertext || 16-byte tag)>

Key setting ``CONVERSATION_ENCRYPTION_KEYS`` is a comma-separated list of
``<key id>:<base64url of 32 random bytes>`` entries. The first entry encrypts
everything new; every entry can decrypt. Rotating is therefore: generate a key
(``python -m inbox_core.crypto generate-key --key-id k2``), put it *in front
of* the old entry, deploy. Old rows keep reading with the old key, new rows use
the new one, and nothing has to be rewritten. Drop an old entry only once no
row names it any more (``FieldCipher.reencrypt`` moves a value onto the primary
key if that is ever wanted).

Nothing here logs. Errors name the setting, the key id or the caller's row ids,
and never key material or plaintext, because exception messages reach Sentry
and the ``system_logs`` table.
"""

from __future__ import annotations

import os

from inbox_core.crypto import (
    DecryptionError,
    EncryptionKeyError,
    FieldCipher,
    generate_key_entry,
)
from inbox_core.crypto import parse_keys as _parse_keys

__all__ = [
    "KEYS_SETTING",
    "SCHEME",
    "DecryptionError",
    "EncryptionKeyError",
    "FieldCipher",
    "check_storage_key_at_startup",
    "cipher_from_env",
    "generate_key_entry",
    "key_status",
    "parse_keys",
]

KEYS_SETTING = "CONVERSATION_ENCRYPTION_KEYS"

# The scheme tag at the front of every stored token. Fixed: changing it would
# make every row already written unreadable.
SCHEME = "vhenc"

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_MALFORMED = "malformed"


def parse_keys(raw: str | None) -> FieldCipher:
    """A cipher from the setting's value, or ``EncryptionKeyError`` naming it."""
    return _parse_keys(raw, setting=KEYS_SETTING, scheme=SCHEME)


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
