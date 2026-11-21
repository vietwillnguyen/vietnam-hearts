#!/usr/bin/env python3
"""One-off OAuth consent for the volunteer inbox, and a token health check.

Run once, locally, signed in as the volunteer inbox. It opens the Google consent
screen, and prints the resulting refresh token to stdout so it can be pasted
straight into Secret Manager.

    uv run python scripts/gmail_oauth_consent.py --client-secrets ~/vh-desktop.json

Nothing is written to disk. ``google-auth-oauthlib`` will happily cache a token
file, and a long-lived credential for a charity's mailbox sitting in a
downloads folder is exactly the thing this script exists to avoid creating.

Verify the grant on day 8:

    uv run python scripts/gmail_oauth_consent.py --check

A token that still refreshes a week later is the proof that matters. Google
expires refresh tokens after 7 days only while the consent screen is in
*Testing* status, so surviving day 8 demonstrates the screen is actually
published - which no amount of reading the console UI reliably tells you.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The single scope the bot is allowed. gmail.modify covers reading, labelling
# and drafts, and deliberately not gmail.send: the bot's outbound mail in the
# first phases is the escalation forward, which goes over the existing SMTP
# path, and E3's in-thread sends are still within modify.
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
ALLOWED_SCOPES = frozenset({GMAIL_MODIFY_SCOPE})

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


class ScopeRefused(ValueError):
    """A scope other than gmail.modify was asked for.

    Refused rather than warned about. This script's output is a durable
    credential for a mailbox full of strangers' mail, and the narrowness of the
    grant is the main thing limiting what a leaked token could do.
    """


def validate_scopes(scopes: list[str]) -> list[str]:
    extra = sorted(set(scopes) - ALLOWED_SCOPES)
    if extra:
        raise ScopeRefused(
            "This script only ever requests "
            f"{GMAIL_MODIFY_SCOPE}; refusing {', '.join(extra)}"
        )
    if not scopes:
        raise ScopeRefused("No scopes requested")
    return scopes


def run_consent(
    client_secrets_path: Path, scopes: list[str] | None = None, port: int = 0
) -> str:
    """Walk the installed-app consent flow and return the refresh token.

    The loopback flow, not the deprecated out-of-band one. It needs a browser on
    the same machine, which is correct: the person granting this has to be
    signed in as the volunteer inbox and has to read the unverified-app warning
    rather than paste a code from somewhere else.
    """
    scopes = validate_scopes(list(scopes or [GMAIL_MODIFY_SCOPE]))

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), scopes)
    credentials = flow.run_local_server(port=port, open_browser=True)

    if not credentials.refresh_token:
        raise RuntimeError(
            "Google returned no refresh token. Revoke the app's existing access "
            "at myaccount.google.com/permissions and run this again: Google only "
            "issues a refresh token on the first consent for a given client."
        )
    return credentials.refresh_token


def check_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Refresh the token and return the address it is a grant for.

    Calls ``users.getProfile``, which is the cheapest possible proof that both
    halves work: the refresh succeeded *and* the scope actually covers the
    mailbox. A refresh that succeeds against the wrong account is a real
    failure mode when there are throwaway test inboxes around.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=GOOGLE_TOKEN_URI,
        scopes=[GMAIL_MODIFY_SCOPE],
    )
    credentials.refresh(Request())

    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute() or {}
    return profile.get("emailAddress", "")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Obtain or verify the volunteer inbox's Gmail refresh token."
    )
    parser.add_argument(
        "--client-secrets",
        type=Path,
        help="Path to the Desktop OAuth client JSON downloaded from Google Cloud.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify the existing grant instead of creating one. Reads "
            "GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET and "
            "GMAIL_OAUTH_REFRESH_TOKEN from the environment."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port for the consent redirect. 0 picks a free one.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the result as a JSON object."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.check:
        client_id = os.getenv("GMAIL_OAUTH_CLIENT_ID", "")
        client_secret = os.getenv("GMAIL_OAUTH_CLIENT_SECRET", "")
        refresh_token = os.getenv("GMAIL_OAUTH_REFRESH_TOKEN", "")
        missing = [
            name
            for name, value in (
                ("GMAIL_OAUTH_CLIENT_ID", client_id),
                ("GMAIL_OAUTH_CLIENT_SECRET", client_secret),
                ("GMAIL_OAUTH_REFRESH_TOKEN", refresh_token),
            )
            if not value
        ]
        if missing:
            print(f"Not set: {', '.join(missing)}", file=sys.stderr)
            return 2

        try:
            address = check_token(client_id, client_secret, refresh_token)
        except Exception as exc:
            # Non-zero on invalid_grant is the whole point of --check: it is
            # meant to be runnable from a cron or a runbook step that fails
            # loudly rather than printing a warning nobody reads.
            print(f"Token check failed: {exc}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps({"status": "ok", "email_address": address}))
        else:
            print(f"Token is valid. Granted for: {address}")
        return 0

    if not args.client_secrets:
        print("--client-secrets is required unless --check is given.", file=sys.stderr)
        return 2
    if not args.client_secrets.is_file():
        print(f"No such file: {args.client_secrets}", file=sys.stderr)
        return 2

    try:
        refresh_token = run_consent(args.client_secrets, port=args.port)
    except ScopeRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"refresh_token": refresh_token}))
    else:
        print("\nRefresh token (store as GMAIL_OAUTH_REFRESH_TOKEN, never commit it):")
        print(refresh_token)
        print(
            "\nPut it in Secret Manager alongside the client id and secret, then "
            "run this script again with --check on day 8."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
