# The inbox bot on inbox-core

The volunteer inbox bot's channel-neutral engine lives in [inbox-core](https://github.com/vietwillnguyen/inbox-core), an MIT package extracted from this bot.
This page says which parts come from the package, which stay in this repository and why, how the package is pinned, and what the package still lacks before the bot can hand it the whole run.

## What comes from inbox-core

| Part | From the package | Bound here by |
|---|---|---|
| Message shapes | `IncomingMessage`, `OutboundReply`, and the email extension (`EmailDetails`, `EmailReplyDetails`) | `app/services/channels/gmail.py` (`parse`, `email_reply`) |
| Category table and tiers | `ClientConfig`, `load_config`, `decide`, `derive_tier`, `escalation_reason` | `app/services/email_bot/inbox_client.toml`, `client.py` |
| Classifiers | `JevClassifier` (decides), `LiteLLMClassifier` (shadows), `ShadowingClassifier`, prompts rendered from the config | `app/services/email_bot/factory.py` |
| Answers | `GroundedAnswer`, the `Handler` protocol, `HandlerRegistry` | `app/services/email_bot/answers.py`, `replies.py` |
| Delivery | `Delivery`, `DeliveryMode`, `parse_mode`, the `ReplySink` and `SendCaps` protocols | `app/services/email_bot/delivery.py`, `GmailReplySink`, `CapsState` |
| Field encryption | `FieldCipher`, key parsing and generation | `app/utils/field_crypto.py` |
| Eval arithmetic | `TriageMetrics`, `GroundednessMetrics`, both threshold sweeps | `evals/metrics.py`, `evals/calibrate_thresholds.py` |

The tier is still decided in code and never by a model: the core's `derive_tier` reads the table in `inbox_client.toml`, and `tests/test_inbox_client_policy.py` pins every row of it.

## What stays here, and why

- **The Gmail channel.** Listing, parsing, labels, drafts, sends, the human-reply check and the revoked-grant switch-off are Gmail's, so they stay in `app/services/channels/`.
  `GmailReplySink` is the core's `ReplySink`: a held reply is a Gmail draft in the thread, and it can only send when it is built for `auto`.
- **The run itself.** `app/services/email_bot/pipeline.py` still orders a poll: guards, the human-reply check, triage, the one-reply-per-thread rule, the handler, delivery, the forward and Discord post, labels, the circuit breaker and the run row.
  The core's own `Pipeline.process` cannot yet reproduce that run without changing what the bot does; the gaps below are why.
- **The conversation record.** Rows, encrypted content, admin-only reading with every view recorded, and deletion on request stay in `conversation_service.py` and `conversation_store.py`, because the run above writes them.
- **The email answer prompt.** `answers.py` keeps the email rules, including "never say or imply that an application has been accepted or rejected" and "never state an amount of money", because the core's answer instructions have no way to carry a client's own rules.
- **The vector search and generation** the FAQ answer plugs into the core (`KnowledgeBaseRetriever`, `KnowledgeBaseGenerator`), the sign-up copy, the holding message and the signature.
- **Send caps** counted from this app's `messages` rows (`CapsState`), because the core's store-backed caps need the core's whole conversation store.
- **The evals' golden set, loader and judge.** The core's golden-set format has no headers for the guard cases, and its groundedness runner only reads Markdown knowledge, not this app's vector store.

## The pin

`pyproject.toml` pins inbox-core by git URL to commit `8651bf477bc89affb107060605364427eae58372`, the tip of the package's pull request #1 at the time.
The v0.1.0 release is not enough: its `FieldCipher` cannot decrypt an encrypted empty string, and every mail with an empty subject or body stores one.
Before this leaves draft it must be re-pinned to the first tagged release that contains that fix.
The Docker builder stage installs `git` so `uv` can fetch the source.

## Behaviour that changed in the move

Recorded in `tests/fixtures/email_bot_characterisation.json`, whose history shows each change:

- The Jev classifier's recorded name is `jev:jev-latest` rather than `jev`.
- An escalation for an unsupported language reads "unsupported language code" rather than "unsupported language other".
- An FAQ that is handed to a person says why in the core's words: "no knowledge entry scored at least 0.50", "the generator found the context insufficient", or "generator gemini:... failed (...)".
- The FAQ retrieval is queried with the subject and the body rather than the body alone.
- The generator sees, and the outbound row records as sources, only the passages that clear `ANSWER_THRESHOLD`, rather than the top three whenever the best one did; when none clears it the generator is not called at all.
- The classifier prompts are rendered from `inbox_client.toml` and read "message" where they read "email"; the money question says "a price" where it said "salary"; the LiteLLM call has a 10 second timeout.
- `TRIAGE_CONFIDENCE_THRESHOLD=0` is refused by the core, so it now falls back to the default 0.6 with a warning.

None of these changes a tier, a label, a recipient or whether a reply is drafted or sent in the recorded scenarios.
The prompt changes cannot be seen with fakes, so the triage and groundedness evals should be re-run against the real models before the bot is switched on.

## What inbox-core lacks

Each of these is why a piece above still lives here.
None was worked around by copying the core's logic into this repository.

1. **A holding message ignores the per-conversation reply cap.**
   `Pipeline._hand_off` delivers the holding message whenever a holding text is configured, even in a conversation where the bot has already replied; this bot sends nothing further in such a thread.
   Proposed: `_hand_off` skips the holding message when `conversation.bot_reply_count >= max_bot_replies_per_conversation` (or a `DeliveryPolicy.holding_counts_toward_cap` switch).
2. **A follow-up in a handed-off conversation tells nobody.**
   `Pipeline._run_leased` marks any message in a non-`bot` conversation `left_for_person` without a notification; this bot re-triages it and forwards it to the escalation owner, without a holding message.
   Proposed: a policy (`TriagePolicy.notify_follow_ups_after_hand_off` or similar) under which a message in a `paused_handoff` conversation is triaged and handed off with no holding message, while `paused_manual` stays silent.
3. **Message content is stored before the guards run.**
   `record_inbound` writes the subject, body and address before `run_guards`, so automated mail, mail from the organisation itself and mail with no usable sender is kept; this bot keeps no content for guard skips.
   Proposed: run `drop` guards before `record_inbound`, or `record_inbound(..., keep_content=False)` followed by `ConversationStore.keep_content(row_id, message)` once the guards pass.
4. **The answer instructions cannot carry a client's rules.**
   `render_answer_instructions` has no per-client additions.
   Proposed: `ClientConfig.answer.rules` (a list of extra rule lines) rendered into the instructions.
5. **A handler's decline has no structured reason.**
   `GroundedAnswer` returns a free-text reason only, so a client cannot tell a knowledge gap from an outage (which this bot counts toward its circuit breaker) without matching strings, and cannot word the reason itself.
   Proposed: `HandlerResult.code` (`no_passage`, `refused`, `retrieval_failed`, `generation_failed`) next to `reason`.
6. **`GroundedAnswer`'s retrieval query is fixed.**
   It searches with the subject and the body joined; this bot searched with the body.
   Proposed: a `query` callable on `GroundedAnswer`, defaulting to today's behaviour.
7. **`escalation_reason` renders the reserved `other` language as `code`.**
   `_SAFE_CODE` does not admit `other`, the one code a classifier is told to use for an unsupported language.
   Proposed: allow `RESERVED_LANGUAGE` explicitly.
8. **`Escalation` carries too little for an email forward.**
   It has no language and no confidence, and no handle on the message, so a notifier that forwards the original mail (as this bot's does) has to look all three up itself.
   Proposed: add `language` and `confidence`, both content-free.
9. **The pipeline's reply has no channel extension.**
   `Pipeline` builds `OutboundReply` without `EmailReplyDetails`, so a Gmail sink cannot thread the reply (subject, References, In-Reply-To) without its own lookup of the inbound message; `EmailReplyDetails` also has no field for the parent's RFC 5322 Message-ID, so this app puts it last in `references`.
   Proposed: a channel hook that supplies the reply extension from the inbound message, and an `in_reply_to` field on `EmailReplyDetails`.
10. **`StoreSendCaps` needs the whole `ConversationStore`.**
    It reads only `replies_sent_between`.
    Proposed: type it against a one-method `SentReplies` protocol.
11. **The golden set has no headers, and the groundedness runner only takes Markdown knowledge.**
    This bot's golden set carries headers for its guard cases, and its knowledge is a vector store.
    Proposed: an optional `headers` field on `GoldenCase`, and a CLI path that takes a client-supplied `Handler`.
12. **An outage inside a handler is not visible to the caller.**
    The bot stops a poll after three consecutive infrastructure failures.
    The core's audit record marks an unavailable classifier (`classifier == "unavailable"`) and a failed delivery (`delivery == "failed"`), but a retrieval or generation outage inside `GroundedAnswer` only as reason text, so this app's retriever and generator record outages themselves (`AnswerOutages`).
    Proposed: an `AuditRecord.failure` field naming the failing step, which item 5 would feed.

Closing 1 to 3 is what would let `pipeline.py` hand each message to `Pipeline.process`, with `ConversationStore` implemented over this app's encrypted tables.
