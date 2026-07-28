# Service Account Setup Guide

This guide explains how to set up your Google Service Account to access both the public API endpoints and admin endpoints in the Vietnam Hearts application.

## Current Authentication Setup

The application uses Supabase authentication for all admin endpoints:

1. **Admin Endpoints** (`/admin/*`): Supabase authentication required
2. **Auth Endpoints** (`/auth/*`): Handle Supabase authentication
3. **Public Endpoints** (`/`, `/health`, `/unsubscribe`): No authentication required

## Admin API Access

All the scheduler API endpoints are now under the admin router (`/admin/*`) and require authentication.
Non-interactive callers such as Cloud Scheduler authenticate with an `apikey` header carrying `SUPABASE_SECRET_KEY`; see [Exercising an endpoint by hand](../tests/README.md#exercising-an-endpoint-by-hand) for the full request recipe and the endpoint list.

## Admin Access via Supabase

To access admin endpoints, you need to register your service account as an admin in Supabase.

### Step 1: Add Service Account to Admin Emails

Add your service account email to the `ADMIN_EMAILS` environment variable in your `.env` file:

```env
ADMIN_EMAILS=auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com,your-other-admin@example.com
```

### Step 2: Install Dependencies

Install the required dependencies:

```bash
uv sync
```

### Step 3: Verify Admin Access

With the application running, confirm the service account is recognised as an admin:

```bash
curl http://localhost:8080/admin/health \
  -H "apikey: $SUPABASE_SECRET_KEY"
```

A `200` with a health payload means the setup is complete.
A `401` means the key does not match the server's `SUPABASE_SECRET_KEY`; a `403` means the key was accepted but its identity is not in `ADMIN_EMAILS`.

## Environment Variables Required

Make sure you have these environment variables set in your `.env` file:

```env
# Supabase Configuration
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_PUBLISHABLE_KEY=your-publishable-key
SUPABASE_SECRET_KEY=your-secret-key

# Admin Access Control
ADMIN_EMAILS=auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com

# Google OAuth (for Supabase Auth)
GOOGLE_OAUTH_CLIENT_ID=your-google-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-google-client-secret

# Google Service Account (for Google Sheets)
# Optional: if this path doesn't exist, the app falls back to Application
# Default Credentials, self-impersonating the runtime service account
# (e.g. on Cloud Run) to obtain Sheets/Drive/Docs-scoped tokens.
# A stale path (file no longer present) is ignored with a warning, so it is
# safe to leave this set even when no key file ships with the deployment.
GOOGLE_APPLICATION_CREDENTIALS=path/to/your/service-account-key.json
```

## Troubleshooting

### `401 Invalid service role key`

1. Check that `SUPABASE_SECRET_KEY` is set in the environment the *server* runs with
2. Verify the `apikey` header value matches it exactly - a rotated key is the usual cause
3. After rotating the key, re-run `scripts/create-or-update-scheduler-jobs.sh` so the Cloud Scheduler jobs pick up the new value

### `403 Admin access required`

1. Check that your service account email is in `ADMIN_EMAILS`
2. Verify the email spelling (no extra spaces)
3. Restart the application after changing `ADMIN_EMAILS`

### Connection errors

1. Make sure the application is running on the expected URL
2. Verify the application is accessible at that URL (`curl <url>/docs`)

## Security Considerations

### For Production

1. **Use HTTPS**: Always use HTTPS in production
2. **Secure Secret Key**: Keep the Supabase secret key secure
3. **Limit Admin Access**: Only add necessary emails to `ADMIN_EMAILS`
4. **Regular Review**: Review admin access regularly
5. **Monitor Logs**: Monitor authentication logs for suspicious activity

### For Development

1. **Use Environment Variables**: Don't hardcode credentials
2. **Separate Environments**: Use different credentials for dev/staging/prod
3. **Mock Authentication**: Use mock auth for testing when possible

## Example Usage in CI/CD

To smoke-test a deployed service from a pipeline, call an admin endpoint with the same `apikey` header the scheduler jobs use:

```yaml
# Example GitHub Actions step
- name: Smoke-test admin API
  run: |
    curl --fail --silent --show-error \
      "${BASE_URL}/admin/health" \
      -H "apikey: ${SUPABASE_SECRET_KEY}"
  env:
    BASE_URL: ${{ vars.BASE_URL }}
    SUPABASE_SECRET_KEY: ${{ secrets.SUPABASE_SECRET_KEY }}
```

The repository's own test workflow does not do this; it runs the pytest suite, which mocks Supabase entirely and needs no credentials.

## Next Steps

After setting up your service account:

1. **Test all endpoints** to ensure they work correctly
2. **Set up monitoring** to track API usage
3. **Configure alerts** for failed authentication attempts
4. **Document access patterns** for your team
5. **Plan for scaling** as your application grows 