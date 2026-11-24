"""The encryption helper that stored conversation text goes through.

Four properties are load-bearing, and each has its own class below:

- a round trip returns exactly what went in, Vietnamese diacritics included;
- the stored form is authenticated, so a wrong key, a tampered byte or a
  token moved to another column fails instead of decrypting to garbage;
- the stored form names the key that wrote it, so a new key can be added in
  front of the old one without rewriting a single row;
- a failure says which setting or which row, and never the key or the text.
"""

import base64
import os

import pytest

from app.utils import field_crypto
from app.utils.field_crypto import (
    KEYS_SETTING,
    DecryptionError,
    EncryptionKeyError,
    FieldCipher,
    check_storage_key_at_startup,
    generate_key_entry,
    parse_keys,
)

KEY_A = "k1:" + base64.urlsafe_b64encode(b"a" * 32).decode()
KEY_B = "k2:" + base64.urlsafe_b64encode(b"b" * 32).decode()

PLAINTEXT = "Xin chào, tôi muốn dạy tiếng Anh vào tháng Một. sender@example.com"
CONTEXT = "messages.body_encrypted"


class TestRoundTrip:
    def test_what_goes_in_comes_out(self):
        cipher = parse_keys(KEY_A)
        token = cipher.encrypt(PLAINTEXT, CONTEXT)
        assert cipher.decrypt(token, CONTEXT) == PLAINTEXT

    def test_the_stored_form_does_not_contain_the_plaintext(self):
        token = parse_keys(KEY_A).encrypt(PLAINTEXT, CONTEXT)
        assert "tiếng Anh" not in token
        assert "@" not in token
        assert "sender" not in token

    def test_two_encryptions_of_the_same_text_differ(self):
        # A fixed nonce would let anyone with the table see which mails are
        # identical, and would break AES-GCM outright.
        cipher = parse_keys(KEY_A)
        assert cipher.encrypt(PLAINTEXT, CONTEXT) != cipher.encrypt(PLAINTEXT, CONTEXT)

    def test_an_empty_string_round_trips(self):
        cipher = parse_keys(KEY_A)
        assert cipher.decrypt(cipher.encrypt("", CONTEXT), CONTEXT) == ""

    def test_none_stays_none_both_ways(self):
        cipher = parse_keys(KEY_A)
        assert cipher.encrypt_optional(None, CONTEXT) is None
        assert cipher.decrypt_optional(None, CONTEXT) is None


class TestTheFormat:
    def test_the_token_names_the_scheme_version_and_the_key(self):
        token = parse_keys(KEY_A).encrypt(PLAINTEXT, CONTEXT)
        scheme, version, key_id, _payload = token.split(":", 3)
        assert (scheme, version, key_id) == ("vhenc", "v1", "k1")


class TestAuthentication:
    def test_a_wrong_key_fails_rather_than_returning_garbage(self):
        token = parse_keys(KEY_A).encrypt(PLAINTEXT, CONTEXT)
        impostor = parse_keys("k1:" + base64.urlsafe_b64encode(b"z" * 32).decode())
        with pytest.raises(DecryptionError):
            impostor.decrypt(token, CONTEXT)

    def test_a_tampered_token_fails(self):
        cipher = parse_keys(KEY_A)
        token = cipher.encrypt(PLAINTEXT, CONTEXT)
        prefix, payload = token.rsplit(":", 1)
        raw = bytearray(base64.urlsafe_b64decode(payload))
        raw[-1] ^= 0x01
        tampered = f"{prefix}:{base64.urlsafe_b64encode(bytes(raw)).decode()}"
        with pytest.raises(DecryptionError):
            cipher.decrypt(tampered, CONTEXT)

    def test_a_token_moved_to_another_column_fails(self):
        # The column is bound in as associated data, so a subject cannot be
        # passed off as a body or an address by copying the ciphertext across.
        cipher = parse_keys(KEY_A)
        token = cipher.encrypt(PLAINTEXT, "messages.subject_encrypted")
        with pytest.raises(DecryptionError):
            cipher.decrypt(token, CONTEXT)

    @pytest.mark.parametrize(
        "token",
        ["", "plain text", "vhenc:v1:k1", "vhenc:v9:k1:AAAA", "vhenc:v1:k1:!!!"],
    )
    def test_a_malformed_token_fails_cleanly(self, token):
        with pytest.raises(DecryptionError):
            parse_keys(KEY_A).decrypt(token, CONTEXT)

    def test_a_token_from_an_unknown_key_says_which_key(self):
        token = parse_keys(KEY_B).encrypt(PLAINTEXT, CONTEXT)
        with pytest.raises(DecryptionError) as raised:
            parse_keys(KEY_A).decrypt(token, CONTEXT)
        assert "k2" in str(raised.value)


class TestRotation:
    def test_the_first_key_writes_and_every_key_reads(self):
        old = parse_keys(KEY_A).encrypt(PLAINTEXT, CONTEXT)

        rotated = parse_keys(f"{KEY_B},{KEY_A}")
        new = rotated.encrypt(PLAINTEXT, CONTEXT)

        assert new.split(":")[2] == "k2"
        assert rotated.decrypt(old, CONTEXT) == PLAINTEXT
        assert rotated.decrypt(new, CONTEXT) == PLAINTEXT

    def test_reencrypt_moves_a_token_onto_the_primary_key(self):
        old = parse_keys(KEY_A).encrypt(PLAINTEXT, CONTEXT)
        rotated = parse_keys(f"{KEY_B},{KEY_A}")
        moved = rotated.reencrypt(old, CONTEXT)
        assert moved.split(":")[2] == "k2"
        assert parse_keys(KEY_B).decrypt(moved, CONTEXT) == PLAINTEXT

    def test_the_primary_key_id_is_reported(self):
        assert parse_keys(f"{KEY_B},{KEY_A}").primary_key_id == "k2"


class TestParsingTheSetting:
    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_a_missing_setting_names_itself(self, raw):
        with pytest.raises(EncryptionKeyError) as raised:
            parse_keys(raw)
        assert KEYS_SETTING in str(raised.value)

    @pytest.mark.parametrize(
        "raw",
        [
            "just-a-key-with-no-id",
            "k1:not-base64!!",
            "k1:" + base64.urlsafe_b64encode(b"short").decode(),
            "BAD ID:" + base64.urlsafe_b64encode(b"a" * 32).decode(),
            f"{KEY_A},{KEY_A}",
            f"{KEY_A},",
        ],
    )
    def test_a_malformed_setting_names_itself_and_never_echoes_the_value(self, raw):
        with pytest.raises(EncryptionKeyError) as raised:
            parse_keys(raw)
        message = str(raised.value)
        assert KEYS_SETTING in message
        for fragment in raw.split(","):
            secret = fragment.split(":", 1)[-1]
            if len(secret) > 6:
                assert secret not in message

    def test_whitespace_around_entries_is_tolerated(self):
        cipher = parse_keys(f"  {KEY_B} , {KEY_A}\n")
        assert cipher.primary_key_id == "k2"

    def test_the_cipher_repr_never_shows_key_material(self):
        cipher = parse_keys(KEY_A)
        secret = KEY_A.split(":", 1)[1]
        assert secret not in repr(cipher)
        assert secret not in str(cipher)


class TestGeneratingAKey:
    def test_a_generated_entry_parses_and_works(self):
        entry = generate_key_entry("k7")
        cipher = parse_keys(entry)
        assert cipher.primary_key_id == "k7"
        assert cipher.decrypt(cipher.encrypt("x", CONTEXT), CONTEXT) == "x"

    def test_generated_keys_differ(self):
        assert generate_key_entry("k1") != generate_key_entry("k1")

    def test_the_runbooks_command_prints_an_entry_this_app_reads(self, capsys):
        # docs/GMAIL_BOT_SETUP.md generates the key with inbox-core's command.
        from inbox_core.crypto import main

        main(["generate-key", "--key-id", "k3"])
        printed = capsys.readouterr().out.strip()
        assert parse_keys(printed).primary_key_id == "k3"


class TestCipherFromEnvironment:
    def test_reads_the_setting(self, monkeypatch):
        monkeypatch.setenv(KEYS_SETTING, KEY_A)
        assert field_crypto.cipher_from_env().primary_key_id == "k1"

    def test_missing_setting_raises_naming_it(self, monkeypatch):
        monkeypatch.delenv(KEYS_SETTING, raising=False)
        with pytest.raises(EncryptionKeyError) as raised:
            field_crypto.cipher_from_env()
        assert KEYS_SETTING in str(raised.value)

    def test_the_test_suite_has_a_fixed_key(self):
        # conftest sets one, so every pipeline test stores through a real
        # cipher rather than through a code path that skips encryption.
        assert os.environ.get(KEYS_SETTING)
        assert field_crypto.cipher_from_env().primary_key_id


class TestStartupCheck:
    """Whether the app may start, for every combination that matters.

    The rule: production refuses to start when the bot could store mail and
    there is no usable key, and also whenever a key is set but malformed.
    Everything else starts, because a deployment with the bot off must not be
    broken by a key it has no use for yet.
    """

    @pytest.mark.parametrize("mode", ["draft", "auto"])
    def test_production_with_the_bot_on_and_no_key_refuses(self, mode):
        with pytest.raises(EncryptionKeyError) as raised:
            check_storage_key_at_startup(
                environment="production", bot_enabled=True, mode=mode, raw_keys=None
            )
        assert KEYS_SETTING in str(raised.value)

    @pytest.mark.parametrize(
        "enabled,mode", [(True, "off"), (False, "draft"), (False, "off")]
    )
    def test_production_with_the_bot_off_and_no_key_starts(self, enabled, mode):
        status = check_storage_key_at_startup(
            environment="production", bot_enabled=enabled, mode=mode, raw_keys=None
        )
        assert status == "missing"

    @pytest.mark.parametrize("enabled,mode", [(True, "draft"), (False, "off")])
    def test_production_with_a_malformed_key_refuses_whatever_the_mode(
        self, enabled, mode
    ):
        with pytest.raises(EncryptionKeyError):
            check_storage_key_at_startup(
                environment="production",
                bot_enabled=enabled,
                mode=mode,
                raw_keys="k1:garbage",
            )

    def test_production_with_a_valid_key_starts(self):
        status = check_storage_key_at_startup(
            environment="production", bot_enabled=True, mode="auto", raw_keys=KEY_A
        )
        assert status == "ok"

    @pytest.mark.parametrize("environment", ["development", "test", "staging"])
    def test_outside_production_nothing_refuses(self, environment):
        assert (
            check_storage_key_at_startup(
                environment=environment, bot_enabled=True, mode="draft", raw_keys=None
            )
            == "missing"
        )
        assert (
            check_storage_key_at_startup(
                environment=environment,
                bot_enabled=True,
                mode="draft",
                raw_keys="k1:garbage",
            )
            == "malformed"
        )

    def test_an_unreadable_mode_counts_as_on(self):
        # The mode is read from the database at startup. If that read failed,
        # guessing "off" would let a misconfigured production start and then
        # refuse every poll; refusing to start is the louder, safer failure.
        with pytest.raises(EncryptionKeyError):
            check_storage_key_at_startup(
                environment="production", bot_enabled=True, mode=None, raw_keys=None
            )


def test_field_cipher_requires_its_primary_key():
    with pytest.raises(EncryptionKeyError):
        FieldCipher({}, "k1")
