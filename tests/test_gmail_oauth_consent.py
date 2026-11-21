"""The one-off consent script, with the OAuth flow mocked.

Two behaviours matter enough to pin. The scope refusal, because the narrowness
of the grant is the main thing limiting what a leaked token could do. And the
non-zero exit on ``invalid_grant``, because ``--check`` is meant to be runnable
from a runbook step that fails loudly rather than printing a warning nobody
reads.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gmail_oauth_consent as consent  # noqa: E402

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


class TestScopeIsRefusedUnlessItIsExactlyGmailModify:
    def test_gmail_modify_alone_is_accepted(self):
        assert consent.validate_scopes([consent.GMAIL_MODIFY_SCOPE]) == [
            consent.GMAIL_MODIFY_SCOPE
        ]

    @pytest.mark.parametrize(
        "scopes",
        [
            [DRIVE_SCOPE],
            [consent.GMAIL_MODIFY_SCOPE, DRIVE_SCOPE],
            ["https://www.googleapis.com/auth/gmail.readonly"],
            ["https://mail.google.com/"],
        ],
    )
    def test_any_other_scope_is_refused(self, scopes):
        with pytest.raises(consent.ScopeRefused):
            consent.validate_scopes(scopes)

    def test_no_scope_at_all_is_refused(self):
        with pytest.raises(consent.ScopeRefused):
            consent.validate_scopes([])

    def test_the_allowed_set_is_exactly_one_scope(self):
        assert consent.ALLOWED_SCOPES == {consent.GMAIL_MODIFY_SCOPE}

    def test_run_consent_refuses_a_wider_scope_before_opening_a_browser(self, tmp_path):
        secrets = tmp_path / "client.json"
        secrets.write_text("{}")

        with patch.object(consent, "validate_scopes", wraps=consent.validate_scopes):
            with pytest.raises(consent.ScopeRefused):
                consent.run_consent(secrets, [DRIVE_SCOPE])


class TestRunConsent:
    def _flow(self, refresh_token: str | None = "1//refresh-token"):
        flow = MagicMock()
        flow.run_local_server.return_value = MagicMock(refresh_token=refresh_token)
        return flow

    def test_the_refresh_token_is_returned(self, tmp_path):
        secrets = tmp_path / "client.json"
        secrets.write_text("{}")
        flow = self._flow()

        with patch.dict(
            sys.modules,
            {
                "google_auth_oauthlib.flow": MagicMock(
                    InstalledAppFlow=MagicMock(
                        from_client_secrets_file=MagicMock(return_value=flow)
                    )
                )
            },
        ):
            assert consent.run_consent(secrets) == "1//refresh-token"

    def test_a_missing_refresh_token_is_an_actionable_error(self, tmp_path):
        # Google only issues one on the first consent for a client, so the
        # message has to say how to get back to that state.
        secrets = tmp_path / "client.json"
        secrets.write_text("{}")
        flow = self._flow(refresh_token=None)

        with patch.dict(
            sys.modules,
            {
                "google_auth_oauthlib.flow": MagicMock(
                    InstalledAppFlow=MagicMock(
                        from_client_secrets_file=MagicMock(return_value=flow)
                    )
                )
            },
        ):
            with pytest.raises(RuntimeError, match="Revoke"):
                consent.run_consent(secrets)

    def test_only_gmail_modify_reaches_the_flow(self, tmp_path):
        secrets = tmp_path / "client.json"
        secrets.write_text("{}")
        from_file = MagicMock(return_value=self._flow())

        with patch.dict(
            sys.modules,
            {
                "google_auth_oauthlib.flow": MagicMock(
                    InstalledAppFlow=MagicMock(from_client_secrets_file=from_file)
                )
            },
        ):
            consent.run_consent(secrets)

        assert from_file.call_args.args[1] == [consent.GMAIL_MODIFY_SCOPE]


class TestCheckExitsNonZeroOnInvalidGrant:
    def _env(self, **overrides):
        env = {
            "GMAIL_OAUTH_CLIENT_ID": "client-id",
            "GMAIL_OAUTH_CLIENT_SECRET": "client-secret",
            "GMAIL_OAUTH_REFRESH_TOKEN": "1//refresh-token",
        }
        env.update(overrides)
        return env

    def test_a_valid_token_exits_zero_and_names_the_account(self, capsys):
        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch.object(consent, "check_token", return_value="inbox@example.com"),
        ):
            assert consent.main(["--check"]) == 0

        assert "inbox@example.com" in capsys.readouterr().out

    def test_an_invalid_grant_exits_one(self, capsys):
        # The whole point: a runbook step or a cron can depend on this.
        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch.object(
                consent,
                "check_token",
                side_effect=RuntimeError("invalid_grant: Token has been expired"),
            ),
        ):
            assert consent.main(["--check"]) == 1

        assert "invalid_grant" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "missing",
        [
            "GMAIL_OAUTH_CLIENT_ID",
            "GMAIL_OAUTH_CLIENT_SECRET",
            "GMAIL_OAUTH_REFRESH_TOKEN",
        ],
    )
    def test_a_missing_variable_exits_two_and_names_it(self, capsys, missing):
        with patch.dict(os.environ, self._env(**{missing: ""}), clear=False):
            assert consent.main(["--check"]) == 2
        assert missing in capsys.readouterr().err

    def test_json_output_is_machine_readable(self, capsys):
        import json

        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch.object(consent, "check_token", return_value="inbox@example.com"),
        ):
            consent.main(["--check", "--json"])

        assert json.loads(capsys.readouterr().out) == {
            "status": "ok",
            "email_address": "inbox@example.com",
        }


class TestTheTokenIsNeverWrittenToDisk:
    def test_the_script_does_not_write_a_token_file(self):
        # google-auth-oauthlib will happily cache one, and a long-lived
        # credential for a charity's mailbox in a downloads folder is exactly
        # what this script exists to avoid creating.
        source = Path(consent.__file__).read_text()
        for forbidden in ("token.json", "open(", ".write_text(", "pickle"):
            assert forbidden not in source

    def test_the_token_goes_to_stdout_only(self, tmp_path, capsys):
        secrets = tmp_path / "client.json"
        secrets.write_text("{}")
        flow = MagicMock()
        flow.run_local_server.return_value = MagicMock(refresh_token="1//tok")

        with patch.dict(
            sys.modules,
            {
                "google_auth_oauthlib.flow": MagicMock(
                    InstalledAppFlow=MagicMock(
                        from_client_secrets_file=MagicMock(return_value=flow)
                    )
                )
            },
        ):
            assert consent.main(["--client-secrets", str(secrets)]) == 0

        assert "1//tok" in capsys.readouterr().out
        assert list(tmp_path.iterdir()) == [secrets]


class TestArgumentHandling:
    def test_client_secrets_is_required_without_check(self, capsys):
        assert consent.main([]) == 2
        assert "--client-secrets is required" in capsys.readouterr().err

    def test_a_missing_secrets_file_exits_two(self, capsys, tmp_path):
        assert consent.main(["--client-secrets", str(tmp_path / "nope.json")]) == 2
        assert "No such file" in capsys.readouterr().err
