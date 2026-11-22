#!/usr/bin/env python3
"""Serve the real admin dashboard locally, signed in as a fake admin, with seeded conversations.

    uv run python tools/dashboard_harness.py [--port 8765]

The dashboard sits behind a Supabase sign-in that cannot be completed locally,
so this runs the real FastAPI app - real routes, real template, real
JavaScript - with exactly one thing replaced: the admin dependency returns a
fixed made-up admin. Everything the Inbox Bot card shows comes from running
the recorded fixture corpus (``tests/fixtures/gmail_recorded``, written for
the tests, no real person's mail) through the real pipeline in draft mode, plus
one mail whose subject and body are an XSS payload, so escaping can be checked
by eye.

It never touches anything real: a throwaway SQLite file, a throwaway key
generated per run, no Gmail, no Supabase, no network. Dev-only; the Docker
image does not copy ``tools/``.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

XSS_SUBJECT = '<img src=x onerror="alert(\'subject\')"> Volunteering & "quotes"'
XSS_BODY = (
    "<script>alert('body')</script>\n"
    "Hello, I would like to volunteer.\n"
    '<a href="javascript:alert(1)">click</a> & <b>bold</b>'
)


def _prepare_environment(database: Path) -> None:
    from app.utils.field_crypto import generate_key_entry

    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "TESTING": "true",
            "ENVIRONMENT": "development",
            "PERSIST_LOGS_TO_DB": "false",
            "SUPABASE_URL": "https://harness.invalid",
            "SUPABASE_PUBLISHABLE_KEY": "harness-publishable",
            "SUPABASE_SECRET_KEY": "harness-secret",
            "GEMINI_API_KEY": "harness-gemini",
            "CONVERSATION_ENCRYPTION_KEYS": generate_key_entry("harness"),
        }
    )


def _seed() -> None:
    from app.database import SessionLocal, init_db
    from app.services.settings_service import set_setting
    from tests.fixtures import gmail_payloads as payloads
    from tests.fixtures.conversations import run_corpus
    from tests.fixtures.email_bot import load_gmail

    init_db()
    db = SessionLocal()
    try:
        set_setting(db, "EMAIL_BOT_MODE", "draft")
        corpus = [load_gmail(name) for name in _corpus_names()]
        xss = payloads.message(
            message_id="harness-xss",
            thread_id="harness-xss-thread",
            subject=XSS_SUBJECT,
            from_address="Mallory <mallory@example.com>",
            text=XSS_BODY,
        )
        run_corpus(db, fixtures=[*corpus, xss])
    finally:
        db.close()


def _corpus_names() -> list[str]:
    from tests.fixtures.conversations import corpus_signals

    return list(corpus_signals())


def _reset_rate_limits(app) -> None:
    """Give each visit a fresh budget: the dashboard calls /auth/me on every
    load, and the "auth" limit of 10 an hour is soon spent by reloading."""
    from app.middleware import RateLimitMiddleware

    node = app.middleware_stack
    while node is not None:
        if isinstance(node, RateLimitMiddleware):
            node.request_counts.clear()
            return
        node = getattr(node, "app", None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="vh-dashboard-harness-"))
    _prepare_environment(workdir / "harness.db")
    os.chdir(ROOT)

    _seed()

    import uvicorn
    from fastapi.responses import HTMLResponse

    from app.dependencies.auth import get_current_admin_user, get_current_user
    from app.main import app

    admin = {"id": "harness-admin", "email": "harness-admin@example.com"}
    app.dependency_overrides[get_current_admin_user] = lambda: dict(admin)
    app.dependency_overrides[get_current_user] = lambda: dict(admin)

    # The dashboard's own script sends the visitor home unless localStorage
    # holds a token, so this page puts a placeholder there and goes on.
    @app.get("/harness", response_class=HTMLResponse, include_in_schema=False)
    def harness_sign_in() -> str:
        _reset_rate_limits(app)
        return (
            "<script>localStorage.setItem('access_token','harness');"
            "location.replace('/admin/dashboard');</script>"
        )

    app.router.routes.insert(0, app.router.routes.pop())
    print(f"Dashboard: http://{args.host}:{args.port}/harness")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
