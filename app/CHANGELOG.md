CHANGELOG:

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
- Update: Adjust schedulte templates for new Head Teaching Assistant
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