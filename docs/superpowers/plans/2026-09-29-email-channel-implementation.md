# Email Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the volunteer inbox into a second channel on the shared answer engine: every new mail labelled and, where answerable, drafted in its thread; escalations forwarded and posted to Discord; and after an evaluation gate, sign-up and FAQ replies sent automatically under caps and a kill switch.

**Architecture:** A `GmailAdapter` over a `MailTransport` seam feeds `IncomingMessage` into the engine the parent spec designs (dedupe, `ConversationService`, `TriageClassifier`, category gate, retrieval and confidence gate, `Notifier`, audit), which this plan builds because Messenger phase 1 has not landed. A delivery mode gate (`off`, `draft`, `auto`) is the only email-specific concept above the transport, and sending does not exist in the codebase until phase E3.

**Tech Stack:** Python 3.12, FastAPI 0.116, SQLAlchemy 2.x, Alembic, pytest, uv, ruff. `google-api-python-client` and `google-auth-oauthlib` are already runtime dependencies. New runtime dependencies, both in E1: `litellm` (parent spec D7) and `typesafe-sdk` (Jev).

**Spec:** `docs/superpowers/specs/2026-09-29-email-channel-design.md` (the "design" below), which amends `docs/superpowers/specs/2026-07-28-qna-bot-design.md` (the "parent spec").

## How to read this plan

This document fixes the phase boundaries, the files, the interfaces every phase shares, the tests, the acceptance criteria and the captain actions each phase depends on.
Each build phase is expanded into its own task-by-task plan, with failing tests written first, in the style of `docs/superpowers/plans/2026-07-29-phase-0-messenger-hardening.md`, immediately before that phase is executed.
Names defined under "Shared interfaces" are the contract between phases; a later phase must use them as written.

## Global Constraints

- Python `>=3.12`. Run everything through uv: `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`.
- The test environment is the `Run tests` step of `.github/workflows/test.yml`; CI enforces `ruff check`, `ruff format --check` and `--cov-fail-under`.
- Runtime dependency additions are limited to `litellm` and `typesafe-sdk`, both in E1. Nothing else.
- Commit messages follow `<type>: <description>`, imperative, lowercase, no trailing period. Never add a co-author or attribution trailer, and never reference the tool that produced a change.
- No em dash characters in code, comments, docs, templates or commit messages. Use a plain hyphen.
- The bot only ever replies to an inbound message. No outbound-initiated mail in any phase.
- Before E3, no code path sends mail to a member of the public. The only outbound mail is the escalation forward to `ESCALATION_OWNER_EMAIL`, and that is enforced by the absence of a send method on the transport, not by a flag.
- Live tests run against a throwaway Gmail test account and a throwaway sender account, never the real inbox. The real inbox is touched only by the captain's own consent step in E0 and by production.
- No volunteer's name, address or message text appears in fixtures, golden cases, docs or commit messages. Recorded payloads are anonymised before they are committed.
- Log lines and exception messages carry Gmail ids, categories, tiers, languages and actions. Never a subject, a body or an address.
- Every setting the design lists is created by `initialize_default_settings()` with the default the design gives, and read at the start of each run, never cached for the process lifetime.
- Cadence for every scheduler job is owned by its `CRON_*` setting and applied by `POST /admin/sync-cron-schedules`; the deploy script sends the schedule only on the create path, as it does today.

## Review Focus

Inputs the design implies but no phase's headline tests exercise, most likely to bite first.
Each line names the phase and test file that pins it.

1. **A mail with no `text/plain` part, or an empty body.** Expected: the adapter extracts text from `text/html`, and an empty result is `needs_admin` with the holding message, never a crashed run. Pinned in E1, `tests/test_gmail_adapter.py`.
2. **Two inbound mails from the same sender in the same thread before the first poll.** Expected: one reply, both inbound rows recorded, the second marked `paused` with no second draft. Pinned in E1, `tests/test_email_bot_pipeline.py`.
3. **A mail from the escalation owner or an `ADMIN_EMAILS` address.** Expected: skipped and labelled, never triaged and never answered. Pinned in E1, `tests/test_mail_guards.py`.
4. **A Vietnamese sign-up mail in `auto` mode while `EMAIL_BOT_AUTO_LANGUAGES` is `en`.** Expected: drafted, not sent, and the run counts it as drafted. Pinned in E3, `tests/test_email_bot_delivery.py`.
5. **A scheduler retry while the previous run is still executing.** Expected: the second request returns 200 with `status: already_running` and does nothing; a run older than 30 minutes with no `finished_at` is treated as dead. Pinned in E1, `tests/test_email_bot_endpoint.py`.

---

## Phase overview

| Phase | Deliverable | Depends on | Captain actions | Estimate |
|---|---|---|---|---|
| E0 | Consent script, setup runbook, secrets in place, test accounts, samples | nothing | OAuth client and consent, Secret Manager, Discord webhook, throwaway accounts, anonymised samples, knowledge-base doc, Jev key | captain 1 to 2 hours, engineer 0.5 day |
| E1 | Classify, label and draft; shared engine; escalation forward and Discord; nothing sent | E0 | Review drafts in Gmail; keep `EMAIL_BOT_MODE=draft` | 2.5 to 3 weeks |
| E2 | Golden set, judge runner, calibration, draft-acceptance metric, Jev versus shadow report | E1 in draft mode for two weeks | Send or edit drafts for two weeks; native speaker reviews the Vietnamese cases | 1 week, plus two calendar weeks of draft mode |
| E3 | Automatic sign-up and FAQ replies, caps, canary, loop test | E2 gate passed | Sign-off on holding-message copy; VI sign-up copy sign-off or `auto` in English only; approve the flip to `auto`; daily audit review during the canary | 1 week, plus one canary week |
| E4 | Daily knowledge-base sync and editor guide, weekly groundedness sampling, `invalid_grant` alert and re-consent runbook, dashboard polish | E1 (sync job), E3 (sampling) | Nominate coordinators as editors; curate the doc | 1 week |

## Shared interfaces

Defined in E1, used unchanged by E2 to E4.

```python
# app/services/channels/base.py
@dataclass(frozen=True)
class IncomingMessage:
    channel: str                      # "email"
    thread_key: str                   # Gmail threadId
    provider_message_id: str          # Gmail message id, the idempotence key
    sender_key: str                   # sha256 of the lower-cased sender address
    sender_address: str               # in memory only; never persisted, never logged
    subject: str
    text: str
    rfc_message_id: str
    references: tuple[str, ...]
    received_at: datetime
    headers: Mapping[str, str]        # lower-cased header names
    label_ids: tuple[str, ...]

@dataclass(frozen=True)
class OutboundReply:
    thread_key: str
    to_address: str
    subject: str
    in_reply_to: str
    references: tuple[str, ...]
    text: str
    language: str                     # "en" | "vi"
    kind: str                         # "signup" | "faq" | "holding"

# app/services/triage/protocol.py
Category = Literal["signup", "faq", "volunteer_ops", "sponsorship", "donation",
                   "partnership", "press", "safeguarding_legal", "acceptance",
                   "human_other", "automated"]
Tier = Literal["auto_answer", "needs_admin", "needs_executive", "skip"]
Language = Literal["en", "vi", "other"]

@dataclass(frozen=True)
class TriageSignals:
    category: Category
    language: Language
    confidence: float                 # 0.0 to 1.0
    asks_for_human: bool
    mentions_money_or_commitment: bool
    classifier: str                   # "jev" | "litellm:<model>"

@dataclass(frozen=True)
class TriageDecision(TriageSignals):
    tier: Tier

class TriageClassifier(Protocol):
    def classify(self, message: IncomingMessage) -> TriageSignals: ...

# app/services/triage/policy.py
EXECUTIVE_CATEGORIES: frozenset[Category]
def derive_tier(signals: TriageSignals, confidence_threshold: float) -> Tier: ...
def decide(signals: TriageSignals, confidence_threshold: float) -> TriageDecision: ...

# app/services/channels/gmail_transport.py
@dataclass(frozen=True)
class RawMail:
    id: str
    thread_id: str
    label_ids: tuple[str, ...]
    payload: Mapping[str, Any]        # users.messages.get(format="full") resource

class GmailAuthRevoked(RuntimeError): ...   # raised on invalid_grant (E4 wires the handling)

class MailTransport(Protocol):
    inbox_address: str
    def list_unprocessed(self, *, newer_than_days: int, exclude_label: str, limit: int) -> list[RawMail]: ...
    def get_thread(self, thread_id: str) -> list[RawMail]: ...
    def ensure_labels(self, names: Iterable[str]) -> dict[str, str]: ...   # name -> id
    def add_labels(self, message_id: str, label_ids: Iterable[str]) -> None: ...
    def create_draft(self, thread_id: str, mime: bytes) -> str: ...         # draft id
    def get_draft(self, draft_id: str) -> RawMail | None: ...
    def delete_draft(self, draft_id: str) -> None: ...
    # E3 adds, and only E3 may call:
    # def send_reply(self, thread_id: str, mime: bytes) -> str: ...          # sent message id

# app/services/conversation_service.py
class ConversationService:
    def __init__(self, db: Session) -> None: ...
    def is_duplicate(self, provider_message_id: str) -> bool: ...
    def get_or_create(self, channel: str, thread_key: str, sender_key: str) -> Conversation: ...
    def record_inbound(self, conversation: Conversation, message: IncomingMessage,
                       decision: TriageDecision, shadow: TriageSignals | None) -> Message: ...
    def record_outbound(self, conversation: Conversation, *, text: str, action: str,
                        language: str, gmail_message_id_out: str | None = None,
                        gmail_draft_id: str | None = None, confidence: float | None = None,
                        sources: list[str] | None = None) -> Message: ...
    def record_action(self, conversation: Conversation, message: IncomingMessage, action: str) -> Message: ...
    def can_bot_reply(self, conversation: Conversation) -> bool: ...   # status == "bot" and bot_reply_count < 1
    def pause(self, conversation: Conversation, tier: Tier, reason: str) -> None: ...
    def pause_manual(self, conversation: Conversation) -> None: ...
    def resume(self, conversation: Conversation) -> None: ...
    def outstanding_draft(self, conversation: Conversation) -> Message | None: ...

# app/services/notifier.py
@dataclass(frozen=True)
class EscalationEvent:
    message: IncomingMessage
    decision: TriageDecision
    reason: str
    summary: str | None
    gmail_thread_url: str

class Notifier(Protocol):
    def notify(self, event: EscalationEvent) -> None: ...

# app/services/email_bot/delivery.py
class DeliveryMode(StrEnum):
    OFF = "off"; DRAFT = "draft"; AUTO = "auto"
def parse_mode(value: str | None) -> DeliveryMode: ...   # anything unknown -> OFF

@dataclass(frozen=True)
class DeliveryResult:
    action: str                       # "drafted" | "sent"
    gmail_draft_id: str | None
    gmail_message_id_out: str | None

class ReplySink(Protocol):
    def deliver(self, reply: OutboundReply) -> DeliveryResult: ...

# app/services/email_bot/pipeline.py
@dataclass
class RunSummary:
    mode: DeliveryMode
    listed: int; processed: int; drafted: int; sent: int
    forwarded: int; skipped: int; errors: int
    aborted_reason: str | None

class EmailBotPipeline:
    def run(self) -> RunSummary: ...
```

---

## Phase E0: Captain setup and runbook

**Goal:** The bot's Gmail grant, the Discord webhook, the Jev key, the test accounts, the knowledge-base doc and the sample corpus exist, and the runbook that produced them is committed so the setup can be repeated.

**Depends on captain actions:**

1. In the existing Google Cloud project, enable the Gmail API and create a new OAuth client of type Desktop, separate from the web client in issue #7. Rotate that leaked secret in the same sitting (issue #7).
2. Consent screen: External, published to production, not submitted for verification, one scope `https://www.googleapis.com/auth/gmail.modify`.
3. Run `scripts/gmail_oauth_consent.py` locally, signed in as the volunteer inbox, and copy the printed refresh token into Secret Manager alongside the client id and secret. Expose them to Cloud Run as `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN`.
4. Run the same script with `--check` on day 8. A token that still refreshes proves production status rather than Testing.
5. Create the Discord webhook in the channel that should receive escalations, store it as `DISCORD_WEBHOOK_URL`, and name the forward recipient for `ESCALATION_OWNER_EMAIL`.
6. Create a Jev key at the vendor console, store it as `TYPESAFE_API_KEY`.
7. Create two throwaway Gmail accounts: one to play the inbox under test, one to play the public sender. Run the consent script against the inbox one and keep its token in the engineer's local `.env` only.
8. Create the curated knowledge-base Google Doc, share it read-only with the runtime service account, and put its id in `KNOWLEDGE_BASE_DOC_ID`. Its first section carries the three rules in the design.
9. Provide the anonymised sample corpus the investigation asked for: about ten sign-up requests with at least three in Vietnamese, five other FAQ questions, two each of sponsorship, donation, partnership and press, three from existing volunteers about logistics, three automated messages with full headers, one multi-topic mail, and a rough monthly volume.

**Tasks:**

- [ ] **Task E0.1: Consent script**
  - Create: `scripts/gmail_oauth_consent.py`
  - Test: `tests/test_gmail_oauth_consent.py`
  - Interfaces: `run_consent(client_secrets_path: Path, scopes: list[str]) -> str` returning the refresh token; `check_token(client_id, client_secret, refresh_token) -> str` returning the granted address via `users.getProfile`. The script prints the token to stdout only and never writes it to disk.
  - Behaviour under test: refuses any scope other than `gmail.modify`; `--check` exits non-zero on `invalid_grant`.

- [ ] **Task E0.2: Setup runbook**
  - Create: `docs/GMAIL_BOT_SETUP.md`
  - Contents: the nine captain steps above with the exact console paths, the Secret Manager and Cloud Run variable names, the throwaway-account rule, how to revoke the grant, and a placeholder-free "Re-consent" section that E4 completes with the `invalid_grant` procedure.

- [ ] **Task E0.3: Configuration surface**
  - Modify: `app/config.py` (read `EMAIL_BOT_ENABLED`, `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN`, `TYPESAFE_API_KEY`, `DISCORD_WEBHOOK_URL`, `ANTHROPIC_API_KEY`; none joins `REQUIRED_ENV_VARS`, because the feature is off by default)
  - Modify: `env.template` (the same names, blank, with one-line comments)
  - Test: `tests/test_settings.py` extended: production validation still passes with all of them unset.

**Test strategy:** unit tests with the OAuth flow mocked; one live check by the captain on the real account (step 4) and by the engineer on the throwaway inbox.

**Acceptance criteria:**

- The captain's token obtained on day 0 refreshes on day 8.
- The Cloud Run service has the new variables set, verified with `gcloud run services describe`, and no value of any of them appears in a log line.
- The throwaway inbox has a working token in the engineer's local environment and the real inbox has never been touched by a test.
- The sample corpus is in the engineer's hands, anonymised.

---

## Phase E1: Classify, label and draft

**Goal:** Every new mail in the inbox is labelled within one poll; answerable mail has a reply waiting as a Gmail draft inside its thread; escalations are forwarded to the captain and posted to Discord; the shared engine exists; nothing is sent to a member of the public, by construction.

**Depends on captain actions:** E0 complete. The captain keeps `EMAIL_BOT_MODE=draft` and reviews drafts in Gmail as they appear; no other action.

**File structure:**

| File | Responsibility |
|---|---|
| `app/services/channels/base.py` | `IncomingMessage`, `OutboundReply`, `ChannelAdapter` protocol |
| `app/services/channels/mail_guards.py` | Pure guard functions: `is_automated(headers, label_ids) -> str | None` (the matching rule name), `is_internal_sender(address, inbox, owner, admins) -> bool`, `human_replied(thread: list[RawMail], inbox_address, bot_message_ids: set[str]) -> bool` |
| `app/services/channels/mail_builder.py` | Pure MIME: `build_reply(reply: OutboundReply, from_address, signature) -> bytes` with `In-Reply-To`, `References`, `Re:` subject, `Auto-Submitted: auto-replied`, `X-Auto-Response-Suppress: All`; `extract_text(payload) -> str` preferring `text/plain`, falling back to stripped `text/html` |
| `app/services/channels/gmail_transport.py` | `MailTransport`, `GmailTransport` built from the Discovery document with refreshable user credentials; the only Gmail I/O; no send method |
| `app/services/channels/gmail.py` | `GmailAdapter`: `list_new()`, `parse(raw) -> IncomingMessage`, `label(message_id, names)`, `draft(reply) -> str`, `delete_draft(draft_id)`, `thread_url(thread_id)` |
| `app/services/triage/protocol.py`, `policy.py`, `jev.py`, `litellm.py`, `shadow.py` | As in Shared interfaces; `policy.py` owns `EXECUTIVE_CATEGORIES` and the category-to-tier table |
| `app/services/conversation_service.py` | As in Shared interfaces |
| `app/services/notifier.py` | `EmailForwardNotifier(email_service, recipient)` over `EmailService.send_custom_email`, `DiscordNotifier(webhook_url, http)`, `CompositeNotifier([...])` |
| `app/services/email_bot/replies.py` | `render_signup_reply(language, settings) -> str`, `render_holding_message(language) -> str`, `localise_days(days, language)`, `format_class_time(start, end, language)` |
| `app/services/email_bot/summaries.py` | `summarise_for_escalation(text, language) -> str | None` through the existing Gemini client, with the no-names instruction |
| `app/services/email_bot/delivery.py` | `DeliveryMode`, `parse_mode`, `ReplySink`, `DraftSink(adapter)`, `choose_sink(mode, language, auto_languages, sinks) -> ReplySink | None` (E1 has only `DraftSink`; `auto` resolves to it) |
| `app/services/email_bot/pipeline.py` | `EmailBotPipeline` and `RunSummary`; the run lock; the circuit breaker; per-run cap |
| `app/services/email_bot/settings.py` | `EmailBotSettings.load(db) -> EmailBotSettings` reading every setting the design lists, per run |
| `app/routers/admin/email_bot.py` | `POST /admin/email-bot/poll`, `POST /admin/email-bot/sync-knowledge-base`, `GET /admin/email-bot/runs`, `GET /admin/email-bot/escalations`, `POST /admin/email-bot/conversations/{id}/resume` |
| `app/models.py` | `Conversation`, `Message`, `EmailBotRun` |
| `alembic/versions/<rev>_email_channel_tables.py` | The three tables, the unique index, the settings defaults |
| `app/services/settings_service.py` | Defaults for every setting in the design's Configuration table except the two E3 caps and `CRON_SYNC_KNOWLEDGE_BASE` |
| `app/services/cron_sync_service.py` | `CRON_POLL_INBOX` mapped to `poll-volunteer-inbox` |
| `scripts/create-or-update-scheduler-jobs.sh` | Job `poll-volunteer-inbox`, bootstrap `0 8,18 * * *`, `/admin/email-bot/poll`, attempt deadline 600s |
| `app/services/bot_service.py` | Empty context raises `NoRelevantContext`; refusal sentinel; email prompt prohibitions; sender language |
| `templates/email/bot/signup-reply.en.txt`, `signup-reply.vi.txt`, `holding.en.txt`, `holding.vi.txt` | The fixed copy, Jinja placeholders only for the settings-derived facts |
| `templates/web/admin/dashboard.html` | "Inbox bot" card: mode, last run, counters, open escalations |
| `pyproject.toml`, `uv.lock` | `litellm`, `typesafe-sdk` |

**Tasks:**

- [ ] **Task E1.1: Data model and migration**
  - Modify: `app/models.py`; Create: `alembic/versions/<rev>_email_channel_tables.py`; Modify: `app/services/settings_service.py`
  - Test: `tests/test_migrations.py` extended (upgrade and downgrade round trip, unique index on `messages.provider_message_id`, unique constraint on `conversations (channel, thread_key)`); `tests/test_settings.py` extended (every new default present with the design's value).

- [ ] **Task E1.2: Conversation service**
  - Create: `app/services/conversation_service.py`; Test: `tests/test_conversation_service.py`
  - Behaviour under test: dedupe on the Gmail id; `can_bot_reply` false after one bot reply; `pause_manual` is only undone by `resume`; `outstanding_draft` returns the last `drafted` row with a draft id and `draft_outcome == "pending"`; state transitions `bot -> paused_handoff -> bot`, `bot -> paused_manual -> bot`.

- [ ] **Task E1.3: Guards and MIME, pure**
  - Create: `app/services/channels/base.py`, `app/services/channels/mail_guards.py`, `app/services/channels/mail_builder.py`
  - Test: `tests/test_mail_guards.py` (table-driven over every rule in the design, the internal-sender rule, `human_replied` with and without bot-sent ids); `tests/test_mail_builder.py` (headers present, `References` chain preserved, `Re:` not doubled, UTF-8 Vietnamese body round-trips, HTML fallback extraction).

- [ ] **Task E1.4: Gmail transport and adapter**
  - Create: `app/services/channels/gmail_transport.py`, `app/services/channels/gmail.py`; Create: `tests/fixtures/gmail_payloads.py` (builders shaped to the published `users.messages.get` contract) and `tests/fixtures/gmail_recorded/` (anonymised real payloads captured from the throwaway inbox during the live test, committed as JSON)
  - Test: `tests/test_gmail_transport.py` (Discovery client mocked; listing query excludes the marker label and bounds by age; labels created once; draft created with `threadId`; **any access to `users().messages().send` fails the test**); `tests/test_gmail_adapter.py` (parse of every fixture, empty body, no `text/plain`, non-ASCII subject, missing `Message-ID`).

- [ ] **Task E1.5: Triage package**
  - Create: `app/services/triage/__init__.py`, `protocol.py`, `policy.py`, `jev.py`, `litellm.py`, `shadow.py`; Modify: `pyproject.toml`, `uv.lock`
  - Test: `tests/test_triage_policy.py` (table-driven: every category to its tier; money signal forces executive; asks-for-human forces admin; below-threshold forces admin; executive beats everything); `tests/test_triage_classifiers.py` (contract tests against a recorded Jev response and a recorded Gemini structured response committed under `tests/fixtures/triage_recorded/`; malformed responses raise rather than guess); `tests/test_triage_shadow.py` (decider's answer returned, both recorded, a shadow failure never affects the decision).

- [ ] **Task E1.6: Notifier**
  - Create: `app/services/notifier.py`; Test: `tests/test_notifier.py`
  - Behaviour under test: the forward is addressed to the configured recipient and nothing else, with the three-line header and the quoted original; the Discord payload contains category, tier, language, summary and thread link and **never** the sender address or body (asserted by substring); `safeguarding_legal` is prefixed `URGENT` with `@here`; a Discord failure does not raise past the composite.

- [ ] **Task E1.7: Replies and summaries**
  - Create: `app/services/email_bot/replies.py`, `app/services/email_bot/summaries.py`, the four templates under `templates/email/bot/`
  - Test: `tests/test_email_bot_replies.py` (English renders the captain's copy verbatim with days, time and link substituted; Vietnamese renders the proposed copy; day names localised; refuses to render while `VOLUNTEER_SIGNUP_FORM_LINK` is empty; holding message matches the parent spec's copy plus signature); `tests/test_email_bot_summaries.py` (Gemini mocked; failure returns `None`).

- [ ] **Task E1.8: BotService hardening**
  - Modify: `app/services/bot_service.py`; Test: `tests/test_bot_service_failure.py` extended
  - Behaviour under test: `_build_context` returning empty raises `NoRelevantContext` before generation (the phase-0 carry-over); the refusal sentinel in the generated text raises `NoRelevantContext`; the email prompt contains every prohibition in the design and the sender-language instruction; `chat()` still returns the top similarity as `confidence`.

- [ ] **Task E1.9: Delivery gate, settings loader and pipeline**
  - Create: `app/services/email_bot/delivery.py`, `app/services/email_bot/settings.py`, `app/services/email_bot/pipeline.py`
  - Test: `tests/test_email_bot_delivery.py` (`parse_mode` maps unknown to `off`; `off` yields no sink; `draft` and `auto` both yield `DraftSink` in this phase); `tests/test_email_bot_pipeline.py` with fakes for transport, classifier, bot service and notifier: guard skip; duplicate no-op; human-replied thread paused and the bot's own draft deleted; executive category drafts the holding message and escalates even at high similarity; FAQ below threshold escalates; sign-up drafts the template; `bot_reply_count` cap; per-run cap leaves the rest unlabelled; circuit breaker after three infrastructure failures; the marker label is applied last; Review Focus item 2.

- [ ] **Task E1.10: Endpoint, scheduler job, dashboard card**
  - Create: `app/routers/admin/email_bot.py`; Modify: `app/routers/admin/__init__.py`, `app/services/cron_sync_service.py`, `scripts/create-or-update-scheduler-jobs.sh`, `templates/web/admin/dashboard.html`
  - Test: `tests/test_email_bot_endpoint.py` (`EMAIL_BOT_ENABLED` false returns `disabled` and constructs nothing; `off` returns `off`; run lock, Review Focus item 5; `sync-knowledge-base` calls `BotService.sync_documents` with the configured doc id; resume endpoint); `tests/test_cron_sync_service.py` extended for the new mapping; `tests/test_admin_dashboard_template.py` extended for the card.

- [ ] **Task E1.11: Privacy and the rewritten FAQ test**
  - Create: `tests/test_email_bot_privacy.py` (runs the pipeline over the fixtures with `caplog` and asserts no `@` and no fixture body substring in any log record or in any exception message raised); Rewrite and un-skip: `tests/test_faq_handling.py` (drives the pipeline end to end with fixture mails through fakes: sign-up drafted, FAQ drafted, executive escalated, automated skipped, both languages).

- [ ] **Task E1.12: Knowledge base prerequisite**
  - Run `scripts/reembed_knowledge_base.py` against production once, then `POST /admin/email-bot/sync-knowledge-base`, and confirm `GET /health` reports documents indexed. Recorded in the runbook, not in code.

**Test strategy:**

- Unit: every pure module is tested without I/O, in the style of `tests/test_meta_signature.py`.
- Recorded fixtures: Gmail API JSON, a Jev response and a Gemini structured response are captured from the throwaway accounts, anonymised, committed, and used by every adapter and classifier test (the parent spec's remedy for defect 2).
- Live end-to-end, the acceptance test: seed the throwaway inbox with the sample corpus from the throwaway sender (helper `scripts/seed_test_inbox.py`, SMTP from the sender account), run `POST /admin/email-bot/poll` from a local instance pointed at the throwaway inbox in `draft` mode, and inspect labels, drafts, the forward and the Discord post in the real Gmail and Discord UIs. Capture the API payloads from this run for `tests/fixtures/gmail_recorded/`.

**Acceptance criteria:**

- On the throwaway inbox, every seeded mail carries `VH-Bot/Seen` and its outcome label after one poll; sign-up and FAQ mails have a draft inside their thread with correct `In-Reply-To`, `References` and RFC 3834 headers; executive mails have a holding-message draft, a forward in the owner's mailbox and a Discord post; automated mails are skipped with no draft.
- `grep -rn "messages().send\|\.send(" app/services/channels/` finds nothing, and the transport test that fails on send is green.
- Replying by hand from the throwaway inbox to a drafted thread, then polling again, pauses the thread and removes the bot's draft.
- The full suite is green, `tests/test_faq_handling.py` is un-skipped, and coverage does not drop below the CI threshold.
- Production runs in `draft` mode twice a day and the dashboard card shows each run.

---

## Phase E2: Evaluation and calibration

**Goal:** Decide, from evidence, which classifier decides in `auto`, what the two thresholds are, and whether the drafts are good enough to send unread.

**Depends on captain actions:** two calendar weeks of draft mode in which the captain sends bot drafts unchanged when they are right and edits or deletes them when they are not; a native Vietnamese speaker reviews the Vietnamese golden cases and the Vietnamese templates.

**File structure:**

| File | Responsibility |
|---|---|
| `evals/golden_qa.yaml` | 60 to 100 cases: `id`, `language`, `subject`, `text`, `headers` (for guard cases), `expected_category`, `expected_tier`, `must_mention`, `must_not_answer`; every category in both languages; the multi-topic rule; automated and bulk cases |
| `evals/loader.py` | Schema validation and typed loading |
| `evals/run_triage_eval.py` | `--classifier jev|litellm --model <string>`; per-category precision and recall, executive recall, language accuracy, confusion table; exits non-zero below 100 percent executive recall |
| `evals/run_groundedness_eval.py` | `BotService.chat()` over the `auto_answer` cases, judged against `must_mention` with the structured-output judge pattern from `tests/test_llm_judge.py` |
| `evals/calibrate_thresholds.py` | Sweeps `ANSWER_THRESHOLD` and `TRIAGE_CONFIDENCE_THRESHOLD`, prints the trade-off table |
| `evals/shadow_report.py` | Agreement between decided and shadow fields in `messages` over a date range; disagreement list by id and category only |
| `evals/README.md` | How to run each script, cost note, why none of it runs in CI |
| `app/services/email_bot/pipeline.py` | Draft reconciliation: each run checks every `pending` draft and sets `draft_outcome` |
| `app/routers/admin/email_bot.py`, `templates/web/admin/dashboard.html` | Draft-acceptance rate and shadow agreement on the card |

**Tasks:**

- [ ] **Task E2.1: Golden set and loader**
  - Create: `evals/golden_qa.yaml`, `evals/loader.py`; Test: `tests/test_evals_loader.py` (runs in CI, no API: ids unique; every category present in both languages; `expected_tier` equals `derive_tier` of `expected_category` with default signals; at least one multi-topic case; guard cases carry headers).

- [ ] **Task E2.2: Triage and groundedness runners**
  - Create: `evals/run_triage_eval.py`, `evals/run_groundedness_eval.py`, `evals/README.md`; Test: `tests/test_evals_runners.py` (metric arithmetic on a tiny synthetic result set; the non-zero exit on a missed executive case).

- [ ] **Task E2.3: Draft reconciliation and the acceptance metric**
  - Modify: `app/services/email_bot/pipeline.py`, `app/services/conversation_service.py`, `app/routers/admin/email_bot.py`, `templates/web/admin/dashboard.html`
  - Test: `tests/test_draft_reconciliation.py` (draft gone and a message from the inbox with identical text gives `sent_unchanged`; different text gives `sent_edited`; draft gone and no message gives `deleted`; draft present stays `pending`; whitespace and quoted-reply trimming are normalised before comparison).

- [ ] **Task E2.4: Calibration and shadow report**
  - Create: `evals/calibrate_thresholds.py`, `evals/shadow_report.py`; Test: `tests/test_evals_runners.py` extended (sweep output shape; agreement arithmetic).

- [ ] **Task E2.5: Run the evaluation and record the outcome**
  - Run both classifiers over the golden set, the groundedness judge, the calibration sweep, and the shadow report over the two draft-mode weeks. Record the numbers, the chosen thresholds and the classifier decision in a dated "Evaluation record" section appended to the design document. Set `TRIAGE_CLASSIFIER`, `ANSWER_THRESHOLD` and `TRIAGE_CONFIDENCE_THRESHOLD` on the dashboard accordingly.

**Test strategy:**

- Unit: loader validation and metric arithmetic run in CI without API calls.
- Recorded fixtures: the golden set itself is the fixture; the classifier contract tests from E1 already pin the response shapes.
- Live: the runners consume real API calls on demand; the draft-acceptance metric is measured on the production inbox in draft mode, which sends nothing.

**Acceptance criteria:**

- The golden set has at least 60 cases, every category in both languages, and passes its CI validation.
- The chosen decider shows 100 percent recall on `needs_executive` over the golden set, Vietnamese cases included.
- Groundedness on `auto_answer` cases is at least 90 percent.
- Over two weeks of draft mode: at least 90 percent of sign-up drafts and 80 percent of FAQ drafts were sent unchanged; zero bot drafts were created on threads a human had already answered.
- The evaluation record is in the design document with the date, the numbers and the settings chosen.

---

## Phase E3: Automatic replies for sign-up and FAQ

**Goal:** `EMAIL_BOT_MODE=auto` sends sign-up replies, confidence-gated FAQ answers and holding messages in-thread, under daily, per-sender and per-run caps, with RFC 3834 headers, after a one-week canary and a loop test.

**Depends on captain actions:** the E2 gate passed and recorded; sign-off on the holding-message copy in English and Vietnamese; sign-off on the Vietnamese sign-up copy, or the decision to run `auto` with `EMAIL_BOT_AUTO_LANGUAGES=en` until it lands; agreement on the two English typo fixes or the instruction to leave the copy verbatim; approval to flip the mode; a daily look at the audit table during the canary week.

**File structure:**

| File | Responsibility |
|---|---|
| `app/services/channels/gmail_transport.py` | `send_reply(thread_id, mime) -> str`, the first and only send method |
| `app/services/channels/gmail.py` | `send(reply) -> str` |
| `app/services/email_bot/caps.py` | `CapsState.load(db, now_vietnam, settings)`, `allows(sender_key) -> CapDecision`, `record_send(sender_key)`; counts `sent` rows for the Vietnam-local day |
| `app/services/email_bot/delivery.py` | `SendSink(adapter, caps)`; `choose_sink` honours mode, `EMAIL_BOT_AUTO_LANGUAGES` and caps, falling back to `DraftSink` when a cap is reached |
| `app/services/email_bot/pipeline.py` | Holding message sent in `auto`; `sent` counters; cap fallback recorded |
| `app/services/settings_service.py` | `EMAIL_BOT_DAILY_SEND_CAP`, `EMAIL_BOT_PER_SENDER_DAILY_CAP` |
| `templates/web/admin/dashboard.html` | Caps status and today's send count |
| `docs/GMAIL_BOT_SETUP.md` | Canary and loop-test procedure |

**Tasks:**

- [ ] **Task E3.1: Caps**
  - Create: `app/services/email_bot/caps.py`; Modify: `app/services/settings_service.py`; Test: `tests/test_email_bot_caps.py` (daily cap counted on the Vietnam-local day using the frozen clock from `tests/fixtures/clock.py`; per-sender cap by `sender_key`; a reached cap yields a draft decision, not a refusal).

- [ ] **Task E3.2: Send path**
  - Modify: `app/services/channels/gmail_transport.py`, `app/services/channels/gmail.py`, `app/services/email_bot/delivery.py`
  - Test: `tests/test_gmail_transport.py` extended (send carries `threadId`; the MIME carries `In-Reply-To`, `References`, `Auto-Submitted`, `X-Auto-Response-Suppress`); `tests/test_email_bot_delivery.py` extended (`off` and `draft` never reach `SendSink`, asserted by a transport mock whose `send_reply` raises; `auto` with a language outside `EMAIL_BOT_AUTO_LANGUAGES` drafts, Review Focus item 4; `auto` at cap drafts).

- [ ] **Task E3.3: Pipeline in auto**
  - Modify: `app/services/email_bot/pipeline.py`, `app/routers/admin/email_bot.py`, `templates/web/admin/dashboard.html`
  - Test: `tests/test_email_bot_pipeline.py` extended (in `auto`: sign-up sent, FAQ above threshold sent, FAQ below threshold gets a sent holding message and an escalation, executive gets a sent holding message and an escalation, second inbound on a replied thread is silent, a send failure leaves the thread unpaused and unlabelled `Sent`).

- [ ] **Task E3.4: Canary and loop test**
  - Modify: `docs/GMAIL_BOT_SETUP.md`
  - Loop test on the throwaway accounts: turn on the vacation responder on the throwaway sender, seed one sign-up mail, run two polls, and verify exactly one bot reply in the thread and the auto-reply labelled `VH-Bot/Skipped`.
  - Canary on production: `EMAIL_BOT_MODE=auto`, `EMAIL_BOT_DAILY_SEND_CAP=10`, `EMAIL_BOT_AUTO_LANGUAGES` per the sign-off, for one week; the captain reviews the audit table daily; then the cap goes to the design default.

**Test strategy:**

- Unit: caps, sink selection and the send MIME.
- Recorded fixtures: the vacation auto-reply captured in the loop test joins `tests/fixtures/gmail_recorded/` and the guard tests.
- Live: the loop test and the canary week, in that order; the loop test never runs against the real inbox.

**Acceptance criteria:**

- The loop test shows exactly one bot reply and one skipped auto-response.
- During the canary week no thread received more than one bot reply, no reply went to a thread a human had answered, no send exceeded a cap, and every sent message has a `messages` row with its Gmail id.
- Flipping `EMAIL_BOT_MODE` to `off` on the dashboard stops sends at the next run without a deploy.

---

## Phase E4: Production readiness

**Goal:** The knowledge base stays current without anyone remembering to click, answer quality is sampled weekly, a revoked grant is noticed and repaired by runbook, and the dashboard tells an operator everything they need.

**Depends on captain actions:** nominate the coordinators who may edit the doc and share it with them; curate the doc against the editor guide once; confirm the Discord channel for the `invalid_grant` alert.

**File structure:**

| File | Responsibility |
|---|---|
| `scripts/create-or-update-scheduler-jobs.sh` | Job `sync-knowledge-base`, bootstrap `0 5 * * *`, `/admin/email-bot/sync-knowledge-base` |
| `app/services/cron_sync_service.py`, `app/services/settings_service.py` | `CRON_SYNC_KNOWLEDGE_BASE` mapped to `sync-knowledge-base` |
| `docs/KNOWLEDGE_BASE_EDITING.md` | Editor guide: the three rules, what the bot does with the doc, how to check the last sync on the dashboard, what never to write |
| `evals/sample_live_answers.py` | Weekly groundedness sampling of last week's sent FAQ answers: fetches each inbound text by Gmail id at run time, judges, prints; stores nothing |
| `app/services/channels/gmail_transport.py` | `invalid_grant` on refresh raises `GmailAuthRevoked` |
| `app/services/email_bot/pipeline.py` | On `GmailAuthRevoked`: `EMAIL_BOT_MODE` set to `off`, `EMAIL_BOT_LAST_ERROR` setting written for the banner, Sentry event, Discord post |
| `docs/GMAIL_BOT_SETUP.md` | Re-consent procedure completed |
| `app/routers/admin/email_bot.py`, `templates/web/admin/dashboard.html` | Banner, last sync time and chunk count, auth health, shadow agreement, per-category counts, thread links, resume button |

**Tasks:**

- [ ] **Task E4.1: Daily knowledge-base sync**
  - Modify: `scripts/create-or-update-scheduler-jobs.sh`, `app/services/cron_sync_service.py`, `app/services/settings_service.py`, `app/routers/admin/email_bot.py` (last sync time and chunk count exposed); Create: `docs/KNOWLEDGE_BASE_EDITING.md`
  - Test: `tests/test_cron_sync_service.py` extended; `tests/test_email_bot_endpoint.py` extended (sync failure is reported, not swallowed; an empty `KNOWLEDGE_BASE_DOC_ID` returns 400).

- [ ] **Task E4.2: Weekly groundedness sampling**
  - Create: `evals/sample_live_answers.py`; Modify: `evals/README.md`
  - Test: `tests/test_evals_runners.py` extended (sampling picks only `sent` FAQ rows from the window; nothing is written to the database; transport and judge mocked).

- [ ] **Task E4.3: Revoked-grant alert and re-consent runbook**
  - Modify: `app/services/channels/gmail_transport.py`, `app/services/email_bot/pipeline.py`, `app/services/notifier.py`, `docs/GMAIL_BOT_SETUP.md`
  - Test: `tests/test_gmail_transport.py` extended (`invalid_grant` maps to `GmailAuthRevoked`; other refresh errors do not); `tests/test_email_bot_pipeline.py` extended (revocation flips the mode, writes the banner setting, notifies, and the run summary records the abort).

- [ ] **Task E4.4: Dashboard polish**
  - Modify: `app/routers/admin/email_bot.py`, `templates/web/admin/dashboard.html`
  - Test: `tests/test_admin_dashboard_template.py` extended (banner shown when `EMAIL_BOT_LAST_ERROR` is set; thread links use the recorded thread id; resume posts to the right endpoint); `tests/test_email_bot_endpoint.py` extended (escalation list excludes resolved threads).

**Test strategy:**

- Unit: mappings, error translation, template rendering.
- Recorded fixtures: a recorded `invalid_grant` error body under `tests/fixtures/gmail_recorded/`.
- Live: revoke the throwaway inbox's grant at the Google account page, poll, and confirm the mode flips and the alert lands; re-consent by the runbook and confirm the next poll succeeds.

**Acceptance criteria:**

- Edits to the curated doc are answered by the bot the next day without a deploy or a click.
- The weekly sampling script runs from a laptop with the production token and prints a groundedness score without storing any mail content.
- Revoking and re-granting the throwaway inbox's consent is recovered end to end by following the runbook alone.
- The dashboard card shows mode, last run, last sync, auth health, counters, open escalations with Gmail links, draft acceptance and shadow agreement.

---

## Live end-to-end protocol

Every phase's live test uses the same setup and the same rule: never the real inbox.

1. Two throwaway Gmail accounts from E0: the inbox under test, with its own consent token in the engineer's local `.env`, and the public sender.
2. `scripts/seed_test_inbox.py` sends the anonymised corpus from the sender to the inbox over SMTP, one mail per case, with the headers each case specifies.
3. A local instance with `EMAIL_BOT_ENABLED=true`, the throwaway token, a local database, the real Gemini key, the Jev key, and a test Discord webhook runs `POST /admin/email-bot/poll`.
4. The engineer inspects the outcome in the Gmail UI of the throwaway inbox and in the test Discord channel, which is the closest an end user comes to the feature.
5. Payloads worth keeping are anonymised and committed under `tests/fixtures/gmail_recorded/`.

## Self-review

**Design coverage.** Transport and auth: E0 and E1.4. Guards, one reply per thread, never talk over a human, idempotence, fail closed, circuit breaker, privacy: E1.3, E1.2, E1.9, E1.11. Triage with Jev deciding and LiteLLM in shadow: E1.5. Category gate before confidence gate: E1.9. Sign-up template from settings and holding message: E1.7. `_build_context` fix, sentinel, prohibitions: E1.8. Escalation forward and Discord with urgent safeguarding: E1.6. Kill switch and modes: E1.9, E1.10. Evaluation gate and draft acceptance: E2. Sending, RFC 3834 headers, caps, canary, loop test: E3. Daily sync, editor guide, weekly sampling, `invalid_grant`, dashboard: E4. Per-language automatic sending: E3.2. Push notifications: out of scope by the design's non-goals.

**Deliberate deferrals.** The IMAP fallback transport is documented in the design and not built unless the E0 consent fails. Messenger's migration onto the engine is not in this plan. The parent spec's `app/routers/bot.py` stays unwired; the one endpoint email needs from it is re-exposed as `POST /admin/email-bot/sync-knowledge-base`.

**Type consistency.** `IncomingMessage`, `OutboundReply`, `TriageSignals`, `TriageDecision`, `MailTransport`, `ConversationService`, `Notifier`, `EscalationEvent`, `DeliveryMode`, `ReplySink`, `DeliveryResult` and `RunSummary` are defined once under Shared interfaces and referenced by name in every phase. `send_reply` is named in E1 as absent and in E3 as added, with the same signature.

**Review Focus.** Items 1, 2, 3 and 5 are pinned in E1; item 4 in E3. Each names its test file.
