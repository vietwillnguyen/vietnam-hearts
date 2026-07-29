# Multi-Channel QnA Bot Design

- **Status:** Approved
- **Date:** 2026-07-28
- **Supersedes:** the disabled bot and messenger routers currently commented out in `app/main.py`

## Context

Vietnam Hearts receives inbound questions from prospective and current volunteers across Facebook Messenger, Instagram DMs, Zalo, and email.
Today those are handled by a mix of ManyChat on its free plan, an ad-hoc Facebook AI integration, and humans reading email.
The tooling is scattered across vendors, none of it shares a knowledge base, and none of it is version controlled alongside the rest of the organisation's automations.

The goal is to centralise inbound question answering into the existing `vietnam-hearts` FastAPI service, which already holds the volunteer database, the admin dashboard, Supabase auth, Google Sheets and Docs integration, and the email sending pipeline.

### What already exists

A substantial RAG bot was built in this repository and then disabled.
`app/main.py` lines 31-32 and 131-137 have the imports and `include_router` calls commented out.

| Component | File | State |
|---|---|---|
| Facebook Messenger webhook and verification | `app/routers/messenger.py` | Complete, unwired, and broken (see D1) |
| Messenger Send API client and mock | `app/services/messenger/message_sender.py` | Complete |
| RAG orchestration | `app/services/bot_service.py` | Complete |
| Gemini embeddings and pgvector similarity search | `app/services/knowledge_service.py` | Complete |
| Google Docs and Drive ingestion | `app/services/document_service.py` | Complete |
| Chat and knowledge-base admin endpoints | `app/routers/bot.py` | Complete, unwired |

This project is therefore a revive, harden, and extend effort rather than a greenfield build.

### Defects found in the existing code

These are stated here because they shape the design rather than merely needing a patch.

1. **Webhook verification cannot succeed.**
   `app/routers/messenger.py` lines 41-44 declare query parameters named `mode`, `verify_token`, and `challenge`.
   Meta sends `hub.mode`, `hub.verify_token`, and `hub.challenge`.
   Those never bind, so the `mode == "subscribe"` check is always false and verification always fails.

2. **The tests encode the wrong contract.**
   `tests/test_messenger.py` line 29 asserts against `"challenge"` rather than `"hub.challenge"`.
   The suite is green over a handshake that cannot work against the real platform.
   Green tests over a fabricated contract are worse than no tests because they suppress the signal.

3. **No webhook signature verification exists anywhere.**
   `FACEBOOK_APP_SECRET` is read at `app/config.py` line 75 and never used.
   No `X-Hub-Signature-256` handling exists in the codebase.
   The webhook endpoint is public and unauthenticated, so anyone can forge events and consume the Gemini quota.

4. **Silent degradation to meaningless embeddings.**
   `app/services/knowledge_service.py` line 199 falls back to MD5-hash pseudo-embeddings when Gemini is unavailable.
   This does not degrade to "no answer", it degrades to "confident answers retrieved from effectively random chunks".
   For a bot facing prospective volunteers this is worse than an outage.

5. **A wasted embedding call on every inbound message.**
   `app/routers/messenger.py` line 112 constructs `BotService()` per message, and `KnowledgeService._get_embedding_model` fires a live `embed_content` call on construction.
   That burns one call per message against a 15 requests-per-minute free tier before any real work happens.

6. **An entire test file is skipped.**
   `tests/test_faq_handling.py` line 17 calls `pytest.skip(..., allow_module_level=True)`.

## Goals

- One service answering inbound questions on Messenger, Instagram, and email from a single knowledge base.
- Retire ManyChat.
- Escalate anything requiring human judgement to the right human, with the thread paused so the bot stops talking over them.
- Never auto-answer a question in a category reserved for executive decision-making, regardless of how well the knowledge base matches it.
- Make answer quality measurable, so the ManyChat cutover is gated on evidence rather than on impression.

## Non-goals

- Outbound broadcast or marketing messages.
  The bot only ever replies to an inbound message.
  This is a deliberate constraint that sidesteps most platform review and cost concerns, described under Platform Constraints.
- Zalo, in this phase.
  See D3.
- Replacing the existing admin dashboard, auth, or email sending.
- A general agent framework.
  This is a single-purpose question answering service.

## Platform constraints

### Facebook Messenger

Viable, and the lowest-friction channel.
Requires the `pages_messaging` permission through Meta App Review.
Meta Business Verification is not required for basic Messenger, unlike WhatsApp, though it is required for certain message tags this design does not use.
The 24-hour response window is a non-issue because the bot only ever replies to a user who just messaged.
Development and testing work today with users who hold a role on the app, before review is granted.

### Instagram

Viable with more gates.
Requires an Instagram Professional account, either Business or Creator.
Requires the `instagram_business_basic`, `instagram_business_manage_messages`, and `human_agent` permissions.
App Review expects a screencast and a demonstrated opt-out path.
Up to 25 test users are available before review.
The automated DM rate limit of roughly 200 per hour per account is far above Vietnam Hearts' volume.
Messenger and Instagram share one Meta app, one webhook URL, one HMAC signature scheme, and the Graph `/me/messages` send endpoint.

### Zalo

Blocked, for two independent reasons.

First, Zalo groups have no API.
`tests/test_zalo_integration.py` line 19 pins `https://zalo.me/g/gcmgkowx6gvotsghvsji`, a group invite link wired into the email templates.
Only a Zalo Official Account is programmable.

Second, a verified Official Account requires business registration documents (`giấy phép kinh doanh`) bearing an official seal and signature, submitted within 14 days of account creation.
A volunteer organisation without a Vietnamese legal entity may not be able to clear this.

Even with a verified account, the economics are constrained.
The OpenAPI permits replies within 7 days of the user's last interaction.
Advisory messages are free within 48 hours but capped at 8 free messages, after which they cost roughly 55 VND each.
OAuth token rotation would also need operating.

### Email

The only channel with no platform gating at all.
No app review, no business verification, no conversation window, no rate limit.
This is why it ships ahead of Instagram in the rollout.

## Prior art

### mailhub (`/home/viet/git/vietwillnguyen/mailhub`)

Heavy, direct overlap.
mailhub solves the same problem one step earlier in the pipeline: read Gmail, classify with an LLM, route somewhere.
It shares this repository's stack of Python, uv, ruff, pytest, and `docs/superpowers/` plans.

| mailhub asset | Role here |
|---|---|
| `docs/adr/0002-imap-app-password-over-oauth.md` | Settles the inbound email auth decision (D6) |
| `docs/adr/0003-model-agnostic-classifier-via-litellm.md` | Settles the triage classifier decision (D7) |
| `src/core/classifier.py` (97 lines) | Triage classifier, with the taxonomy replaced |
| `src/core/imap_client.py` (156 lines) | `EmailAdapter.parse()` |
| `src/core/store.py` (89 lines) | Watermark and seen-message tracking, the dedup layer |
| `src/core/digest.py` | Batching admin and executive notifications |

mailhub diverges in one important way.
It is digest-and-notify: read-only, batched, with a single known recipient.
This project is converse-and-reply: interactive, per-thread, with members of the public.
mailhub therefore supplies ingestion and classification but not conversation state, loop protection, or handoff.

### firedesk (`/home/viet/git/magnitude-minds/firedesk`)

Strong conceptual echo, near-zero reusable code.
`backend/app/predefined_skills/support-operations/SKILL.md` describes this project almost verbatim as triaging incoming support tickets and emails, drafting responses from documentation, and queueing for approval.

The code does not transfer.
`backend/app/connectors/catalog/display_only/instagram/provider.yaml` is `auth_mode: DISPLAY_ONLY`, a user-interface placeholder with no implementation.
firedesk is per-organisation Cloud Run agents, GCS FUSE workspaces, Redis Streams, Langfuse, and Grafana, which is the wrong weight class for a volunteer organisation's FAQ bot.

One finding is worth following up.
Migration `backend/alembic/versions/024_drop_knowledge.py`, dated 2026-03-30, drops the `knowledgedocument` table with the comment that recreating it is intentionally omitted because the feature is removed, and provides no downgrade path.
firedesk now lists `ragieai` as a display-only connector instead.
A commercial product in the same organisation built in-house document knowledge and then deliberately exited it, while this project is about to invest further in in-house RAG.
See Open Questions.

### DataFabric (`/home/viet/git/magnitude-minds/fabrion/DataFabric`)

Borrow the discipline, not the infrastructure.
DataFabric is a Go and Kubernetes control plane orchestrating Dagster, n8n, Milvus, Keycloak, and Flowise.
Three things transfer: the ADR format in `docs/adr/`, the evaluation-harness pattern from the sibling `fabrion-extraction-evaluation` repository, and the anti-corruption-layer shape of its subsystem clients, which motivates the channel adapter seam.

## Decisions

**D1. Fix the Meta webhook contract against recorded real payloads.**
Bind `hub.mode`, `hub.verify_token`, and `hub.challenge` explicitly with FastAPI `Query` aliases.
Commit real recorded Meta payloads as test fixtures rather than hand-written dictionaries, because defects 1 and 2 share the root cause of writing both the handler and its tests against an imagined contract.

**D2. Ship Messenger and Instagram on one Meta app, one webhook, one adapter class.**
The two surfaces overlap by roughly 90 percent.
A class per channel would be duplication presented as abstraction.
The adapter seam exists so that genuinely different channels, email now and Zalo later, can be separate files.

**D3. Zalo is out of scope, not merely sequenced later.**
Confirmed during review that Vietnam Hearts holds no Vietnamese legal entity and does not expect one soon.
A verified Official Account cannot be created without one, and only Official Accounts are programmable.
This is a legal-status blocker rather than an engineering one, so no Zalo adapter and no adapter stub will be written, since neither could be tested end to end.
Revisit only if an entity is established.

**D4. Approach B, normalise and harden, rather than minimal wiring.**
Channel adapters normalise inbound events into a single `IncomingMessage`.
A `ConversationService` owns pause and resume state.
Existing services are preserved rather than rewritten.
Minimal wiring was rejected because Instagram's payload differences and the handoff state machine would land in the same module-level functions that already mix transport, routing, and business logic.
Self-hosting Chatwoot and Flowise was rejected because a second deployment, database, and auth system re-scatters precisely what this project centralises.

**D5. Three escalation tiers, with the category gate running before the confidence gate.**
This ordering is the single most important decision in the design.
A question such as "can we put your logo on our fundraising page?" will match branding documents with high confidence and be auto-answered if confidence is evaluated first.
Under confidence-first ordering, the highest-stakes questions are exactly the ones most likely to be handled automatically.

**D6. Inbound email over IMAP using the existing app password.**
`app/services/email_service.py` lines 35-39 already authenticate to Gmail SMTP with `GMAIL_APP_PASSWORD`.
The same credential unlocks IMAP, so inbound email needs no new credentials and no Google Cloud setup.
Polling runs on the existing Cloud Scheduler cron.

Confirmed during review that the mailbox is `vietnam.hearts.volunteering@gmail.com`, a plain consumer Gmail account rather than a Google Workspace domain.
That makes IMAP the only practical option rather than merely the preferred one.
Service-account domain-wide delegation requires a Workspace domain and is therefore unavailable.
mailhub ADR-0002 reached this conclusion against the identical constraint: the `gmail.readonly` and `gmail.send` scopes are restricted, an external OAuth app in Testing status receives refresh tokens that expire every 7 days, escaping that requires Google's paid multi-week CASA Tier 2 verification, and a consumer account cannot use the "Internal app" escape hatch available to Workspace domains because there is no organisation to scope the app to.

This mailbox is the organisation's root account, so the app password grants broad read and send access to it.
The credential must live only in Secret Manager, never in the repository, and is revocable at <https://myaccount.google.com/apppasswords>.
This is an accepted cost of the constraint above rather than a preference.

**D7. Route the triage classifier through LiteLLM with JSON-schema structured output.**
Consistent with mailhub ADR-0003.
The model becomes a configuration string, so Gemini free tier can run for cost and be swapped for a stronger model if triage accuracy disappoints, without a code change.
The RAG answer path stays on the existing `google-genai` client.

**D8. Process webhooks inline rather than in a background task.**
Meta's webhook timeout is roughly 20 seconds and a full triage plus retrieval plus generation cycle is 1-3 seconds.
Cloud Run throttles CPU after the response is returned unless always-on CPU is enabled, so `BackgroundTasks` would be starved on the current deployment.

**D9. Fail closed.**
Delete `_create_fallback_embeddings`.
Any unavailability of embeddings or generation escalates to `needs_admin` rather than producing a guess.

**D10. Hard cutover from ManyChat, gated on the evaluation harness.**
Two applications subscribed to the same Page webhook both fire, so an overlap period would send every user two replies.
ManyChat keeps running untouched until the evaluation passes and App Review is approved, then it is disconnected and the new app subscribed in one switch.

**D11. Copy and adapt mailhub's email code rather than sharing a package.**
Roughly 250 lines across two repositories with different deployment targets.
Coupling a volunteer organisation's service to a personal inbox tool buys nothing.

**D12. Escalation notifications go to email plus one real-time channel.**
Email alone was rejected during review because an executive escalation that sits unread in an inbox defeats the purpose of pausing the thread.
The `EmailService` path is reused for the durable record, and a second channel carries the interrupt.

Zalo was the reviewer's first instinct for that second channel, but it cannot work, for exactly the reason recorded in D3.
Sending a Zalo message programmatically requires an Official Account, the same Official Account that no legal entity exists to register.
The notifier is therefore designed against a small `Notifier` interface with an email implementation plus one webhook implementation, and the specific webhook provider is the subject of Open Question 2.
Whichever is chosen, the integration is a single outbound POST with no inbound surface, so it carries none of the platform review burden the messaging channels do.

## Architecture

```
[Meta webhook: push]              [Cron: IMAP poll, every ~5 min]
       │                                    │
  verify HMAC-SHA256                  filter bulk / auto-submitted /
  (X-Hub-Signature-256)               no-reply; enforce loop guard
       │                                    │
  MetaAdapter.parse()                 EmailAdapter.parse()
       └──────────────┬─────────────────────┘
                      ▼
              list[IncomingMessage]
      {channel, thread_key, sender_id, text, provider_message_id}
                      │
              dedupe on provider_message_id
                      │
        ConversationService.get_or_create()  ── paused? record, no reply
                      ▼
   [1] TriageClassifier   (LiteLLM, JSON schema, per D7)
       → {tier, category, summary, reason}
                      │
       tier == needs_executive?  ─── yes ──┐   ← category gate runs FIRST (D5)
                      │ no                 │
                      ▼                    │
   [2] BotService.answer() → {text, confidence, sources}
                      │                    │
       confidence ≥ threshold? ─yes─► send reply
                      │ no                 │
                      └────────┬───────────┘
                               ▼
              send holding message in the user's language
              ConversationService.pause(tier, reason)
              notify admins (needs_admin) or escalation owner (needs_executive)
```

Triage costs one additional LLM call per message before retrieval.
On a small, fast model that cost is negligible, and it is the gate that prevents a well-matched document from auto-answering a licensing request.

## Components

| Module | Status | Purpose |
|---|---|---|
| `app/services/channels/base.py` | new | `ChannelAdapter` protocol: `parse`, `send_text`, `verify_signature`, `channel` |
| `app/services/channels/meta.py` | new | Messenger and Instagram, one class (D2) |
| `app/services/channels/email.py` | new | IMAP adapter, adapted from mailhub `imap_client.py` |
| `app/services/channels/mock.py` | adapted | Extends the existing `MockMessageSender` |
| `app/services/triage_service.py` | new | LiteLLM classifier, adapted from mailhub `classifier.py` |
| `app/services/conversation_service.py` | new | Pause and resume state, transcripts, loop guard |
| `app/routers/webhooks.py` | replaces `messenger.py` | Single verified entry point |
| `app/routers/admin/conversations.py` | new | Paused-thread list, transcript, resume |
| `app/services/bot_service.py` | modified | Real confidence, reply in the user's language, constructed once |
| `app/services/knowledge_service.py` | modified | Remove the MD5 fallback, fail closed (D9) |
| `app/services/messenger/message_sender.py` | absorbed | Send logic moves behind the adapter |
| `evals/` | new | Golden question set and judge runner |

## Data model

One Alembic migration adds two tables.

`conversations`

| Column | Notes |
|---|---|
| `id` | primary key |
| `channel` | `messenger`, `instagram`, `email` |
| `thread_key` | page-scoped ID for Meta, root `Message-ID` for email |
| `status` | `bot`, `paused_handoff`, `paused_manual` |
| `tier` | last triage tier that caused a pause |
| `paused_reason`, `paused_at` | populated on escalation |
| `bot_reply_count` | loop guard, reset when a human replies |
| `last_inbound_at`, `created_at`, `updated_at` | |

Unique constraint on `(channel, thread_key)`.

`messages`

| Column | Notes |
|---|---|
| `id`, `conversation_id` | |
| `direction` | `in` or `out` |
| `text` | |
| `provider_message_id` | **unique index** |
| `confidence`, `sources` | outbound only |
| `tier`, `triage_category`, `triage_summary` | inbound only |
| `created_at` | |

The unique index on `provider_message_id` is load-bearing.
Meta retries webhook deliveries, and without it every retry sends a duplicate reply.

## Triage taxonomy

| Tier | Trigger | Action |
|---|---|---|
| `auto_answer` | Knowledge base covers it and retrieval confidence is above the threshold | Bot replies |
| `needs_admin` | Low confidence, explicit request for a human, or any service unavailability | Holding reply, pause thread, notify the admin group |
| `needs_executive` | Category match, regardless of confidence | Holding reply, pause thread, notify the escalation owner directly |

The escalation owner is the single person who makes executive decisions for Vietnam Hearts, configured separately from the general `ADMIN_EMAILS` list.

Executive categories, all four confirmed by the escalation owner:

1. **Money and commitments.** Donations, sponsorship offers, budget questions, paying for anything, or anything committing Vietnam Hearts to a cost or obligation.
2. **Partnerships, media, and brand use.** Collaboration proposals from other organisations, press and interview requests, and any request to use the Vietnam Hearts name, logo, or photographs.
3. **Safeguarding, complaints, and legal or visa matters.** Child protection concerns, incidents, complaints about a volunteer or staff member, and immigration, visa, or legal questions.
4. **Volunteer acceptance decisions.** Accepting, rejecting, or guaranteeing a placement for a specific applicant, as opposed to explaining the general process.

For email specifically, a single message may carry several questions.
The rule is that if any part of a message escalates, the whole message escalates.

## Confidence gating

The hardcoded `0.9` and `0.3` values at `app/services/bot_service.py` line 169 are replaced by the maximum `similarity` score that the `match_documents` RPC already returns.
A score below `answer_threshold`, starting at 0.5 and calibrated against the evaluation set, becomes `needs_admin`.

Separately, the generation prompt instructs the model to emit a refusal sentinel when the retrieved context is insufficient, and that sentinel is also treated as `needs_admin`.
Both signals earn their place because cosine similarity is a weak proxy for whether the retrieved text actually answers the question asked.

## Email-specific guards

Email needs protections the direct-message channels do not.

- **Mail loop protection.** Auto-replying to another auto-responder loops indefinitely. `bot_reply_count` caps bot replies per thread without an intervening human reply.
- **Bulk and automated filtering.** Messages carrying `Precedence: bulk`, `List-Unsubscribe`, or `Auto-Submitted` headers are skipped, as are no-reply senders.
- **Threading.** Replies go in-thread using the original `Message-ID` in `In-Reply-To` and `References`.

## Error handling

| Failure | Behaviour |
|---|---|
| LLM unavailable or rate-limited | Retry with backoff, then `needs_admin`. Never guess. |
| Embeddings unavailable | `needs_admin`, no hash fallback (D9) |
| Unknown webhook `object` | Return 200 and log. Never 4xx, because Meta disables webhooks after repeated failures. |
| Signature mismatch | 403, log, no processing |
| Send failure | Log and report to Sentry, no retry loop |
| IMAP unreachable | Cron run fails loudly to Sentry, leaving no partial state |

## Testing strategy

- Adapter parse tests run against **recorded real payloads committed as fixtures**, which is the direct remedy for defect 2.
- Signature verification tests compute real HMAC values.
- A webhook verification test uses the actual `hub.*` parameter names.
- Deduplication: the same `provider_message_id` delivered twice produces exactly one reply.
- Loop guard: the bot reply cap is enforced per thread.
- Bulk filtering: messages with `Precedence: bulk` and `List-Unsubscribe` are skipped.
- Triage: table-driven over the golden set's tier labels, with the LLM mocked for unit tests.
- Conversation state machine transitions.
- `tests/test_faq_handling.py` is rewritten against the new pipeline and un-skipped.

## Evaluation harness

Lives in `evals/`, patterned on the sibling `fabrion-extraction-evaluation` repository.

`evals/golden_qa.yaml` holds cases with `question`, `language` (`vi` or `en`), `expected_tier`, `must_mention` facts, and `must_not_answer`.

Two dimensions are scored.

1. **Triage accuracy**, where the metric that matters is executive recall.
   A missed escalation is the worst failure this system can produce.
2. **Answer groundedness**, judged by an LLM against the `must_mention` facts, reusing the structured-output pattern already established in `tests/test_llm_judge.py`.

The cutover gate is 100 percent recall on `needs_executive` cases, which is non-negotiable and admits zero misses, together with at least 90 percent on auto-answer correctness.
Both Vietnamese and English cases are required.
The harness runs on demand rather than in blocking CI, because it consumes API calls.

## Rollout

| Phase | Content | Gated on |
|---|---|---|
| 0 | Fix Messenger: `hub.*` binding, HMAC verification, `BotService` singleton, deduplication, wire the routers | nothing |
| 1 | Conversation store, triage classifier, three-tier handoff, admin view | 0 |
| 2 | Email adapter, adapted from mailhub | 1 |
| 3 | Instagram adapter and Meta App Review submission | 1 |
| 4 | Evaluation harness, golden set, threshold calibration | 1 |
| 5 | ManyChat hard cutover | 4 passing and App Review approved |

Email sits at phase 2, ahead of Instagram, precisely because it has no review queue.
That delivers a working channel while Meta's review sits in the wait state.

## Open questions

These do not block starting phase 0.

1. **Why was firedesk's `knowledgedocument` table dropped in migration 024?**
   If in-house RAG quality proved unmanageable without systematic evaluation, that reinforces the evaluation gate in phase 4.
   If customers simply wanted to bring their own vector store, it says nothing about this project.

2. **Which real-time channel carries the executive escalation interrupt?**
   Zalo was the first instinct but is unavailable for the reason recorded in D3 and D12.
   Telegram, Discord, and Slack are all a single outbound webhook POST and all work from Vietnam.
   Discord has a specific advantage: mailhub's `src/core/discord_client.py` is 49 lines and adapts directly, matching D11.

3. **What is the exact wording of the holding message, in Vietnamese and English?**
   It is the only bot-authored text a user sees when escalation happens, so it should be written by a human rather than generated.

### Resolved during review

- **Mailbox type.** `vietnam.hearts.volunteering@gmail.com`, a plain consumer Gmail account, not a Workspace domain. Folded into D6, where it upgrades IMAP from preferred to only viable.
- **Zalo legal entity.** None exists and none is expected soon. Folded into D3, which now places Zalo out of scope rather than later in the queue.
- **Escalation reach.** Email alone is insufficient. Folded into D12.

## References

- Meta Messenger Platform overview: https://developers.facebook.com/documentation/business-messaging/messenger-platform/overview
- Instagram messaging with Instagram Login: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/
- Zalo Official Account registration and usage policy: https://oa.zalo.me/home/documents/policy/dang-ky-va-su-dung-tai-khoan-zalo-official-account-doanh-nghiep
- Zalo Official Account message types and fees: https://oa.zalo.me/home/documents/guides/tong-quan-cac-loai-tin-nhan-tren-zalo-official-account-_3651713298729094511
- Zalo for Developers: https://developers.zalo.me/docs
- mailhub ADR-0002, IMAP app password over OAuth
- mailhub ADR-0003, model-agnostic classifier via LiteLLM
