# Phase 0: Messenger Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the disabled, broken Facebook Messenger bot into a wired-up, signature-verified, quota-efficient service that fails closed instead of answering from meaningless embeddings.

**Architecture:** Replace `app/routers/messenger.py` with `app/routers/webhooks.py` mounted at `/webhook/meta`, so phase 3 can add Instagram to the same endpoint without a second URL. Signature verification and challenge handling move into a pure, separately testable module. `BotService` becomes a cached FastAPI dependency instead of a per-message construction. `KnowledgeService` loses its hash-embedding fallback and reports unavailability honestly.

**Tech Stack:** Python 3.12, FastAPI 0.116, SQLAlchemy 2.x, pytest, uv, ruff. No new runtime dependencies in this phase.

## Global Constraints

- Python `>=3.12`. This phase adds **no runtime dependencies**. The single permitted dependency change is `pytest-asyncio` in the dev group, added by Task 4; any other dependency addition is out of scope.
- Run everything through uv: `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`.
- Never add a co-author or attribution trailer to a commit, and never reference the tool that produced a change.
- Commit messages follow `<type>: <description>`, imperative, lowercase, no trailing period.
- The webhook must return HTTP 200 for any well-formed request it cannot process. Meta disables webhooks after repeated non-200 responses. The only non-200 responses permitted are 403 for a failed signature check and 403 for a failed verification handshake.
- The bot only ever replies to an inbound message. No outbound-initiated messaging in any task.
- No em dash characters in code, comments, docs, or commit messages. Use a plain hyphen.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/services/channels/__init__.py` | Package marker |
| `app/services/channels/meta_signature.py` | Pure functions: HMAC verification and the verification-handshake decision. No FastAPI, no I/O, so it is trivially testable. |
| `app/routers/webhooks.py` | HTTP surface only: bind params, call the signature module, dispatch events. Replaces `app/routers/messenger.py`. |
| `app/dependencies/services.py` | Cached `get_bot_service()` provider |
| `tests/fixtures/meta_payloads.py` | Meta webhook payloads matching the published contract, used by every adapter test |
| `tests/test_meta_signature.py` | Unit tests for the pure signature module |
| `tests/test_webhooks.py` | Integration tests for the webhook endpoint. Replaces `tests/test_messenger.py`. |

Deleted at the end of the phase: `app/routers/messenger.py`, `tests/test_messenger.py`.

---

### Task 1: Pure signature and handshake module

**Files:**
- Create: `app/services/channels/__init__.py`
- Create: `app/services/channels/meta_signature.py`
- Test: `tests/test_meta_signature.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `verify_signature(app_secret: str | None, raw_body: bytes, header: str | None) -> bool`
  - `resolve_challenge(expected_token: str | None, mode: str | None, verify_token: str | None, challenge: str | None) -> str | None` returning the challenge string to echo, or `None` when verification must be refused.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_meta_signature.py`:

```python
"""Unit tests for Meta webhook signature verification and the verify handshake."""

import hashlib
import hmac

from app.services.channels.meta_signature import resolve_challenge, verify_signature

APP_SECRET = "test_app_secret"
BODY = b'{"object":"page","entry":[]}'


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_accepts_a_correct_signature(self):
        assert verify_signature(APP_SECRET, BODY, _sign(APP_SECRET, BODY)) is True

    def test_rejects_a_signature_made_with_the_wrong_secret(self):
        assert verify_signature(APP_SECRET, BODY, _sign("wrong_secret", BODY)) is False

    def test_rejects_a_correct_signature_over_different_bytes(self):
        assert verify_signature(APP_SECRET, b'{"object":"page"}', _sign(APP_SECRET, BODY)) is False

    def test_rejects_a_missing_header(self):
        assert verify_signature(APP_SECRET, BODY, None) is False

    def test_rejects_a_header_without_the_sha256_prefix(self):
        bare = hmac.new(APP_SECRET.encode(), BODY, hashlib.sha256).hexdigest()
        assert verify_signature(APP_SECRET, BODY, bare) is False

    def test_rejects_a_non_hex_header(self):
        assert verify_signature(APP_SECRET, BODY, "sha256=nothexatall") is False

    def test_fails_closed_when_no_app_secret_is_configured(self):
        assert verify_signature(None, BODY, _sign(APP_SECRET, BODY)) is False
        assert verify_signature("", BODY, _sign(APP_SECRET, BODY)) is False


class TestResolveChallenge:
    def test_returns_the_challenge_when_mode_and_token_match(self):
        assert resolve_challenge("tok", "subscribe", "tok", "abc123") == "abc123"

    def test_returns_a_non_numeric_challenge_unchanged(self):
        # Meta sends an opaque random string, not an integer.
        assert resolve_challenge("tok", "subscribe", "tok", "a1b2-c3d4") == "a1b2-c3d4"

    def test_refuses_a_wrong_verify_token(self):
        assert resolve_challenge("tok", "subscribe", "nope", "abc123") is None

    def test_refuses_a_wrong_mode(self):
        assert resolve_challenge("tok", "unsubscribe", "tok", "abc123") is None

    def test_refuses_when_no_token_is_configured(self):
        assert resolve_challenge(None, "subscribe", "tok", "abc123") is None
        assert resolve_challenge("", "subscribe", "", "abc123") is None

    def test_refuses_a_missing_challenge(self):
        assert resolve_challenge("tok", "subscribe", "tok", None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_meta_signature.py -v`
Expected: FAIL, collection error `ModuleNotFoundError: No module named 'app.services.channels'`

- [ ] **Step 3: Write the implementation**

Create `app/services/channels/__init__.py` as an empty file.

Create `app/services/channels/meta_signature.py`:

```python
"""Pure verification helpers for Meta webhooks.

Kept free of FastAPI and I/O so the security-critical logic can be tested
directly. Both functions fail closed: a missing secret or token yields a
refusal rather than an accidental accept.
"""

import hashlib
import hmac

SIGNATURE_PREFIX = "sha256="


def verify_signature(app_secret: str | None, raw_body: bytes, header: str | None) -> bool:
    """Check an X-Hub-Signature-256 header against the raw request body.

    The digest must be computed over the exact bytes received. Re-serializing
    parsed JSON changes whitespace and key order, which breaks the comparison.
    """
    if not app_secret or not header or not header.startswith(SIGNATURE_PREFIX):
        return False

    received = header[len(SIGNATURE_PREFIX) :]
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def resolve_challenge(
    expected_token: str | None,
    mode: str | None,
    verify_token: str | None,
    challenge: str | None,
) -> str | None:
    """Return the challenge to echo back, or None to refuse verification.

    hub.challenge is an opaque random string, not an integer, so it is echoed
    verbatim.
    """
    if not expected_token or not challenge:
        return None
    if mode != "subscribe":
        return None
    if not hmac.compare_digest(expected_token, verify_token or ""):
        return None
    return challenge
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_meta_signature.py -v`
Expected: PASS, 13 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app/services/channels tests/test_meta_signature.py
uv run ruff format app/services/channels tests/test_meta_signature.py
git add app/services/channels tests/test_meta_signature.py
git commit -m "feat: add pure meta webhook signature and handshake helpers"
```

---

### Task 2: Meta payload fixtures

**Files:**
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/meta_payloads.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `page_text_message(sender_id: str = "USER_PSID", text: str = "How do I volunteer?", mid: str = "m_AG5Hz2U") -> dict`, `page_postback(sender_id: str = "USER_PSID", payload: str = "GET_STARTED") -> dict`, `page_echo(sender_id: str = "PAGE_ID") -> dict`, and `VERIFY_QUERY: dict[str, str]`.

This task exists on its own because every later task and both phase 3 tasks consume these fixtures. The current `tests/test_messenger.py` invented its payload shape by hand, which is how the `hub.*` defect survived; centralising the real shape once prevents a repeat.

- [ ] **Step 1: Write the fixture module**

Create `tests/fixtures/__init__.py` as an empty file.

Create `tests/fixtures/meta_payloads.py`:

```python
"""Meta webhook payloads matching the published Messenger Platform contract.

Hand-written dictionaries are what let the hub.* binding defect ship green, so
these live in one place and every webhook test builds from them. During phase 3
App Review testing, capture a real delivery and diff it against these shapes
before trusting any new field.

Reference: https://developers.facebook.com/documentation/business-messaging/messenger-platform/overview
"""

from typing import Any

PAGE_ID = "PAGE_ID"

# Meta sends these as query parameters with literal dots in the names.
VERIFY_QUERY: dict[str, str] = {
    "hub.mode": "subscribe",
    "hub.challenge": "1158201444",
    "hub.verify_token": "test_verify_token",
}


def _envelope(messaging: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "page",
        "entry": [
            {
                "id": PAGE_ID,
                "time": 1764547200000,
                "messaging": messaging,
            }
        ],
    }


def page_text_message(
    sender_id: str = "USER_PSID",
    text: str = "How do I volunteer?",
    mid: str = "m_AG5Hz2U",
) -> dict[str, Any]:
    """An inbound text message from a person to the Page."""
    return _envelope(
        [
            {
                "sender": {"id": sender_id},
                "recipient": {"id": PAGE_ID},
                "timestamp": 1764547200000,
                "message": {"mid": mid, "text": text},
            }
        ]
    )


def page_postback(
    sender_id: str = "USER_PSID",
    payload: str = "GET_STARTED",
) -> dict[str, Any]:
    """A postback from a button or the Get Started action."""
    return _envelope(
        [
            {
                "sender": {"id": sender_id},
                "recipient": {"id": PAGE_ID},
                "timestamp": 1764547200000,
                "postback": {"mid": "m_postback1", "title": "Get Started", "payload": payload},
            }
        ]
    )


def page_echo(sender_id: str = PAGE_ID) -> dict[str, Any]:
    """A message the Page itself sent, echoed back.

    The bot must ignore these. Replying to its own echo is an infinite loop.
    """
    return _envelope(
        [
            {
                "sender": {"id": sender_id},
                "recipient": {"id": "USER_PSID"},
                "timestamp": 1764547200000,
                "message": {
                    "mid": "m_echo1",
                    "text": "Thanks for your question!",
                    "is_echo": True,
                    "app_id": 123456789,
                },
            }
        ]
    )
```

- [ ] **Step 2: Verify the fixtures import cleanly**

Run: `uv run python -c "from tests.fixtures.meta_payloads import page_text_message, page_echo, VERIFY_QUERY; print(page_text_message()['entry'][0]['messaging'][0]['message']['mid']); print(page_echo()['entry'][0]['messaging'][0]['message']['is_echo']); print(VERIFY_QUERY['hub.mode'])"`
Expected: prints `m_AG5Hz2U`, then `True`, then `subscribe`

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check tests/fixtures
uv run ruff format tests/fixtures
git add tests/fixtures
git commit -m "test: add meta webhook payload fixtures from the published contract"
```

---

### Task 3: Cached BotService dependency

**Files:**
- Create: `app/dependencies/services.py`
- Test: `tests/test_service_dependencies.py`

**Interfaces:**
- Consumes: `BotService` from `app.services.bot_service`; `SUPABASE_URL` and `SUPABASE_SECRET_KEY` from `app.config`.
- Produces: `get_bot_service() -> BotService`, cached for the process lifetime; `_build_supabase_client()` returning a Supabase client or `None`; and `reset_bot_service() -> None` for tests.

**The Supabase client is load-bearing, not optional.** `BotService(None)` gives `KnowledgeService.supabase = None`, and Task 4's fail-closed `similarity_search` raises immediately on that. A provider that passes `None` therefore produces a bot that escalates every single question. `app/routers/bot.py:62` already builds the client correctly; this provider must do the same, and Task 5 deletes that duplicate.

`KnowledgeService._get_embedding_model` issues a live `embed_content` call on construction (`app/services/knowledge_service.py:86`). `app/routers/messenger.py:112` constructs `BotService()` per inbound message, so every message burned one embedding call against a 15 RPM free tier before doing any work.

- [ ] **Step 1: Write the failing test**

Create `tests/test_service_dependencies.py`:

```python
"""The bot service must be constructed once per process, not per message."""

from unittest.mock import MagicMock, patch

from app.dependencies.services import (
    _build_supabase_client,
    get_bot_service,
    reset_bot_service,
)


class TestGetBotService:
    def setup_method(self):
        reset_bot_service()

    def teardown_method(self):
        reset_bot_service()

    def test_constructs_the_service_only_once_across_many_calls(self):
        with patch("app.dependencies.services.BotService") as mock_cls:
            mock_cls.return_value = MagicMock()
            first = get_bot_service()
            for _ in range(10):
                get_bot_service()
            assert mock_cls.call_count == 1
            assert get_bot_service() is first

    def test_reset_forces_a_fresh_construction(self):
        with patch("app.dependencies.services.BotService") as mock_cls:
            mock_cls.side_effect = [MagicMock(), MagicMock()]
            first = get_bot_service()
            reset_bot_service()
            second = get_bot_service()
            assert mock_cls.call_count == 2
            assert first is not second

    def test_passes_the_supabase_client_through_to_the_bot_service(self):
        # A BotService built with None has no vector store, so Task 4's
        # fail-closed retrieval raises on every question. This is the
        # regression test for that.
        fake_client = object()
        with (
            patch("app.dependencies.services.BotService") as mock_cls,
            patch(
                "app.dependencies.services._build_supabase_client",
                return_value=fake_client,
            ),
        ):
            get_bot_service()
            mock_cls.assert_called_once_with(fake_client)


class TestBuildSupabaseClient:
    def test_returns_none_when_credentials_are_absent(self):
        with (
            patch("app.dependencies.services.SUPABASE_URL", ""),
            patch("app.dependencies.services.SUPABASE_SECRET_KEY", ""),
        ):
            assert _build_supabase_client() is None

    def test_returns_none_when_client_construction_raises(self):
        with (
            patch("app.dependencies.services.SUPABASE_URL", "https://example.supabase.co"),
            patch("app.dependencies.services.SUPABASE_SECRET_KEY", "secret"),
            patch(
                "supabase.create_client",
                side_effect=RuntimeError("boom"),
            ),
        ):
            assert _build_supabase_client() is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_service_dependencies.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.dependencies.services'`

- [ ] **Step 3: Write the implementation**

Create `app/dependencies/services.py`:

```python
"""Process-lifetime service providers.

BotService construction reaches out to Gemini to verify the embedding model, so
building one per request wastes a call from a 15 RPM free tier on every inbound
message. lru_cache makes construction happen once.
"""

from functools import lru_cache

from app.config import SUPABASE_SECRET_KEY, SUPABASE_URL
from app.services.bot_service import BotService
from app.utils.logging_config import get_api_logger

logger = get_api_logger()


def _build_supabase_client():
    """Return a Supabase client, or None when one cannot be built.

    Without this client KnowledgeService has no vector store, so fail-closed
    retrieval raises on every question and the bot escalates everything.
    Passing None here silently disables the knowledge base, so the None paths
    below log loudly.
    """
    if not (SUPABASE_URL and SUPABASE_SECRET_KEY):
        logger.warning("Supabase credentials missing; knowledge base unavailable")
        return None
    try:
        from supabase import create_client

        client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
        logger.info("Bot service initialized with Supabase client")
        return client
    except Exception as exc:
        logger.error(f"Supabase client init failed; knowledge base unavailable: {exc}")
        return None


@lru_cache(maxsize=1)
def get_bot_service() -> BotService:
    """Return the shared BotService, constructing it on first use."""
    return BotService(_build_supabase_client())


def reset_bot_service() -> None:
    """Drop the cached instance. Tests use this to isolate from each other."""
    get_bot_service.cache_clear()
```

The config constants are imported at module level rather than inside the function, unlike `app/routers/bot.py:62`, so tests can patch them.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_service_dependencies.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app/dependencies/services.py tests/test_service_dependencies.py
uv run ruff format app/dependencies/services.py tests/test_service_dependencies.py
git add app/dependencies/services.py tests/test_service_dependencies.py
git commit -m "perf: construct botservice once per process instead of per message"
```

---

### Task 4: Fail closed instead of hashing

**Files:**
- Modify: `app/services/knowledge_service.py` (delete `_create_fallback_embeddings`, lines 199-216; change `create_embeddings`, `similarity_search`, and the `chat_model` branches)
- Test: `tests/test_knowledge_service_failure.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `KnowledgeService.create_embeddings` raises `EmbeddingsUnavailable` when Gemini cannot embed; `KnowledgeService.similarity_search` propagates it. `EmbeddingsUnavailable` is exported from `app.services.knowledge_service`.

`knowledge_service.py:199` currently returns MD5-derived vectors when Gemini is unavailable. Those are not semantic, so `match_documents` returns effectively random chunks above threshold and the bot answers confidently from them. Silence is recoverable; a confident wrong answer to a prospective volunteer is not.

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_service_failure.py`:

```python
"""When embeddings are unavailable the service must refuse, not improvise."""

import pytest

from app.services.knowledge_service import EmbeddingsUnavailable, KnowledgeService


def _service_without_embeddings() -> KnowledgeService:
    service = KnowledgeService.__new__(KnowledgeService)
    service.supabase = None
    service.gemini_client = None
    service.embedding_model = None
    return service


class TestFailsClosed:
    @pytest.mark.asyncio
    async def test_create_embeddings_raises_when_the_model_is_unavailable(self):
        service = _service_without_embeddings()
        with pytest.raises(EmbeddingsUnavailable):
            await service.create_embeddings(["some volunteer question"])

    @pytest.mark.asyncio
    async def test_similarity_search_raises_when_the_model_is_unavailable(self):
        service = _service_without_embeddings()
        with pytest.raises(EmbeddingsUnavailable):
            await service.similarity_search("how do I volunteer")

    def test_no_hash_based_fallback_remains(self):
        assert not hasattr(KnowledgeService, "_create_fallback_embeddings")

    def test_is_available_is_false_without_embeddings(self):
        assert _service_without_embeddings().is_available() is False
```

`pytest-asyncio` is not currently a dependency. Add it in the same commit:

```bash
uv add --dev "pytest-asyncio>=0.24,<0.26"
```

Then append to `tests/pytest.ini`:

```ini
asyncio_mode = auto
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_knowledge_service_failure.py -v`
Expected: FAIL, `ImportError: cannot import name 'EmbeddingsUnavailable'`

- [ ] **Step 3: Write the implementation**

In `app/services/knowledge_service.py`, add the exception immediately after the `EMBEDDING_DIMENSIONS` constant:

```python
class EmbeddingsUnavailable(RuntimeError):
    """Raised when Gemini cannot produce embeddings.

    Callers must escalate to a human rather than answer. The previous
    hash-based fallback returned non-semantic vectors, which made
    match_documents surface arbitrary chunks that then read as confident
    answers.
    """
```

Delete the entire `_create_fallback_embeddings` method (lines 199-216).

Replace the body of `create_embeddings` with:

```python
    async def create_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Create 768-dimensional embeddings, or raise if that is impossible."""
        if not self.embedding_model or self.embedding_model == "chat_model":
            raise EmbeddingsUnavailable(
                "Gemini embedding model is not available; refusing to answer"
            )
        if not texts:
            return []

        logger.info(f"Creating embeddings for {len(texts)} text chunks using Gemini")
        embeddings: list[list[float]] = []
        for index, text in enumerate(texts):
            try:
                result = self.gemini_client.models.embed_content(
                    model=self.embedding_model,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type="RETRIEVAL_DOCUMENT",
                        output_dimensionality=EMBEDDING_DIMENSIONS,
                    ),
                )
            except Exception as exc:
                raise EmbeddingsUnavailable(
                    f"Embedding call failed for chunk {index + 1}: {exc}"
                ) from exc

            vector = result.embeddings[0].values if result.embeddings else None
            if not vector:
                raise EmbeddingsUnavailable(f"Empty embedding returned for chunk {index + 1}")
            embeddings.append(_l2_normalize(vector))

        logger.info(f"Successfully created {len(embeddings)} embeddings")
        return embeddings
```

Replace the body of `similarity_search` with:

```python
    async def similarity_search(
        self, query: str, limit: int = 3, threshold: float = 0.3
    ) -> list[dict[str, Any]]:
        """Find similar chunks, or raise if retrieval cannot be trusted."""
        if not self.supabase:
            raise EmbeddingsUnavailable("Supabase is not configured; refusing to answer")
        if not self.embedding_model or self.embedding_model == "chat_model":
            raise EmbeddingsUnavailable(
                "Gemini embedding model is not available; refusing to answer"
            )

        try:
            result = self.gemini_client.models.embed_content(
                model=self.embedding_model,
                contents=query,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY",
                    output_dimensionality=EMBEDDING_DIMENSIONS,
                ),
            )
            query_embedding = result.embeddings[0].values if result.embeddings else None
        except Exception as exc:
            raise EmbeddingsUnavailable(f"Query embedding failed: {exc}") from exc

        if not query_embedding:
            raise EmbeddingsUnavailable("Empty query embedding returned")

        try:
            matches = self.supabase.rpc(
                "match_documents",
                {
                    "query_embedding": _l2_normalize(query_embedding),
                    "match_threshold": threshold,
                    "match_count": limit,
                },
            ).execute()
        except Exception as exc:
            raise EmbeddingsUnavailable(f"Vector search failed: {exc}") from exc

        found = matches.data or []
        logger.info(f"Vector similarity search found {len(found)} results")
        return found
```

Delete the now-unreachable `_fallback_text_search` method entirely. It contained dead f-string SQL construction and unreachable code after its own `return`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_knowledge_service_failure.py -v`
Expected: PASS, 4 passed

Run: `uv run pytest -v`
Expected: PASS. If any existing test asserted on hash-fallback behaviour, it was asserting a defect; delete that test and note it in the commit body.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app/services/knowledge_service.py tests/test_knowledge_service_failure.py
uv run ruff format app/services/knowledge_service.py tests/test_knowledge_service_failure.py
git add app/services/knowledge_service.py tests/test_knowledge_service_failure.py tests/pytest.ini pyproject.toml uv.lock
git commit -m "fix: fail closed when embeddings are unavailable

MD5-derived vectors are not semantic, so match_documents returned arbitrary
chunks above threshold and the bot answered confidently from them. Refusing
is recoverable; a confident wrong answer to a prospective volunteer is not."
```

---

### Task 5: Replace the webhook router and wire it up

**Files:**
- Create: `app/routers/webhooks.py`
- Create: `tests/test_webhooks.py`
- Modify: `app/main.py:31-32` and `app/main.py:131-137`
- Modify: `app/routers/bot.py:60-76` (delete its duplicate `get_bot_service`, import the shared one)
- Modify: `app/routers/public.py:304` (import the shared provider)
- Delete: `app/routers/messenger.py`, `tests/test_messenger.py`

**Interfaces:**
- Consumes: `verify_signature`, `resolve_challenge` (Task 1); fixtures (Task 2); `get_bot_service` (Task 3); `EmbeddingsUnavailable` (Task 4).
- Produces: `webhooks_router` mounted at `/webhook/meta`, and `_handle_message(sender_id: str, message: dict, bot_service: BotService) -> None`, which phase 1 replaces with the triage pipeline.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_webhooks.py`:

```python
"""Integration tests for the Meta webhook endpoint."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.meta_payloads import (
    VERIFY_QUERY,
    page_echo,
    page_postback,
    page_text_message,
)

APP_SECRET = "test_app_secret"
VERIFY_TOKEN = "test_verify_token"


def _signed(payload: dict) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


@pytest.fixture
def meta_config():
    with (
        patch("app.routers.webhooks.FACEBOOK_APP_SECRET", APP_SECRET),
        patch("app.routers.webhooks.FACEBOOK_VERIFY_TOKEN", VERIFY_TOKEN),
    ):
        yield


@pytest.fixture
def sender():
    mock = MagicMock()
    mock.send_text_message.return_value = True
    with patch("app.routers.webhooks.get_message_sender", return_value=mock):
        yield mock


@pytest.fixture
def bot():
    mock = MagicMock()
    mock.chat = AsyncMock(return_value={"response": "You can volunteer by signing up.", "confidence": 0.9})
    with patch("app.routers.webhooks.get_bot_service", return_value=mock):
        yield mock


class TestVerification:
    def test_echoes_the_challenge_for_a_correct_token(self, client: TestClient, meta_config):
        response = client.get("/webhook/meta", params=VERIFY_QUERY)
        assert response.status_code == 200
        assert response.text == VERIFY_QUERY["hub.challenge"]

    def test_echoes_a_non_numeric_challenge(self, client: TestClient, meta_config):
        query = {**VERIFY_QUERY, "hub.challenge": "a1b2-c3d4"}
        response = client.get("/webhook/meta", params=query)
        assert response.status_code == 200
        assert response.text == "a1b2-c3d4"

    def test_refuses_a_wrong_token(self, client: TestClient, meta_config):
        query = {**VERIFY_QUERY, "hub.verify_token": "wrong"}
        assert client.get("/webhook/meta", params=query).status_code == 403


class TestSignature:
    def test_rejects_an_unsigned_post(self, client: TestClient, meta_config, sender, bot):
        response = client.post("/webhook/meta", json=page_text_message())
        assert response.status_code == 403
        sender.send_text_message.assert_not_called()

    def test_rejects_a_tampered_body(self, client: TestClient, meta_config, sender, bot):
        _, headers = _signed(page_text_message())
        response = client.post(
            "/webhook/meta",
            content=json.dumps(page_text_message(text="different")).encode(),
            headers=headers,
        )
        assert response.status_code == 403
        sender.send_text_message.assert_not_called()


class TestDispatch:
    def test_answers_a_text_message(self, client: TestClient, meta_config, sender, bot):
        body, headers = _signed(page_text_message())
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        sender.send_text_message.assert_called_once_with(
            "USER_PSID", "You can volunteer by signing up."
        )

    def test_ignores_its_own_echo(self, client: TestClient, meta_config, sender, bot):
        body, headers = _signed(page_echo())
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        sender.send_text_message.assert_not_called()

    def test_acknowledges_a_postback_without_calling_the_bot(
        self, client: TestClient, meta_config, sender, bot
    ):
        body, headers = _signed(page_postback())
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        bot.chat.assert_not_called()

    def test_returns_200_for_an_unknown_object_type(self, client: TestClient, meta_config, sender, bot):
        body, headers = _signed({"object": "whatsapp_business_account", "entry": []})
        response = client.post("/webhook/meta", content=body, headers=headers)
        # Never 4xx: Meta disables webhooks after repeated failures.
        assert response.status_code == 200
        sender.send_text_message.assert_not_called()

    def test_stays_silent_when_the_bot_raises(self, client: TestClient, meta_config, sender, bot):
        from app.services.knowledge_service import EmbeddingsUnavailable

        bot.chat = AsyncMock(side_effect=EmbeddingsUnavailable("down"))
        body, headers = _signed(page_text_message())
        response = client.post("/webhook/meta", content=body, headers=headers)
        assert response.status_code == 200
        # Phase 1 replaces silence with a holding message plus an escalation.
        sender.send_text_message.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_webhooks.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.routers.webhooks'`

- [ ] **Step 3: Write the implementation**

Create `app/routers/webhooks.py`:

```python
"""Meta webhook endpoint for Messenger, and from phase 3 also Instagram.

One app, one URL, one signature scheme. Every response is 200 except a failed
signature or handshake, because Meta disables webhooks that repeatedly fail.
"""

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import (
    ENVIRONMENT,
    FACEBOOK_APP_SECRET,
    FACEBOOK_VERIFY_TOKEN,
)
from app.dependencies.services import get_bot_service
from app.services.channels.meta_signature import resolve_challenge, verify_signature
from app.services.messenger.message_sender import MessageSender
from app.services.messenger.mock_message_sender import MockMessageSender
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

webhooks_router = APIRouter(prefix="", tags=["webhooks"])


def get_message_sender():
    """Real sender in production, mock everywhere else."""
    if ENVIRONMENT in ("development", "test"):
        return MockMessageSender()
    return MessageSender()


@webhooks_router.get("/webhook/meta")
async def verify_webhook(
    mode: str | None = Query(None, alias="hub.mode"),
    verify_token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Meta's subscription handshake.

    The parameter names carry literal dots, so each needs an explicit alias.
    Binding them as plain identifiers silently never matches.
    """
    echo = resolve_challenge(FACEBOOK_VERIFY_TOKEN, mode, verify_token, challenge)
    if echo is None:
        logger.warning("Webhook verification refused")
        return PlainTextResponse("Verification failed", status_code=403)

    logger.info("Webhook verified successfully")
    return PlainTextResponse(echo, status_code=200)


@webhooks_router.post("/webhook/meta")
async def handle_webhook(request: Request) -> Response:
    """Receive and dispatch Meta messaging events."""
    raw_body = await request.body()
    if not verify_signature(
        FACEBOOK_APP_SECRET, raw_body, request.headers.get("X-Hub-Signature-256")
    ):
        logger.warning("Rejected webhook with an invalid signature")
        return PlainTextResponse("Invalid signature", status_code=403)

    try:
        body = await request.json()
    except ValueError:
        logger.warning("Rejected webhook with an unparseable body")
        return PlainTextResponse("EVENT_RECEIVED", status_code=200)

    if body.get("object") != "page":
        logger.info(f"Ignoring webhook object: {body.get('object')}")
        return PlainTextResponse("EVENT_RECEIVED", status_code=200)

    bot_service = get_bot_service()
    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            await _process_event(event, bot_service)

    return PlainTextResponse("EVENT_RECEIVED", status_code=200)


async def _process_event(event: dict, bot_service) -> None:
    """Route a single messaging event. Never raises."""
    try:
        sender_id = event.get("sender", {}).get("id")
        if not sender_id:
            return

        if "message" in event:
            await _handle_message(sender_id, event["message"], bot_service)
        elif "postback" in event:
            _handle_postback(sender_id, event["postback"])
    except Exception as exc:
        logger.error(f"Error processing messaging event: {exc}", exc_info=True)


async def _handle_message(sender_id: str, message: dict, bot_service) -> None:
    """Answer an inbound text message.

    Phase 1 replaces this with the triage pipeline, which turns the silent
    failure path below into a holding message plus an escalation.
    """
    if message.get("is_echo"):
        return
    text = message.get("text")
    if not text:
        logger.info(f"Ignoring non-text message from {sender_id}")
        return

    try:
        result = await bot_service.chat(text)
    except Exception as exc:
        logger.error(f"Bot service failed for {sender_id}: {exc}", exc_info=True)
        return

    if get_message_sender().send_text_message(sender_id, result["response"]):
        logger.info(f"Response sent to {sender_id}")
    else:
        logger.error(f"Failed to send response to {sender_id}")


def _handle_postback(sender_id: str, postback: dict) -> None:
    """Acknowledge a button or Get Started postback."""
    logger.info(f"Postback from {sender_id}: {postback.get('payload', '')}")
```

In `app/main.py`, replace the commented import at lines 31-32 with:

```python
from app.routers.webhooks import webhooks_router
```

and replace the commented `include_router` calls at lines 131-137 with:

```python
app.include_router(webhooks_router)
```

Leave `public_bot_router` and `bot_admin_router` commented out. They expose admin knowledge-base endpoints that phase 1 restructures, and wiring them now would mean wiring them twice.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_webhooks.py -v`
Expected: PASS, 10 passed

- [ ] **Step 4b: Collapse the duplicate bot service provider**

`app/routers/bot.py:60-76` defines its own `@lru_cache`-wrapped `get_bot_service`, and `app/routers/public.py:304` imports it inside the health-check endpoint. Two providers means two `BotService` singletons and two cold-start Gemini verification calls. Delete the one in `bot.py`.

In `app/routers/bot.py`, delete the entire `get_bot_service` function (the `@lru_cache` decorator through `return BotService(None)`) and add to the imports:

```python
from app.dependencies.services import get_bot_service
```

Remove the now-unused `from functools import lru_cache` import if nothing else in the file uses it.

In `app/routers/public.py`, change line 304 from:

```python
            from app.routers.bot import get_bot_service
```

to:

```python
            from app.dependencies.services import get_bot_service
```

Run: `uv run pytest -v`
Expected: PASS. The health-check endpoint in `public.py` still resolves a bot service, now the shared one.

- [ ] **Step 5: Delete the superseded module and its tests**

```bash
git rm app/routers/messenger.py tests/test_messenger.py
uv run pytest -v
```

Expected: PASS across the whole suite. `tests/test_faq_handling.py` remains skipped at line 17; phase 1 rewrites it against the triage pipeline.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check app tests
uv run ruff format app tests
git add app/routers/webhooks.py app/main.py tests/test_webhooks.py
git commit -m "fix: repair and wire up the meta webhook endpoint

hub.mode, hub.verify_token, and hub.challenge carry literal dots, so the old
plain-identifier parameters never bound and verification could not succeed.
The old tests asserted the same wrong contract, so the suite stayed green over
a handshake that never worked. Adds X-Hub-Signature-256 verification, which
FACEBOOK_APP_SECRET was read for but never used, echoes the challenge as
opaque text rather than coercing it to an int, and ignores is_echo events."
```

---

## Self-Review

**Spec coverage for phase 0.** The spec lists phase 0 as: fix `hub.*` binding (Task 5), HMAC verification (Tasks 1 and 5), `BotService` singleton (Task 3), deduplication, and wire the routers (Task 5). Fail-closed behaviour is D9 (Task 4).

**One deliberate deferral.** Deduplication on `provider_message_id` is listed under phase 0 in the spec but needs the `messages` table, which phase 1 creates. Implementing it in phase 0 would mean a throwaway in-memory cache that a Cloud Run instance restart empties. It is therefore moved to phase 1, Task 1, where the unique index makes it durable. Until then a Meta retry can produce a duplicate reply. That is acceptable only because the endpoint is not yet subscribed to a live Page: phase 0 ends with the bot testable but not receiving public traffic.

**Type consistency.** `verify_signature` and `resolve_challenge` keep identical signatures across Tasks 1 and 5. `get_bot_service` is defined in Task 3 and imported in Task 5. `EmbeddingsUnavailable` is defined in Task 4 and imported in the Task 5 test. Fixture function names match between Tasks 2 and 5.

**Known gap carried into phase 1.** `_handle_message` currently fails silently when the bot raises. The test asserts that silence deliberately, and phase 1 replaces it with a holding message plus an escalation.

---

## Carried into phase 1

Found by the whole-branch review after phase 0's fix wave, and deliberately not fixed here.

1. **`_build_context` can still yield an ungrounded answer.**
   `app/services/bot_service.py` `_build_context` swallows exceptions and returns `""`, and it also returns `""` with no exception at all when every retrieved chunk has blank or missing `content`.
   `chat()` then sends Gemini an empty context block and returns a dict carrying the real, high top-similarity score, which is exactly the ungrounded confident answer D9 exists to prevent.
   Normal ingestion strips blank chunks in `document_service.split_into_chunks`, so it is not reachable today through the supported path.
   It matters because `match_documents` is a Supabase-side RPC with no definition in this repository, so its returned column names are an untracked external contract: if it ever returns the text under a key other than `content`, every answer silently becomes ungrounded with no log line.
   Phase 1 fix: raise rather than returning `""`, and treat an empty assembled context as `NoRelevantContext`.

   Phase 0 therefore satisfies D9 on the retrieval-unavailable and generation-unavailable paths, not yet on the empty-context path.
   Any claim that D9 holds end to end is premature until the above is done.

2. **Blocking I/O on the event loop.**
   `Dockerfile` runs uvicorn with no `--workers`, so there is one process and one event loop, and `handle_webhook` awaits work that is synchronous underneath: the Gemini SDK's `generate_content`, the Supabase `rpc().execute()`, and `requests.post` in `MessageSender`.
   One inbound message blocks the whole instance for the full round trip, and the endpoint only acknowledges after processing every event in a delivery.
   Meta's webhook timeout is roughly 20 seconds and a timeout counts as a failure, which is the outcome this design exists to avoid.
   This is safe only while no Page is subscribed.
   **Treat it as a prerequisite before subscribing a live Page**, not as an optimisation.

3. **Degradation is sticky.**
   `KnowledgeService.__init__` probes Gemini exactly once, and `get_bot_service` is `lru_cache`d for the process lifetime, so a transient Gemini outage at construction pins `embedding_model = None` until the instance restarts.
   The post-deploy smoke check against `/health` is what triggers construction, so one Gemini call at deploy time decides the instance's fate.

4. **`get_client_ip` trusts the caller.**
   `app/utils/request_helpers.py` takes the first `X-Forwarded-For` entry verbatim, which on Cloud Run is caller-supplied.
   Impact on the webhook is negligible because HMAC rejects forgeries first, but it makes the `/auth` limit of 10 attempts per hour bypassable by rotating the header, and lets an attacker grow `request_counts` on keys of their choosing.
   Pre-existing and untouched by this branch; deserves its own ticket.

5. **`/health` cannot see a broken chat model.**
   `KnowledgeService.is_available()` is true when the embedding model and the Supabase client both exist, and `GET /health` derives its `bot_service` entry from exactly that.
   `CHAT_MODEL` is never probed there or anywhere else in the codebase, so an instance whose `generate_content` call fails on every request still reports the bot healthy while it answers nobody.
   Accepted for phase 0 for two reasons.
   A second construction-time probe would deepen the sticky degradation already recorded as item 3, since it would pin a second capability off for the process lifetime on one transient failure.
   It would also spend a second live Gemini call against a 15 requests-per-minute free tier at every construction, which is the same per-construction cost the shared `BotService` provider exists to cut down.
   Phase 1 fix: surface a non-generating bot through the escalation path, where `GenerationUnavailable` already lands, rather than through a health flag.

## Remaining plan sequence

Each of these gets its own plan document and produces working software on its own.

| Plan | Contents | Depends on |
|---|---|---|
| Phase 1 | `conversations` and `messages` tables plus migration, deduplication, `ConversationService`, `TriageService` via LiteLLM, confidence gating, `Notifier` interface with email and Discord, admin conversations view, rewrite of `test_faq_handling.py` | Phase 0 |
| Phase 2 | Email adapter adapted from mailhub, loop guard, bulk filtering, IMAP poll on the existing cron | Phase 1 |
| Phase 3 | Instagram adapter on the same endpoint, App Review submission assets | Phase 1 |
| Phase 4 | `evals/golden_qa.yaml`, judge runner, threshold calibration | Phase 1 |
| Phase 5 | ManyChat cutover. Operational, no code. | Phases 3 and 4 |
