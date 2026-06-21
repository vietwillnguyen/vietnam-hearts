CHANGELOG:

## Version 3.1.4

- Fix: Weekly reminder email no longer shows "No data available for {class} (missing Head Teaching Assistant row)". Root cause was a data-model mismatch — the schedule sheet was restructured (most classes no longer have a Head TA row, blank separator rows added) but the email parser still read rows by fixed position, and the Google Sheets API trims trailing empty rows so configured cell ranges silently dropped rows.
- Add: Schedule auto-discovery (`app/services/schedule_parser.py`) — class blocks are now parsed from the Schedule tab by row labels (Teacher / Head Assistant / Assistants MAX N), tolerating missing rows, blank separators, ragged/trimmed rows, and per-class structure differences.
- Change: The "Head Assistant" column is rendered only for classes that actually have a head TA row.
- Change: A blank teacher/role cell now defaults to "Need Volunteers" (❌ Missing Teacher); genuinely off days must be written explicitly as "No Class {reason}".
- Remove: The "Schedule Config" tab dependency and `app/services/classes_config.py`. The Schedule tab is now the single source of truth — `time` and `max_assistants` are read from the sheet itself; the unused `room`/`notes` fields and hardcoded cell ranges are gone. The weekly sheet date-updater now discovers class header rows instead of relying on configured ranges.

## Version 3.1.3

- Fix: sync-volunteers cron job now runs the full pipeline (`/admin/review-and-sync`): LLM judge pending submissions → write ACCEPTED/REJECTED to sheet → sync accepted volunteers to DB → send confirmation emails. Previously the cron only synced rows that were already ACCEPTED, so the judge never ran automatically.
- Fix: Pending-submission detection now requires a non-empty timestamp (proof of a real form entry) and a blank LLM judge score column — rows that already have a judgement are never re-judged, and junk rows without a timestamp are skipped.
- Update: Default CRON_SYNC_VOLUNTEERS schedule changed from every 4 hours to every 2 hours, matching the live Cloud Scheduler job.

## Version 3.1.2

- Fix: LLM judge prompt removes safeguarding scenario questions as a scored rubric item (subjective); replaces with a hard-reject SAFEGUARDING GATE that flags explicit red flags (grooming, predatory language) without influencing the 1-10 numeric rating.
- Fix: Remove stale `{safeguarding_discomfort}` placeholder from judge prompt that was causing a latent KeyError.

## Version 3.1.1

- Fix: LLM judge now processes new form submissions that arrive with a blank applicant_status column (Google Forms does not pre-fill it). Condition changed from requiring explicit "PENDING" to excluding final states (ACCEPTED/REJECTED).

## Version 3.1.0
- Add: LLM as a judge on sheet signups. Triggers on PENDING applicants. Assigns a verdict based on safety of a candidate.
- Remove: Messenger functionality and endpoint, not being used nor a plan to incorprate added unnecessary feature.

## Version 3.0.8

- Add: Cron job schedule settings configurable from the admin Settings tab (sync volunteers, weekly reminders, rotate schedule)
- Remove: `CRON_SEND_CONFIRMATION_EMAILS` setting — confirmation emails are sent automatically during volunteer sync
- Remove: `INVITE_LINK_DISCORD` and `INVITE_LINK_FACEBOOK_MESSENGER` settings — Discord and Facebook Messenger deprecated, Zalo is the sole community platform

## Version 3.0.7

- Fix: Update remaining Zalo references in dashboard, error page, docs, run.sh
- Replace: Discord/Facebook Messenger group chat with Zalo

## Version 3.0.6

- Fix: Unsubscribe form always returning 422 — logging middleware was consuming the request body stream before the route handler could read it; body is now replayed via a cached receive closure
- Fix: Unsubscribe POST except block re-queries volunteer state after rollback so the form re-renders with the correct pre-selected radio button
- Fix: Schedule status date parsing changed from `%m/%d/%Y` to `%m/%d` with current year; add `display_weeks_count` for actual visible sheets
- Remove: Deprecated scripts (`deploy.sh`, `docker.sh`), debug artifacts (`debug_auth.py`, `test_auth.html`, `.cursorrules`)
- Update: Dockerfile uses production-only dependencies (`--only main`) and uppercase `AS` for multi-stage build
- Remove: bot endpoints, as not currently working.

## Version 3.0.5

- Refactor: Split 1,639-line admin.py into feature-based router package (volunteers, emails, sign-ups, schedules, users, health)
- Refactor: Extract Messenger webhook endpoints from public.py into dedicated messenger.py router
- Refactor: Deduplicate timeout_handler, _get_client_ip, and AdminUser into shared utils
- Fix: Settings endpoints now require admin authentication
- Fix: Remove self-referential root redirect in main.py
- Fix: Scrubbed real Google OAuth credentials from env.template
- Remove: Unused WebhookHandler class and dead scheduler/migration dependencies
- Rename: create_tables() → init_db() to accurately reflect function behavior

## Version 3.0.4

- Refactor:  Database Query Optimization
-   Before: N+1 queries (1 + N additional queries)
-   After: Single query with joinedload()
-   Performance: 92.2% faster (4.2s → 0.3s)
- Add: Timeout Protection, Authentication Caching

## Version 3.0.3

- Refactor: simply authentication.

## Version 3.0.2

- Fix: Single authentication dependency handles all token sources (headers, query params, cookies, API keys)
- Refactor: Consolidated two separate authentication dependencies into one unified solution.

## Version 3.0.1

- Fix: Email reading 'Status'

## Version 3.0.0

- Add: basic middleware, logging, rate_limiting 
- Fix: security issue where not all admin endpoints were protected
- Refactor: project and file organization
- Update: email templates

## Version 2.0.5

- Fix: Supabase Auth usage of mock user
- Fix: Weekly reminder emails to accomodate for head teaching assistant.
- Add: cosmetic changes, add icon, favicon, etc.

## Version 2.0.4

- Fix: reminder link in weekly reminder emails 


## Version 2.0.3

- Fix: endpoint for unsubscribe. no more /public prefix.
- Add: version number to home and dashboard page

## Version 2.0.2

- Fix: rotate schedule error.

## Version 2.0.1

- Fix: template import error, caused emails to not be able to to be sent.

## Version 2.0.0

- Add: Authentication: only those with supabase service role key OR admins can make calls to the API. 
- Add: Authentication: only those with permitted access can log in through google auth.

## Version 1.2.0

- Add: Dashboard allows forms sync
- Add: Dashboard configures different many settings
- Add: Schedule Config now adjusts the schedule.
- Update: Adjust emails content 
- Update: Adjust schedule templates for new Head Teaching Assistant
- Fix: 'Schedule Template' now is hidden during rotation of schedules

## Version 1.1.3

- Fix: Bug where when syncing, it took all ACTIVE emails vs just all emails, this caused sync with old database data to add this person again
- Refactor: logs get written to a single log in logs directory
- Add: tests for pytest now work.

## Version 1.1.2

- Refactor: Project recovered from GCR, changed dockerfile
- Fix: bug in email unsubscribe display message

## Version 1.1.1

- Fix: Inefficient sync volunteers (use active field and better queries, reduce operation time ~60 seconds with batch operations)
- Fix: When syncing, new volunteers in database or sheets will receive a confirmation email.
- Add: Retry for operations with Google API to handle SSL retries due to cold container starts.
- Add: admin set active/inactive volunteers.
- Fix: pytest now works for all tests in ./test.
- Remove: remove admin endpoints for production.
- Add: Will use default credential account by default, then GOOGLE_APPLICATION_CREDENTIALS if specified.

## Version 1.1.0

- Add: unsubscribe system.
- Add: admin api guarded by google auth
- Add: scheduler api guarded by OIDC tokens
- Add: DRY_RUN now blocks all email sending except to DRY_RUN_RECIPIENT
- Refactor: Database is source of truth, Google sheets is used as data input only.

## Version 1.0.0

- Database now updates with Google Sheets confirmation status.
- Moved links as environment variables.
- Add log message for failed class build