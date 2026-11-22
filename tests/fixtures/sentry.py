"""Capturing what Sentry would have sent, without sending anything.

Runs the real ``init_sentry()`` - so the real options and ``before_send`` -
with a transport that keeps envelopes in memory. ``payload()`` flushes and
returns everything captured as one JSON string, which is what a privacy
assertion wants to search.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import sentry_sdk
from sentry_sdk.transport import Transport

from app.utils import sentry_config


class MemoryTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


@contextmanager
def captured_sentry(monkeypatch) -> Iterator[Callable[[], str]]:
    transport = MemoryTransport()
    real_init = sentry_sdk.init

    def init(**kwargs):
        return real_init(transport=transport, **kwargs)

    monkeypatch.setattr(
        sentry_config, "SENTRY_DSN", "https://key@o0.ingest.sentry.io/0"
    )
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setattr(sentry_config.sentry_sdk, "init", init)
    sentry_config.init_sentry()

    def payload() -> str:
        sentry_sdk.flush()
        return "\n".join(
            json.dumps(item.payload.json, ensure_ascii=False)
            for envelope in transport.envelopes
            for item in envelope.items
            if item.payload.json
        )

    try:
        yield payload
    finally:
        sentry_sdk.init(dsn=None)
