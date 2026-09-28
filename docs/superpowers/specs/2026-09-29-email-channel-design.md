# Email Channel Design

- **Status:** Accepted
- **Date:** 2026-09-29
- **Parent spec:** `docs/superpowers/specs/2026-07-28-qna-bot-design.md` (the "parent spec" below).
  This document adds the email channel to that design and amends its D6.
  Everything the parent spec decides and this document does not mention still applies.
- **Decided by:** the captain's six decisions of 2026-09-29, recorded in the Decisions table.
- **Open:** the holding-message copy is still awaiting sign-off (parent spec, Open questions).

## Context

The volunteer inbox is a plain consumer Gmail account and is read by hand today.
The parent spec already routes an `EmailAdapter` into the same triage, retrieval, escalation and holding-message pipeline as Messenger, and places email at rollout phase 2 because it has no platform review queue.
Only phase 0 of that rollout has landed, so the shared engine the email channel needs (conversation store, triage classifier, notifier, three-tier handoff) does not exist yet.
The email channel therefore builds the engine, and Messenger plugs into it afterwards.

Two findings change the plan the parent spec assumed.

1. D6 chose IMAP with the app password because "an external OAuth app in Testing status receives refresh tokens that expire every 7 days" and escaping that needs paid verification.
   Google's documentation separates the two: the 7-day expiry applies only to the Testing publishing status, and an app used only by its owner is exempt from verification and from the CASA assessment.
   The Gmail API is viable on this account after one consent click.
2. The dominant inbound question, "how do I sign up?", is best answered by a fixed, human-written template rather than by retrieval, for the reasons the parent spec gives for the holding message.

## Goals

- Every new inbox mail is labelled, and answerable mail has a reply waiting as a Gmail draft inside its thread, within one poll of arriving.
- Escalations reach the captain by forward and by Discord, with the thread paused for the bot.
- After the evaluation gate, sign-up and FAQ replies go out automatically, under caps and a kill switch.
- Nothing is sent by the bot until that gate passes, enforced by construction rather than by a flag at the call site.

## Non-goals

- Push notifications from Gmail (Pub/Sub `users.watch`).
  The poll runs twice a day, so minutes of latency are irrelevant and a second public endpoint is not worth securing.
- Outbound sequences or any message the bot initiates.
  The bot only ever replies to an inbound message.
- Migrating the Messenger webhook onto the shared engine.
  That is the parent spec's phase 1 leftover and stays a separate piece of work; the engine is built here so it can happen.
- Answering from the sign-up responses sheet, the schedule sheet or the volunteers table.
  Those hold personal data and are never a retrieval source.

## Decisions

Captain decisions of 2026-09-29.
Where they differ from the investigation's recommendation, the captain's decision is what is recorded.

| # | Decision | Decided | Replaces | Consequence |
|---|---|---|---|---|
| 1 | Inbound over the Gmail API with a one-time owner OAuth consent (scope `gmail.modify`, consent screen External, published to production without verification), polled twice a day in Vietnam time on the existing Cloud Scheduler plus admin-endpoint pattern. IMAP with the app password stays documented as the fallback if consent fails. | 2026-09-29 | Parent spec D6 (IMAP, polled every ~5 min) | Threads, labels and drafts are first-class objects; the bot's grant is revocable without touching the SMTP password; one poll endpoint, no push |
| 2 | Triage: Jev decides in draft mode, with the LiteLLM structured-output classifier (Gemini Flash-Lite by default, Claude Haiku 4.5 as the config swap) running in shadow behind one `TriageClassifier` protocol. Jev keeps the deciding role only after 100 percent executive recall on the bilingual golden set. | 2026-09-29 | Parent spec D7 as the sole classifier | Two implementations ship together; disagreements are recorded on every message; the switch is a setting |
| 3 | Knowledge base: one curated Google Doc owned by the captain, editable by coordinators, re-synced daily. | 2026-09-29 | Nothing (the ingestion path already reads Google Docs) | A daily `sync-knowledge-base` job; an editor guide; the sign-up answer is not in the doc, it is a template |
| 4 | Escalation: every escalation forwards to the captain, plus a Discord post; safeguarding is flagged urgent in Discord. | 2026-09-29 | The routing-table proposal | One recipient setting; no per-category routing to build |
| 5 | Automatic sending: after the evaluation gate, sign-up and FAQ replies go automatic together. | 2026-09-29 | The sign-up-first staging | One build phase (E3) turns on `auto` for both, under the confidence gate for FAQ |
| 6 | Sign-up reply: written by the captain, in English and Vietnamese; points only to the Google Form; onboarding and the group chat follow by email after approval; class days, times and the form link render from settings. The Vietnamese version needs native-speaker sign-off before it is sent automatically. | 2026-09-29 | Nothing | A fixed template per language; automatic sending is gated per language |

Open items, not decided:

- The holding-message copy in English and Vietnamese (parent spec, Holding message section) is drafted and awaiting sign-off.
  The email channel reuses it unchanged, with a signature line.
- The captain's English sign-up copy has two candidate typo fixes ("If you are in the area", "when it's approved") that need his agreement before they are applied.
  Until then the copy renders verbatim.

## Architecture

```
[Cloud Scheduler: poll-volunteer-inbox, twice daily, Asia/Ho_Chi_Minh]
        |
  POST /admin/email-bot/poll   (apikey header, same as every other job)
        |
  EMAIL_BOT_ENABLED env false? -> 200 "disabled", nothing else
  EMAIL_BOT_MODE setting == off? -> 200 "off", nothing else
        |
  GmailTransport.list_unprocessed()   inbox, last 7 days, no VH-Bot/Seen label
        |
  deterministic guards (mail_guards.py): auto-submitted, bulk, list,
  no-reply, self, Gmail promotions/social/updates/forums  -> skip, label
        |
  GmailAdapter.parse() -> IncomingMessage
        |
  dedupe on provider_message_id (unique index)          -> already seen, relabel only
        |
  ConversationService.get_or_create()
  thread has a non-bot message from the inbox address?  -> paused_manual, label only,
                                                           delete the bot's own draft
        |
  [1] TriageClassifier.classify()  Jev decides, LiteLLM runs in shadow,
      both recorded                -> {category, language, confidence, signals}
      derive_tier() in code, never by the model
        |
  tier == skip?            -> label VH-Bot/Skipped, audit row, stop
  tier == needs_executive  -> holding reply + escalate      (category gate first, D5)
  tier == needs_admin      -> holding reply + escalate
                              (no holding reply once the thread has its one bot reply)
  tier == auto_answer, category == signup -> fixed template in the sender's language
  tier == auto_answer, category == faq    -> [2] BotService.chat()
                                             similarity >= ANSWER_THRESHOLD and no
                                             refusal sentinel, else needs_admin
        |
  delivery mode gate (delivery.py)
      draft -> Gmail draft inside the thread, label VH-Bot/Drafted
      auto  -> send in-thread with RFC 3834 headers, under caps, label VH-Bot/Sent
               (only for languages in EMAIL_BOT_AUTO_LANGUAGES; otherwise draft)
        |
  escalation (any mode): forward to ESCALATION_OWNER_EMAIL with a summary,
  Discord post (urgent for safeguarding_legal), label VH-Bot/Escalated,
  ConversationService.pause()
        |
  audit: conversations + messages rows, email_bot_runs row, label VH-Bot/Seen last
```

Messenger shares everything from `IncomingMessage` down to the audit rows.
The channel-specific parts of email are exactly three: the transport, the deterministic guards, and the delivery mode, which Messenger has no equivalent for.
The engine never learns about drafts.

## Components

| Module | Status | Purpose |
|---|---|---|
| `app/services/channels/base.py` | new | `ChannelAdapter` protocol, `IncomingMessage`, `OutboundReply` (parent spec component, built here) |
| `app/services/channels/mail_guards.py` | new | Pure deterministic guard functions over headers and labels; no I/O, like `meta_signature.py` |
| `app/services/channels/mail_builder.py` | new | Pure MIME construction for replies and forwards: `In-Reply-To`, `References`, subject, RFC 3834 headers |
| `app/services/channels/gmail_transport.py` | new | `MailTransport` protocol and `GmailTransport`, the only module that talks to the Gmail API (Discovery-built client, already a dependency). `send_reply` does not exist until E3 |
| `app/services/channels/gmail.py` | new | `GmailAdapter`: list, guard, parse, label, draft, delete draft, and from E3 send. It never forwards; the forward is a notifier concern |
| `app/services/triage/protocol.py` | new | `TriageClassifier` protocol, `TriageDecision`, `Category`, `Tier`, `Language` |
| `app/services/triage/policy.py` | new | `derive_tier()` and the executive-category table; pure |
| `app/services/triage/jev.py` | new | `JevClassifier` over the vendor SDK |
| `app/services/triage/litellm.py` | new | `LiteLLMClassifier` with JSON-schema structured output (parent spec D7) |
| `app/services/triage/shadow.py` | new | `ShadowingClassifier`: runs the decider and the shadow, records both |
| `app/services/conversation_service.py` | new | Pause and resume, reply counter, human-reply detection, dedupe (parent spec component) |
| `app/services/notifier.py` | new | `Notifier` protocol, `EmailForwardNotifier` over the existing `EmailService` SMTP path, `DiscordNotifier`, `CompositeNotifier` (parent spec D12) |
| `app/services/email_bot/pipeline.py` | new | The per-run orchestration described in the diagram |
| `app/services/email_bot/replies.py` | new | Sign-up template and holding message rendering, per language, from settings |
| `app/services/email_bot/delivery.py` | new | Delivery mode gate; `DraftSink` in E1, `SendSink` and caps in E3 |
| `app/services/email_bot/summaries.py` | new | One-sentence escalation summary through the existing Gemini client, escalation path only |
| `app/services/bot_service.py` | modified | Empty context raises `NoRelevantContext`; refusal sentinel; email prompt prohibitions; answer in the sender's language |
| `app/routers/admin/email_bot.py` | new | `POST /admin/email-bot/poll`, `POST /admin/email-bot/sync-knowledge-base`, escalation list, run history, resume |
| `scripts/gmail_oauth_consent.py` | new | One-off installed-app consent flow that prints the refresh token |
| `evals/` | new | Golden set, judge runner, threshold calibration, shadow report, live sampling |

The parent spec named a single `app/services/triage_service.py` and `app/services/channels/email.py`.
Two classifier implementations plus a pure policy module justify a package, and the adapter is named for the transport it wraps.

## Data model

One Alembic migration adds the parent spec's two tables with the email-specific columns below, plus a run table.

`conversations`, as in the parent spec, with:

| Column | Notes |
|---|---|
| `thread_key` | Gmail `threadId` for email. The parent spec said root `Message-ID`; the thread id is what drafts, replies and thread reads need, and the `Message-ID` lives on the message row |
| `sender_key` | SHA-256 of the lower-cased sender address. Enough for the per-sender cap and for grouping; the address itself is never stored |
| `status` | `bot`, `paused_handoff`, `paused_manual`. A human reply from the inbox address moves a thread to `paused_manual` and only a dashboard resume moves it back |

`messages`, as in the parent spec, with:

| Column | Notes |
|---|---|
| `text` | Outbound bot text only on the email channel. Inbound bodies are not copied out of Gmail; the audit row carries ids and triage metadata |
| `rfc_message_id`, `gmail_thread_id` | For `In-Reply-To` and `References`, and for the Gmail link on the dashboard |
| `language` | `en`, `vi`, `other` |
| `triage_confidence`, `classifier` | The deciding classifier's confidence and name |
| `triage_shadow` | JSON: the shadow classifier's decision, for the disagreement report |
| `action` | `labelled`, `drafted`, `sent`, `forwarded`, `skipped`, `paused` |
| `gmail_message_id_out`, `gmail_draft_id` | Ids of anything the bot created, so drafts can be deleted and sends audited |
| `draft_outcome` | `pending`, `sent_unchanged`, `sent_edited`, `deleted`; filled in by later polls, feeds the draft-acceptance metric |

`email_bot_runs`

| Column | Notes |
|---|---|
| `started_at`, `finished_at`, `mode` | |
| `listed`, `processed`, `drafted`, `sent`, `forwarded`, `skipped`, `errors` | Counters for the dashboard card |
| `aborted_reason` | Set when the circuit breaker stops a run |

The unique index on `messages.provider_message_id` (the Gmail message id) is the idempotence key.
Gmail labels are the human-visible mirror and are re-applied on a retry, never relied on.

## Gmail transport and auth

**Consent.** A dedicated OAuth client of type Desktop, separate from the web client in issue #7, in the existing project.
Consent screen External, published to production, not submitted for verification, one scope, `https://www.googleapis.com/auth/gmail.modify`.
The captain runs `scripts/gmail_oauth_consent.py` once, signed in as the volunteer inbox, through the unverified-app screen, and it prints a refresh token.
Client id, client secret and refresh token go to Secret Manager and reach Cloud Run as `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET` and `GMAIL_OAUTH_REFRESH_TOKEN`, the same way the other secrets are set today.
The token lives until revoked, unused for six months, or invalidated by a password change on the account.

**Refresh and revocation.** The app refreshes access tokens itself.
An `invalid_grant` on refresh raises `GmailAuthRevoked`; the pipeline flips `EMAIL_BOT_MODE` to `off`, records the reason for the dashboard banner, and reports to Sentry.
The most likely cause is a password change on the root account, and the re-consent runbook in `docs/GMAIL_BOT_SETUP.md` is the fix.

**Polling.** `CRON_POLL_INBOX` defaults to `0 8,18 * * *` in `Asia/Ho_Chi_Minh`: one run in the morning and one at the end of the day.
Cadence is owned by the setting and applied by the existing `POST /admin/sync-cron-schedules`, exactly like the other jobs.
The scheduler job's attempt deadline must exceed the worst case of one run at the per-run cap, and the endpoint is idempotent, so a scheduler retry is harmless.

**Listing.** With two runs a day, `history.list` and a stored `historyId` buy nothing.
The transport lists inbox messages from the last 7 days that do not carry the `VH-Bot/Seen` label.
That is stateless and self-healing: a run that dies halfway leaves its unfinished mail unlabelled, and the next run picks it up; the unique index turns any overlap into a no-op.
The window bounds the self-healing: mail that stays unlabelled for more than 7 days, for example through an outage or a revoked grant, is never triaged or escalated by the bot.
It stays in the inbox for humans, and the re-consent runbook ends with a hand triage of `in:inbox -label:VH-Bot/Seen older_than:7d`.
`VH-Bot/Seen` is applied last, after the audit row is committed.

**Labels.** `VH-Bot/Seen`, `VH-Bot/Drafted`, `VH-Bot/Sent`, `VH-Bot/Escalated`, `VH-Bot/Skipped`, `VH-Bot/Paused`, and one label per category under `VH-Bot/`.
Missing labels are created on the first run.

**Quota.** A poll of a low-volume inbox is a few hundred quota units a day against a per-minute limit in the millions.

**Fallback.** If the consent is refused at setup time, D6 stands as approved: `ImapSmtpTransport` implements the same `MailTransport` protocol over IMAP with Gmail's `X-GM-THRID` and `X-GM-LABELS` extensions and the existing SMTP path, using `GMAIL_APP_PASSWORD`.
Nothing above the transport changes.
It is documented, not built, until it is needed.

**Blocking I/O.** The poll endpoint is a plain `def` route, so FastAPI runs it in the threadpool and the synchronous Gmail, classifier and Supabase clients do not block the event loop.
`EmailBotPipeline.run()` is therefore synchronous, but `BotService.chat()` and `BotService.sync_documents()` are `async def`.
The pipeline reaches `chat()` through one bridge, `anyio.from_thread.run`, which hands the coroutine back to the event loop that owns the worker thread; `asyncio.run` inside the threadpool is not used.
Tests drive the pipeline the same way, through `anyio.to_thread.run_sync`.
`POST /admin/email-bot/sync-knowledge-base` is an `async def` route that awaits `sync_documents()` directly.
The Messenger prerequisite in the phase-0 plan's carry-over list still stands for a live Page and is unaffected.

## Classification

Deterministic guards run first and cost nothing.
A message is skipped without any model call when any of these hold: `Auto-Submitted` other than `no`; `Precedence: bulk`, `list` or `junk`; `List-Id` or `List-Unsubscribe`; `X-Auto-Response-Suppress`; a sender local part of `no-reply`, `noreply`, `mailer-daemon`, `postmaster` or `bounce`; the sender is the inbox itself; or Gmail's own `CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `CATEGORY_UPDATES` or `CATEGORY_FORUMS` label.

The classifier answers four typed questions in one call: `category`, `language` (`en`, `vi`, `other`), `asks_for_human`, and `mentions_money_or_commitment`.
The category instructions say to pick the highest-tier category present, which is how the parent spec's rule that any escalating part escalates the whole mail is applied.

The tier is derived in code by `derive_tier()`, never chosen by the model:

1. An executive category, or `mentions_money_or_commitment`, gives `needs_executive`.
2. Otherwise the `automated` category gives `skip`, whatever the confidence, so the second line of defence behind the header guards never produces a holding reply.
3. Otherwise `language == other`, `asks_for_human`, or a confidence below `TRIAGE_CONFIDENCE_THRESHOLD`, gives `needs_admin`.
4. Otherwise the category's own tier from the table.

| Category | Tier | Handling |
|---|---|---|
| `signup` | `auto_answer` | Fixed template in the sender's language, no retrieval |
| `faq` | `auto_answer` | `BotService.chat()`, then the confidence gate; below it, holding message and `needs_admin` |
| `volunteer_ops` | `needs_admin` | Holding message, forward, Discord |
| `sponsorship`, `donation` | `needs_executive` | Holding message, forward, Discord (money and commitments) |
| `partnership`, `press` | `needs_executive` | Holding message, forward, Discord (partnerships, media, brand) |
| `safeguarding_legal` | `needs_executive` | Holding message, forward, Discord flagged urgent |
| `acceptance` | `needs_executive` | Holding message, forward, Discord; never answered from any sheet |
| `human_other` | `needs_admin` | Holding message, forward, Discord |
| `automated` | `skip` | No reply, `VH-Bot/Skipped`, audit row; a second line of defence behind the header guards |

The two classifier implementations:

- `JevClassifier` calls `jev-latest` through the vendor SDK and returns native per-option probabilities, whose spread is the confidence that feeds `TRIAGE_CONFIDENCE_THRESHOLD`.
- `LiteLLMClassifier` asks one model for a JSON-schema object with the same four fields; the model string is the `TRIAGE_FALLBACK_MODEL` setting, `gemini/gemini-3.5-flash-lite` by default and `anthropic/claude-haiku-4-5-20251001` as the swap.
  Its self-reported confidence is not calibrated, which is why it is the shadow rather than the decider while Jev is being evaluated.

`ShadowingClassifier` runs the decider, then the shadow, records both on the message row, and returns the decider's decision.
Which implementation decides is the `TRIAGE_CLASSIFIER` setting (`jev` or `litellm`); flipping it needs no deploy.
Jev's Vietnamese support is undocumented and the vendor is early access, which is exactly what the golden-set gate and the shadow exist to expose.

## Answering

**Sign-up.** The template is a fixed string per language, rendered by `replies.py` from settings: teaching days from `SCHEDULE_TEACHING_DAYS` (localised day names), class time from `CLASS_START_TIME` and `CLASS_END_TIME`, the form from `VOLUNTEER_SIGNUP_FORM_LINK`.
The captain's English copy, verbatim apart from the placeholders:

> Thank you very much for your message and interest in volunteering with Vietnam Hearts 💚
>
> We teach in HCM {{ teaching_days }} from {{ class_time }} and are always looking for volunteers to come in to support our teachers as TA or to teach.
>
> If you in the area and interested, you can fill out the form below! we will email you all our onboarding and send you a link to our groupchat when its approved.
>
> {{ signup_form_link }}

Proposed Vietnamese rendering, pending native-speaker sign-off, a fresh rendering rather than a literal translation so both read naturally:

> Cảm ơn bạn rất nhiều vì đã nhắn tin và quan tâm đến việc làm tình nguyện viên cùng Vietnam Hearts 💚
>
> Chúng mình dạy tại TP.HCM vào {{ teaching_days }} từ {{ class_time }} và luôn tìm kiếm tình nguyện viên đến hỗ trợ giáo viên với vai trò trợ giảng hoặc đứng lớp.
>
> Nếu bạn ở gần và quan tâm, bạn có thể điền vào biểu mẫu dưới đây nhé! Khi đơn được duyệt, chúng mình sẽ gửi email hướng dẫn và link nhóm chat cho bạn.
>
> {{ signup_form_link }}

The template points only to the form.
Onboarding and the group chat are sent by the existing confirmation email after approval, so the template never mentions them by link.

**FAQ.** `BotService.chat()` stays the only generation path.
Three changes make it safe for email: an empty assembled context raises `NoRelevantContext` instead of reaching the generator (the phase-0 carry-over); the prompt instructs the model to emit a refusal sentinel when the context does not answer the question, and the sentinel is treated as `NoRelevantContext`; and the email prompt adds prohibitions: no dates or commitments not in the context, no acceptance or rejection language, no amounts, no personal data, no promised follow-up timing, answer only the answerable parts and say a person will follow up on the rest, and answer in the sender's language.
The confidence gate compares the top retrieval similarity to the `ANSWER_THRESHOLD` setting, starting at 0.5 and calibrated in E2.

**Knowledge base.** One curated Google Doc, id in the `KNOWLEDGE_BASE_DOC_ID` setting, ingested by `POST /admin/email-bot/sync-knowledge-base`, a thin endpoint over the existing document sync in `BotService`, and re-synced daily by the `sync-knowledge-base` job.
The parent spec's `app/routers/bot.py` stays unwired pending its phase 1 restructure.
Its first section carries three rules: only facts the organisation is happy to state publicly; no personal data about anyone; and a "never answer, always escalate" list mirroring the four executive categories.
The re-embed script must have been run and `has_indexed_documents()` must be true before the first draft is produced.

**Holding message.** The parent spec's copy, unchanged, in the sender's language, with a signature line and `Auto-Submitted: auto-replied`.
Senders in a language other than English or Vietnamese get the English version and `needs_admin`.

## Escalation

Every `needs_admin` and `needs_executive` outcome does all of the following, in every mode:

1. Labels the thread `VH-Bot/Escalated` plus its category label.
2. Forwards the original mail to `ESCALATION_OWNER_EMAIL` through the existing `EmailService` SMTP path (parent spec D12), with a three-line header, category, one-sentence summary, why it escalated, and the original quoted below it.
   The original is the best context and needs no re-typing; the Gmail transport itself is not involved in sending.
3. Posts to Discord through `DISCORD_WEBHOOK_URL`: category, tier, language, the summary, and a link to the Gmail thread.
   `safeguarding_legal` is prefixed `URGENT` and mentions `@here`.
   The post never contains the sender's address or the body.
4. Pauses the conversation for the bot.
5. Gives the sender the holding message, as a draft in `draft` mode and as a send in `auto` mode, unless the thread already has its one bot reply; then the sender gets nothing and steps 1 to 4 still happen.

The summary is generated by the existing Gemini client only on escalation, with an instruction to omit names and contact details.
If summary generation fails, the forward and the post go out without it.

## Safety controls

| Control | Design |
|---|---|
| Deterministic guards | The header and label rules above run before any model and before any row is written; a skipped mail costs nothing and gets no reply |
| One bot reply per thread | `bot_reply_count` is capped at 1. A second inbound on a thread the bot already replied to is `needs_admin`: it is forwarded and posted to Discord like any escalation, and the sender gets no second reply |
| Never talk over a human | Before acting, the thread is read from Gmail. Any message from the inbox address that is not one the bot sent (by recorded id) moves the thread to `paused_manual`; the bot only labels, and deletes its own outstanding draft. A captain sending a bot draft counts as a human reply |
| Send caps | `EMAIL_BOT_PER_RUN_CAP` (default 20) bounds messages processed per run in any mode, which also bounds model calls against the Gemini free tier. `EMAIL_BOT_DAILY_SEND_CAP` (default 30) and `EMAIL_BOT_PER_SENDER_DAILY_CAP` (default 2 threads) bound sends in `auto`; the account's 500-per-day limit is shared with the weekly reminder blast. Leftovers wait for the next run |
| Kill switch | `EMAIL_BOT_MODE` setting: `off`, `draft`, `auto`; editable on the dashboard, read at the start of every run, no deploy. Any other value reads as `off`. `EMAIL_BOT_ENABLED` env var is the operator-level stop. Pausing the scheduler job and revoking the OAuth grant are the two hard stops |
| Draft mode by construction | In E1 the transport has no `send_reply` method, `messages.send` is not called anywhere in the codebase, and the only outbound mail path is `EmailForwardNotifier`, whose recipient is `ESCALATION_OWNER_EMAIL` and nothing else. `SendSink` arrives in E3 and is the only caller of `send_reply`. Tests fail if the Gmail client mock sees a send, or the SMTP mock sees any other recipient |
| Per-language automatic sending | `EMAIL_BOT_AUTO_LANGUAGES` (default `en`) lists the languages that may be sent automatically; any other language is drafted even in `auto`. Vietnamese joins the list after native-speaker sign-off |
| Fail closed | Low confidence, no context, the refusal sentinel, or an unknown language give `needs_admin` with the holding message. A classifier, retrieval or generation outage is retried with backoff and then also gives `needs_admin`, never a guess (parent spec D9) |
| Circuit breaker | Three consecutive infrastructure failures in one run abort the run, leave the remaining mail unlabelled for the next run, record `aborted_reason`, and report to Sentry. A systemic outage must not turn into twenty forwards |
| Idempotence | Unique index on the Gmail message id; the inbound row is committed before any side effect; `VH-Bot/Seen` is applied last. A re-listed message re-applies labels and does nothing else |
| RFC 3834 | Outbound replies carry `Auto-Submitted: auto-replied` and `X-Auto-Response-Suppress: All`, so well-behaved auto-responders stay silent. Inbound guards catch the rest |
| Privacy of logs | Log lines carry Gmail ids, category, tier, language and action; never a subject, body or address, because logs persist to the database. Exception messages raised by the pipeline carry ids only, since Sentry runs with `send_default_pii=True` for request debugging. The database stores sender hashes and outbound bot text, not inbound bodies. The existing `Processing chat message` line in `BotService.chat()`, which logs the first 100 characters of the message, is removed. A test drives the real `BotService.chat()`, with only retrieval and generation mocked, and checks captured log output for `@` and for fixture body text |
| Audit | Every action is a `messages` row with `action` and the id of anything created, and every run is an `email_bot_runs` row; labels mirror both inside Gmail |

## Language handling

Vietnamese and English are both required.
The classifier reports `language`; the sign-up template and the holding message pick the matching version; the generator is told to answer in the sender's language.
`other` gives `needs_admin` with the English holding message, through `derive_tier()` rule 3.
Automatic sending is gated per language by `EMAIL_BOT_AUTO_LANGUAGES`, so the Vietnamese templates can ship as drafts before they are signed off.
The golden set carries both languages for every category, and the executive-recall gate is measured on both.

## Configuration and secrets

Environment variables (secrets, set on the Cloud Run service out of band, never in the repo):

| Variable | Purpose |
|---|---|
| `EMAIL_BOT_ENABLED` | Operator-level stop, default `false` |
| `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN` | The bot's Gmail grant |
| `TYPESAFE_API_KEY` | Jev |
| `DISCORD_WEBHOOK_URL` | Escalation posts |
| `ANTHROPIC_API_KEY` | Only when `TRIAGE_FALLBACK_MODEL` is a Claude model |
| `GEMINI_API_KEY` | Existing; the fallback classifier's default model and the answer path |

Settings (database, dashboard-editable, read per run):

| Setting | Default | Purpose |
|---|---|---|
| `EMAIL_BOT_MODE` | `off` | `off`, `draft`, `auto` |
| `EMAIL_BOT_AUTO_LANGUAGES` | `en` | Languages that may be sent automatically |
| `EMAIL_BOT_LAST_ERROR` | empty | Written by the pipeline on a revoked grant or an aborted run; shown as the dashboard banner and cleared by the next clean run |
| `CRON_POLL_INBOX` | `0 8,18 * * *` | Poll cadence, Vietnam time |
| `CRON_SYNC_KNOWLEDGE_BASE` | `0 5 * * *` | Daily knowledge-base sync |
| `KNOWLEDGE_BASE_DOC_ID` | empty | The curated Google Doc |
| `ESCALATION_OWNER_EMAIL` | empty | Forward recipient; the bot does nothing in `draft` or `auto` while it is empty |
| `TRIAGE_CLASSIFIER` | `jev` | `jev` or `litellm` decides |
| `TRIAGE_FALLBACK_MODEL` | `gemini/gemini-3.5-flash-lite` | LiteLLM model string |
| `TRIAGE_CONFIDENCE_THRESHOLD` | `0.6` | Below it, `needs_admin` |
| `ANSWER_THRESHOLD` | `0.5` | Retrieval similarity gate |
| `EMAIL_BOT_PER_RUN_CAP`, `EMAIL_BOT_DAILY_SEND_CAP`, `EMAIL_BOT_PER_SENDER_DAILY_CAP` | `20`, `30`, `2` | Caps |
| `VOLUNTEER_SIGNUP_FORM_LINK` | empty | Sign-up form; the template is not rendered while it is empty |
| `CLASS_START_TIME`, `CLASS_END_TIME` | `09:30`, `10:30` | Rendered per language |
| `SCHEDULE_TEACHING_DAYS` | existing | Rendered per language |

Model choice, thresholds and caps are settings so that E2's calibration and the Jev decision need no deploy.
Keys are environment variables because they are secrets.

## Error handling

| Failure | Behaviour |
|---|---|
| Gmail API unreachable | The run fails loudly to Sentry, leaving no partial state; unlabelled mail waits for the next run |
| `invalid_grant` on refresh | `GmailAuthRevoked`; mode flips to `off`; dashboard banner; Sentry; re-consent runbook |
| Classifier unavailable | Retry with backoff, then `needs_admin`; counts toward the circuit breaker |
| Embeddings, retrieval or generation unavailable | `needs_admin`, no fallback (parent spec D9); counts toward the circuit breaker |
| Summary generation fails | Forward and Discord post go out without the summary |
| Discord webhook fails | Logged and reported; the forward is the durable record, so the escalation is not lost |
| Send fails in `auto` | Logged and reported, no retry loop; the thread stays unpaused and the mail is not relabelled `Sent` |
| Caps reached | Remaining answerable mail is drafted instead of sent, and the run records it |

## Evaluation gate

The parent spec's gate applies, with two additions from real use in draft mode.

- 100 percent recall on `needs_executive` over the bilingual golden set, for whichever classifier is to decide in `auto`.
  Jev keeps the deciding role only if it meets this; otherwise `TRIAGE_CLASSIFIER` flips to `litellm` and nothing else changes.
- At least 90 percent correctness on `auto_answer` cases, judged for groundedness against `must_mention` facts.
- Draft acceptance: at least 90 percent of sign-up drafts and at least 80 percent of FAQ drafts sent unchanged by the captain over two weeks, measured by `draft_outcome`.
- Zero bot replies on threads a human had already answered, over the same two weeks.

The harness runs on demand, not in blocking CI, because it consumes API calls.

## Evaluation record

*Not yet filled in.* The evaluation cannot be run: it needs the Jev and Gemini
keys, which do not exist yet, and two calendar weeks of draft mode on the real
inbox to measure draft acceptance. The harness that produces every number below
is built and its arithmetic is tested in CI (`evals/`, `evals/README.md`).

The numbers are deliberately absent rather than estimated. This section is what
a future reader will use to answer "why is the threshold 0.55, and who decided
that", so a plausible-looking guess here would be worse than a gap.

Fill it in from one pass of the harness, on the date it was run:

| Measurement | How | Gate | Result |
|---|---|---|---|
| Executive recall, `jev` | `evals/run_triage_eval.py --classifier jev` | 100 percent, both languages | |
| Executive recall, `litellm` | `evals/run_triage_eval.py --classifier litellm` | 100 percent, both languages | |
| Category accuracy, deciding classifier | same run | no gate, recorded | |
| Language accuracy, deciding classifier | same run | no gate, recorded | |
| Answer correctness on `auto_answer` | `evals/run_groundedness_eval.py` | at least 90 percent | |
| Sign-up drafts sent unchanged | dashboard, over two weeks | at least 90 percent | |
| FAQ drafts sent unchanged | dashboard, over two weeks | at least 80 percent | |
| Bot replies on threads a human had answered | audit table, same two weeks | zero | |
| Deciding and shadow agreement | `evals/shadow_report.py` | no gate, recorded | |

And the decisions that follow from them:

| Setting | Chosen value | Why |
|---|---|---|
| `TRIAGE_CLASSIFIER` | | |
| `TRIAGE_CONFIDENCE_THRESHOLD` | | |
| `ANSWER_THRESHOLD` | | |

`evals/calibrate_thresholds.py` prints the trade-off table the two thresholds
are chosen from. Read the "wrong" column of the `ANSWER_THRESHOLD` sweep first:
every entry in it is a wrong answer that went to a member of the public.

## References

- Parent spec: `docs/superpowers/specs/2026-07-28-qna-bot-design.md`
- Implementation plan: `docs/superpowers/plans/2026-09-29-email-channel-implementation.md`
- Google OAuth token expiration: https://developers.google.com/identity/protocols/oauth2#expiration
- Google restricted-scope verification exemptions: https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification
- Unverified apps: https://support.google.com/cloud/answer/7454865
- Gmail API quota: https://developers.google.com/workspace/gmail/api/reference/quota
- Gmail API sending and threading: https://developers.google.com/workspace/gmail/api/guides/sending
- Gmail IMAP extensions (fallback): https://developers.google.com/workspace/gmail/imap/imap-extensions
- Gmail sending limits: https://support.google.com/mail/answer/22839
- RFC 3834, recommendations for automatic responses to electronic mail: https://www.rfc-editor.org/rfc/rfc3834
- Jev and System One models: https://typesafe.ai/blog/introducing-system-one-models-and-jev and https://docs.typesafe.ai/
