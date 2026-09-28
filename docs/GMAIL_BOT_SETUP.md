# Volunteer inbox bot: setup runbook

Everything the volunteer inbox bot needs before it can run, in the order it has to happen.
The engineer's half is committed as code; this file is the captain's half, written so it can be repeated from scratch if the grant is ever lost.

Read `docs/superpowers/specs/2026-09-29-email-channel-design.md` for why any of this is shaped the way it is.

## What the bot can and cannot do after this

Once the steps below are done and `EMAIL_BOT_MODE` is set to `draft`, the bot reads the volunteer inbox twice a day and, for each new mail, does exactly three things: applies Gmail labels, saves a suggested reply as a Gmail draft inside the thread, and forwards anything that needs a person to one configured address with a Discord notice.

In `draft` it sends nothing to a member of the public.
Only `auto` sends, and only sign-up replies, confident FAQ answers and holding messages, in-thread, in the languages listed in `EMAIL_BOT_AUTO_LANGUAGES`, one reply per thread, and under the daily and per-sender caps; a reply held back by any of those is drafted instead.
`GmailTransport.send_reply` is the only send path in the repository, and a test fails if a second one appears.
Do not set `auto` until the loop test and the canary week below have been done.

## Before you start

You need:

- Access to the Google Cloud project the service already uses.
- The password for the volunteer inbox Gmail account (`vietnam.hearts.volunteering@gmail.com`).
- Permission to add secrets in Secret Manager and variables on the Cloud Run service.
- Admin rights on the Discord server that should receive escalations.

Set aside an uninterrupted hour.
Step 3 involves a consent screen that only issues a refresh token the first time, so being rushed is how it gets done twice.

## Step 1: Enable the Gmail API and create a Desktop OAuth client

1. Google Cloud console > **APIs & Services** > **Library** > search "Gmail API" > **Enable**.
2. **APIs & Services** > **Credentials** > **Create credentials** > **OAuth client ID**.
3. Application type: **Desktop app**. Name it something like `vietnam-hearts-inbox-bot`.
4. Download the JSON. Keep it out of the repository; it is a credential.

This is deliberately a **new, separate** client from the web client in `GOOGLE_OAUTH_CLIENT_ID`, which Supabase auth uses for admin sign-in.
Two reasons: this one holds a single Gmail scope and nothing else, and it can be revoked without breaking anyone's ability to log in to the dashboard.

**While you are on this page, rotate `GOOGLE_OAUTH_CLIENT_SECRET`** (repository issue #7: it leaked in public repository history).
Doing it in the same sitting is the only reliable way it gets done.

## Step 2: Configure the consent screen

1. **APIs & Services** > **OAuth consent screen**.
2. User type: **External**.
3. Publishing status: **Published** ("In production"). Do **not** submit for verification.
4. Scopes: add exactly one, `https://www.googleapis.com/auth/gmail.modify`. Nothing else.

Both of these choices matter and they are commonly conflated.

**Published, not Testing.** Google expires refresh tokens after 7 days for apps in *Testing* status.
That expiry is a property of the publishing status, not of verification, so publishing is what makes the token durable.
Step 4 is how you confirm it actually worked.

**Not submitted for verification.** `gmail.modify` is a restricted scope, but Google exempts an app used only by its owner from verification and from the CASA security assessment.
The volunteer inbox is granting access to its own mailbox, so that exemption applies.
You will see an "unverified app" warning during consent; that is expected, and you are the owner being warned about your own app.

## Step 3: Run the consent flow and store the refresh token

Sign in to the browser as the **volunteer inbox account** first. Check this twice: the grant is issued for whoever is signed in, and a token for the wrong mailbox fails in a confusing way much later.

```bash
uv run python scripts/gmail_oauth_consent.py --client-secrets ~/Downloads/client_secret_....json
```

A browser opens. Approve the unverified-app warning. The script prints a refresh token to the terminal and writes nothing to disk.

Store three values in Secret Manager and expose them to the Cloud Run service, the same way the existing secrets are set:

| Secret | Value |
|---|---|
| `GMAIL_OAUTH_CLIENT_ID` | the client id from the downloaded JSON |
| `GMAIL_OAUTH_CLIENT_SECRET` | the client secret from the downloaded JSON |
| `GMAIL_OAUTH_REFRESH_TOKEN` | what the script printed |

Then delete the downloaded JSON from your machine.

If the script reports that Google returned no refresh token, the client has already been consented to once.
Revoke it at [myaccount.google.com/permissions](https://myaccount.google.com/permissions) and run the script again; Google only issues a refresh token on a first consent.

## Step 4: Confirm the token on day 8

Put a reminder in your calendar for **eight days** after step 3, then:

```bash
export GMAIL_OAUTH_CLIENT_ID=... GMAIL_OAUTH_CLIENT_SECRET=... GMAIL_OAUTH_REFRESH_TOKEN=...
uv run python scripts/gmail_oauth_consent.py --check
```

It prints the address the grant belongs to, and exits non-zero if the token has expired.

This check is the whole point of the 7-day distinction in step 2.
A token that still refreshes on day 8 proves the consent screen is genuinely published, which reading the console UI does not reliably tell you.
If it has expired, the screen is still in Testing: fix the publishing status and redo step 3.

## Step 5: Discord webhook and the escalation recipient

1. In Discord, open the channel that should receive escalations > **Edit Channel** > **Integrations** > **Webhooks** > **New Webhook** > **Copy Webhook URL**.
2. Store it as the `DISCORD_WEBHOOK_URL` secret on the Cloud Run service.
3. On the admin dashboard, set **`ESCALATION_OWNER_EMAIL`** to the address escalated mail should be forwarded to.

The bot refuses to run at all while `ESCALATION_OWNER_EMAIL` is empty.
An escalation with nowhere to go would be dropped silently, so the bot would rather do nothing than draft replies while losing the handoffs.

What each channel carries is deliberately different.
The **forward** is the durable record: the original mail, quoted in full, to one address.
The **Discord post** is the fast notice and carries only the category, the tier, the confidence, a one-sentence summary and a link to the thread - never the sender's address and never the body, because a chat channel has a wider audience than a mailbox.
`safeguarding_legal` is prefixed `URGENT` and mentions `@here`.

## Step 6: Jev API key

Create a key at the TypeSafe console and store it as the `TYPESAFE_API_KEY` secret.

Jev is the deciding triage classifier. If the key is missing the bot still works: it logs the mismatch loudly and decides with the LiteLLM classifier instead, so a missing key degrades quality rather than stopping the inbox from being triaged.

## Step 7: Throwaway test accounts (engineer, not captain)

Two disposable Gmail accounts, neither of them the real inbox:

- one to play the **inbox under test**, with its own consent token from step 3, kept in the engineer's local `.env` only;
- one to play the **public sender**, with an app password.

The real volunteer inbox is touched only by the captain's own consent in step 3 and by production.
Nothing in the test suite or the seeding script may point at it: `scripts/seed_test_inbox.py` refuses that address outright, and `tests/test_email_bot_live.py` reads the granted address back from `users.getProfile` and refuses to continue if it is the real one.

Seed the test inbox and run a poll against it:

```bash
uv run python scripts/seed_test_inbox.py \
    --corpus tests/fixtures/sample_corpus.yaml \
    --to <throwaway-inbox>@gmail.com \
    --from-address <throwaway-sender>@gmail.com \
    --i-confirm-this-is-a-throwaway-account

# then, against a local instance pointed at the throwaway inbox:
curl -X POST localhost:8080/admin/email-bot/poll -H "apikey: $SUPABASE_SECRET_KEY"
```

Inspect the result in the throwaway inbox's Gmail UI and in the test Discord channel, which is the closest an end user comes to this feature.
Payloads worth keeping are anonymised and committed under `tests/fixtures/gmail_recorded/`.

## Step 8: The curated knowledge base doc

1. Create one Google Doc. This is the only thing the bot answers FAQ questions from.
2. Share it read-only with the runtime service account (`SERVICE_ACCOUNT_EMAIL`).
3. Put its document id in the `KNOWLEDGE_BASE_DOC_ID` setting on the dashboard.

Its first section carries three rules, and they are rules about the doc rather than advice:

1. **Only facts the organisation is happy to state publicly.** Anything in this doc can be quoted back to a stranger.
2. **No personal data about anyone.** No names, no addresses, no phone numbers, no details about a volunteer or a child.
3. **A "never answer, always escalate" list**, mirroring the four executive categories: money and sponsorship, partnerships and press, safeguarding and legal, and whether an application was accepted.

The volunteer signup responses sheet, the schedule sheet and the volunteers table are **never** a retrieval source.
They hold passport numbers, dates of birth and addresses, and nothing in this design reads them.

Before the first draft is produced, the knowledge base has to be populated:

```bash
uv run python scripts/reembed_knowledge_base.py     # once, against production
curl -X POST <service-url>/admin/email-bot/sync-knowledge-base -H "apikey: ..."
curl <service-url>/health                            # confirm documents are indexed
```

## Step 9: Create the scheduler job and turn the bot on

```bash
./scripts/create-or-update-scheduler-jobs.sh
```

This creates `poll-volunteer-inbox`, pointed at `/admin/email-bot/poll`, with a 600 second attempt deadline.
The cadence it sets is only a bootstrap default; the live cadence is owned by the `CRON_POLL_INBOX` setting and applied by pressing **Apply to Cloud Scheduler** on the dashboard.

Then, in order:

1. Set `EMAIL_BOT_ENABLED=true` on the Cloud Run service. This is the operator-level stop and lives outside the database.
2. On the dashboard, set `EMAIL_BOT_MODE` to `draft`.
3. Set `VOLUNTEER_SIGNUP_FORM_LINK` to the volunteer signup form. While it is empty the sign-up reply is not rendered at all and those mails go to a person instead, because a reply saying "fill out the form below" with no form below is worse than no reply.
4. Check `SCHEDULE_TEACHING_DAYS`, `CLASS_START_TIME` and `CLASS_END_TIME`. These render into the sign-up reply, so a wrong value is a wrong statement to a prospective volunteer.

Watch the **Inbox Bot** card on the dashboard after the next scheduled run.

## Running it day to day

**Reviewing drafts.** Open the thread in Gmail and send, edit, or delete the draft.
All three are useful: the **Inbox Bot** card shows how often drafts are sent unchanged, per answer path, and that number is what decides whether automatic sending is ever turned on.

**Taking over a thread.** Just reply by hand. The bot notices at the next poll, moves the thread to `paused_manual`, and deletes its own outstanding draft.
Only the **Resume bot** button on the dashboard hands the thread back.

**Stopping it.** Four stops, coarsest last:

| Stop | How | Effect |
|---|---|---|
| Mode | Set `EMAIL_BOT_MODE` to `off` on the dashboard | The next poll does nothing. No deploy. |
| Operator | Set `EMAIL_BOT_ENABLED=false` on Cloud Run | The endpoint does nothing and builds no Gmail client. |
| Scheduler | Pause the `poll-volunteer-inbox` job | No polls happen at all. |
| Grant | Revoke the app at [myaccount.google.com/permissions](https://myaccount.google.com/permissions) | The bot loses all access to the mailbox. |

**Reading the card.** Mode, today's sending against the caps, the last run's counters, and open escalations with a link straight into Gmail.
A warning banner means the last run recorded a problem; it clears itself on the next clean run, so a banner that stays means the problem is still there.

## Re-consent

*Completed in phase E4, which adds the `invalid_grant` detection and alert this procedure responds to.*

What is already known and does not depend on E4:

- The most likely cause of a revoked grant is a **password change on the volunteer inbox account**, which invalidates existing refresh tokens. A token also dies if it goes six months unused or if the grant is revoked by hand.
- The repair is steps 2, 3 and 4 of this document again: confirm the consent screen is still published, re-run the consent script signed in as the volunteer inbox, replace `GMAIL_OAUTH_REFRESH_TOKEN` in Secret Manager, and confirm with `--check`.
- **The re-consent is not finished until the gap has been triaged by hand.** The bot lists inbox mail from the last 7 days only, so anything that stayed unlabelled for longer than the outage lasted is never triaged or escalated by the bot at all. Search the inbox for:

  ```
  in:inbox -label:VH-Bot/Seen older_than:7d
  ```

  and deal with those by hand. This is the one failure mode where mail can be silently missed, and the search above is the whole remedy.

## Canary and loop test

Both happen before production ever sends, in this order. The loop test is the
cheaper and the more important of the two: it is the one that proves two
machines cannot end up talking to each other.

### The loop test, on the throwaway accounts

Never on the real inbox.

1. On the **throwaway sender**, turn on Gmail's vacation responder ("Vacation
   responder" under Settings > General), set to reply to everyone.
2. Send exactly one sign-up mail from that account to the throwaway inbox, by
   hand; the `signup-en-1` case in `tests/fixtures/sample_corpus.yaml` will do.
   Not `scripts/seed_test_inbox.py`, which sends the whole corpus.

3. With the local instance in `auto` mode against the throwaway inbox, run
   `POST /admin/email-bot/poll` **twice**, a minute apart.

What has to be true afterwards, in the throwaway inbox's Gmail UI:

- **Exactly one** bot reply in the sign-up thread. Not two, and not one per
  poll.
- The sender's vacation auto-reply carries `VH-Bot/Skipped` and got no reply of
  its own.

The second poll is the whole test. The first sends the sign-up reply; the
vacation responder answers it; and the second poll must recognise that answer as
machine-generated and stop, rather than sending a holding message and starting a
cycle. Three separate controls have to hold for that: the `Auto-Submitted`
header guard, the `automated` category as the second line of defence, and the
one-reply-per-thread cap.

If you see two bot replies, stop and do not proceed to the canary.

Capture the vacation auto-reply's payload and commit it, anonymised, to
`tests/fixtures/gmail_recorded/` so the guard tests carry a real one.

### The canary week, on production

Only after the loop test passes and the E2 evaluation gate is recorded.

1. Set `EMAIL_BOT_DAILY_SEND_CAP` to **10** on the dashboard. Well below the
   design's 30, so a week of a wrong decision is ten mails and not two hundred.
2. Set `EMAIL_BOT_AUTO_LANGUAGES` per the sign-off. `en` alone unless a native
   speaker has signed off the Vietnamese copy.
3. Set `EMAIL_BOT_MODE` to `auto`.
4. **Read the audit table every day** for a week. The Inbox Bot card shows
   today's sending, the last run, and the open escalations; `GET
   /admin/email-bot/runs` has the per-run counters.

What to look for each day, and what it means:

| Look at | Wrong if |
|---|---|
| The `sent` count against what arrived | The bot is answering things it should be escalating |
| The `capped` count | Replies are being held back; either the cap is too low or something is looping |
| Open escalations | They are piling up unread, which is the failure mode that makes the bot worse than nothing |
| Any thread with more than one bot reply | The one-reply cap has failed. Set the mode to `off` immediately |
| Any reply on a thread you had answered | The "never talk over a human" control has failed. Set the mode to `off` immediately |

After a clean week, put `EMAIL_BOT_DAILY_SEND_CAP` back to 30.

Setting `EMAIL_BOT_MODE` to `off` on the dashboard stops sending at the next
run, with no deploy. That is the thing to reach for first if anything above
looks wrong.

## Fallback: IMAP with the app password

If the consent in step 3 cannot be completed, the original approach stands as approved: `ImapSmtpTransport` implements the same `MailTransport` protocol over IMAP, using Gmail's `X-GM-THRID` and `X-GM-LABELS` extensions and the existing `GMAIL_APP_PASSWORD`.
Nothing above the transport changes.

It is documented, not built. Build it only if the consent is actually refused, and say so in the pull request that does.
