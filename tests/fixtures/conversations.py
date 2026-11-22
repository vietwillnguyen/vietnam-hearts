"""Reading stored conversation text back in tests.

Stored text is encrypted, so an assertion about what was stored has to decrypt
it, and it does so through the same cipher the app uses. A test that compared
the ciphertext would pass or fail for reasons unrelated to what was kept.
"""

from __future__ import annotations

from typing import Any

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


OWNER = "owner@example.com"
FORM = "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"


def corpus_signals() -> dict[str, Any]:
    """Every recorded fixture with the classification it should get.

    Covers the draft path, the escalation path and the skip path in one run;
    the privacy sweep, the store tests, the admin API tests and the dashboard
    harness all use this one list.
    """
    from tests.fixtures.email_bot import signals

    return {
        "signup_en.json": signals(category="signup", confidence=0.94),
        "signup_vi.json": signals(category="signup", language="vi", confidence=0.89),
        "faq_en.json": signals(category="faq", confidence=0.86),
        "html_only.json": signals(category="faq", confidence=0.9),
        "empty_body.json": signals(category="faq", confidence=0.7),
        "newsletter.json": signals(category="automated", confidence=0.97),
        "vacation_autoreply.json": signals(category="automated", confidence=0.97),
        "bounce.json": signals(category="automated", confidence=0.97),
        "safeguarding_vi.json": signals(
            category="safeguarding_legal", language="vi", confidence=0.91
        ),
        "sponsorship_en.json": signals(
            category="sponsorship", confidence=0.82, money=True
        ),
        "multi_topic_en.json": signals(
            category="donation", confidence=0.58, money=True
        ),
    }


def run_corpus(db, *, fixtures=None, bot_service=None) -> dict[str, Any]:
    """Put the fixture corpus through the real pipeline in draft mode.

    Returns the run summary, every fixture as the adapter parsed it (keyed by
    Gmail id), and the fake transport, so a test can compare what was stored
    with what the mail said.
    """
    import anyio

    from app.services.channels.gmail import GmailAdapter
    from app.services.channels.gmail_transport import RawMail
    from app.services.email_bot.pipeline import EmailBotPipeline
    from app.services.email_bot.settings import EmailBotSettings
    from tests.fixtures.email_bot import (
        FakeBotService,
        FakeClassifier,
        FakeTransport,
        RecordingNotifier,
        load_gmail,
        signals,
    )

    plan = corpus_signals()
    resources = fixtures or [load_gmail(name) for name in plan]
    transport = FakeTransport(mails=resources)
    adapter = GmailAdapter(transport)
    by_message = {
        resource["id"]: plan[name]
        for name, resource in zip(plan, resources, strict=False)
    }
    pipeline = EmailBotPipeline(
        db=db,
        adapter=adapter,
        classifier=FakeClassifier(
            by_message=by_message,
            default=signals(category="human_other", confidence=0.5),
        ),
        notifier=RecordingNotifier(),
        settings=EmailBotSettings(
            mode="draft",
            escalation_owner_email=OWNER,
            signup_form_link=FORM,
            per_run_cap=50,
        ),
        bot_service=bot_service or FakeBotService(),
    )
    summary = anyio.run(lambda: anyio.to_thread.run_sync(pipeline.run))
    parsed = {
        resource["id"]: adapter.parse(RawMail.from_resource(resource))
        for resource in resources
    }
    return {"summary": summary, "parsed": parsed, "transport": transport}


def fixture_phrases(parsed) -> list[str]:
    """Distinctive strings of the corpus: whole body lines, subjects, addresses."""
    phrases: list[str] = []
    for message in parsed.values():
        phrases.extend(
            line.strip() for line in message.text.splitlines() if len(line.strip()) > 25
        )
        if len(message.subject) > 12:
            phrases.append(message.subject)
        if message.sender_address:
            phrases.append(message.sender_address)
    return phrases
