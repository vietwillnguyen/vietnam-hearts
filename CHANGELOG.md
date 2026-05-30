# Changelog

All notable changes to Vietnam Hearts will be documented here.

## [3.0.8] - 2026-05-30

### Added
- Cron job schedule settings in the admin Settings tab — admins can now configure when `sync_volunteers`, `send_weekly_reminders`, and `rotate_schedule` run using standard cron expressions
- Default schedules: sync volunteers every 4 hours (`0 */4 * * *`), weekly reminders every Sunday at 12:00 PM (`0 12 * * 0`), rotate schedule every Friday at 5:00 PM (`0 17 * * 5`)

### Removed
- `CRON_SEND_CONFIRMATION_EMAILS` setting — confirmation emails are sent automatically during volunteer sync and do not need a separate schedule
- `INVITE_LINK_DISCORD` and `INVITE_LINK_FACEBOOK_MESSENGER` settings — Discord and Facebook Messenger are no longer used; Zalo is the sole community platform

## [3.0.7] - 2026-05-27

### Changed
- Replaced Discord and Facebook Messenger group chat references with Zalo across dashboard, error page, docs, and run script

## [3.0.6] - 2026-05-27

### Removed
- Bot endpoints (deprecated)

### Fixed
- Unsubscribe form always returning 422 — logging middleware was consuming the request body stream before the route handler could read it; body is now replayed via a cached receive closure
- Unsubscribe POST except block re-queries volunteer state after rollback so the form re-renders with the correct pre-selected radio button
- Schedule status date parsing changed from `%m/%d/%Y` to `%m/%d` with current year; added `display_weeks_count` for actual visible sheets

### Removed
- Deprecated scripts (`deploy.sh`, `docker.sh`) and debug artifacts
