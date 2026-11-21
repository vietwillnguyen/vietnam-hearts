"""
Sentry events must not carry volunteer personal data.

Runs the real init_sentry() with a transport that keeps envelopes in memory,
then raises and logs the way production code does and inspects what would
have been sent.
"""

import json
import logging

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from app.utils import sentry_config

EMAIL = "nguyen.van.an@example.com"
PHONE = "0912 345 678"


class _MemoryTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


@pytest.fixture
def sent(monkeypatch):
    transport = _MemoryTransport()
    real_init = sentry_sdk.init
    captured_kwargs = {}

    def init(**kwargs):
        captured_kwargs.update(kwargs)
        return real_init(transport=transport, **kwargs)

    monkeypatch.setattr(
        sentry_config, "SENTRY_DSN", "https://key@o0.ingest.sentry.io/0"
    )
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setattr(sentry_config.sentry_sdk, "init", init)
    sentry_config.init_sentry()

    def payload():
        sentry_sdk.flush()
        return "\n".join(
            json.dumps(item.payload.json)
            for env in transport.envelopes
            for item in env.items
            if item.payload.json
        )

    yield captured_kwargs, payload
    sentry_sdk.init(dsn=None)


def test_default_pii_is_off(sent):
    kwargs, _ = sent
    assert kwargs["send_default_pii"] is False
    assert kwargs["before_send"] is sentry_config.scrub_event


def test_exception_messages_and_breadcrumbs_are_masked(sent):
    _, payload = sent
    logging.getLogger("sentry-probe").warning("about to email %s at %s", EMAIL, PHONE)
    try:
        raise ValueError(f"SMTP rejected {EMAIL}")
    except ValueError as e:
        sentry_sdk.capture_exception(e)

    text = payload()
    assert "SMTP rejected" in text, "no event was captured"
    assert "about to email" in text, "the breadcrumb was not captured"
    assert EMAIL not in text
    assert PHONE not in text
    assert "n***@example.com" in text


def test_scrub_event_handles_nested_payloads():
    event = {
        "exception": {"values": [{"value": f"bad {EMAIL}"}]},
        "breadcrumbs": {"values": [{"message": f"call {PHONE}"}]},
        "extra": {"rows": [[EMAIL, 3]]},
        "level": "error",
    }
    scrubbed = json.dumps(sentry_config.scrub_event(event, {}))
    assert EMAIL not in scrubbed
    assert PHONE not in scrubbed
    assert '"level": "error"' in scrubbed
