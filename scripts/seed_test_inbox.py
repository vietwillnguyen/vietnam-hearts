#!/usr/bin/env python3
"""Seed a THROWAWAY Gmail inbox with the anonymised sample corpus, over SMTP.

Part of the live end-to-end protocol, and the reason it is a script rather than a
test: it sends real mail, so it must never be something CI can trigger.

    uv run python scripts/seed_test_inbox.py \
        --corpus tests/fixtures/sample_corpus.yaml \
        --to vh-bot-test-inbox@gmail.com \
        --from-address vh-bot-test-sender@gmail.com

Two hard refusals, both checked before a socket is opened:

- the recipient must not be the real volunteer inbox, and
- ``--i-confirm-this-is-a-throwaway-account`` must be given.

Neither is paranoia about the script. It is paranoia about the next person to
copy a command out of a runbook into the wrong shell.
"""

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Any

# Hard-coded, not configurable. This is the one address the whole build is
# forbidden to touch, and a refusal that can be turned off with a flag is not
# a refusal.
REAL_VOLUNTEER_INBOX = "vietnam.hearts.volunteering@gmail.com"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


class RefusedTarget(RuntimeError):
    """The recipient is the real inbox, or the throwaway flag is missing."""


def assert_safe_target(to_address: str, confirmed: bool) -> None:
    target = (to_address or "").strip().lower()
    if not target:
        raise RefusedTarget("No recipient given")
    if target == REAL_VOLUNTEER_INBOX:
        raise RefusedTarget(
            f"Refusing to send to {REAL_VOLUNTEER_INBOX}. This script is only "
            "ever pointed at a throwaway test inbox."
        )
    if not confirmed:
        raise RefusedTarget(
            "Pass --i-confirm-this-is-a-throwaway-account to confirm the "
            "recipient is a disposable test mailbox."
        )


def load_corpus(path: Path) -> list[dict[str, Any]]:
    """Load the sample cases.

    YAML if PyYAML is importable, otherwise the same file read as JSON, so the
    script works in an environment that has not installed a YAML parser just to
    send a dozen test mails.
    """
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        loaded = yaml.safe_load(text)
    except ImportError:
        import json

        loaded = json.loads(text)

    cases = loaded.get("cases") if isinstance(loaded, dict) else loaded
    if not isinstance(cases, list):
        raise ValueError(f"{path} does not contain a list of cases")
    return cases


def build_message(
    case: dict[str, Any], from_address: str, to_address: str
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = to_address
    message["Subject"] = case.get("subject", "(no subject)")
    for name, value in (case.get("headers") or {}).items():
        # Set rather than add, so a case can override a header the builder
        # already wrote (a guard case supplying its own Precedence, say).
        if name in message:
            del message[name]
        message[name] = value
    message.set_content(case.get("text", ""), subtype="plain", charset="utf-8")
    return message


def send_all(
    cases: list[dict[str, Any]],
    from_address: str,
    app_password: str,
    to_address: str,
    dry_run: bool = False,
) -> int:
    messages = [build_message(case, from_address, to_address) for case in cases]

    if dry_run:
        for case, message in zip(cases, messages, strict=True):
            print(f"[dry-run] {case.get('id', '?')}: {message['Subject']}")
        return len(messages)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(from_address, app_password)
        for case, message in zip(cases, messages, strict=True):
            server.send_message(message)
            print(f"sent {case.get('id', '?')}: {message['Subject']}")
    return len(messages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--to", required=True, help="The throwaway inbox under test.")
    parser.add_argument("--from-address", required=True, help="The throwaway sender.")
    parser.add_argument(
        "--app-password",
        default="",
        help="App password for the sender. Read from TEST_SENDER_APP_PASSWORD if omitted.",
    )
    parser.add_argument(
        "--i-confirm-this-is-a-throwaway-account", action="store_true", dest="confirmed"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        assert_safe_target(args.to, args.confirmed or args.dry_run)
    except RefusedTarget as exc:
        print(str(exc), file=sys.stderr)
        return 2

    import os

    password = args.app_password or os.getenv("TEST_SENDER_APP_PASSWORD", "")
    if not password and not args.dry_run:
        print(
            "No app password given. Pass --app-password or set "
            "TEST_SENDER_APP_PASSWORD.",
            file=sys.stderr,
        )
        return 2

    count = send_all(
        load_corpus(args.corpus),
        args.from_address,
        password,
        args.to,
        dry_run=args.dry_run,
    )
    print(f"{count} message(s) {'prepared' if args.dry_run else 'sent'}.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
