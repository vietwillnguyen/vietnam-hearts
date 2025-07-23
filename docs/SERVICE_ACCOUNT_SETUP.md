# Service Account Setup Guide

This guide explains how to set up your Google Service Account to access both the public API endpoints and admin endpoints in the Vietnam Hearts application.

## Current Authentication Setup

The application uses Supabase authentication for all admin endpoints:

1. **Admin Endpoints** (`/admin/*`): Supabase authentication required
2. **Auth Endpoints** (`/auth/*`): Handle Supabase authentication
3. **Public Endpoints** (`/public/*`): No authentication required

## Admin API Access

All the scheduler API endpoints are now under the admin router and require authentication:

```bash
# Test admin API endpoints (requires Supabase auth)
python tests/test_api.py health --auth-type=supabase
python tests/test_api.py all --auth-type=supabase
```

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
poetry install
```

### Step 3: Test Admin Access

Test admin endpoints using Supabase authentication:

```bash
# Test individual endpoints
python tests/test_api.py health --auth-type=supabase
python tests/test_api.py send-confirmation-emails --auth-type=supabase
python tests/test_api.py sync-volunteers --auth-type=supabase
python tests/test_api.py send-weekly-reminders --auth-type=supabase
python tests/test_api.py rotate-schedule --auth-type=supabase

# Test all endpoints
python tests/test_api.py all --auth-type=supabase
```

## Testing Your Setup

### Test Admin Endpoints

All scheduler endpoints now require Supabase authentication:

```bash
# Test with Supabase authentication (all endpoints)
python tests/test_api.py health --auth-type=supabase
python tests/test_api.py send-confirmation-emails --auth-type=supabase
python tests/test_api.py sync-volunteers --auth-type=supabase
python tests/test_api.py send-weekly-reminders --auth-type=supabase
python tests/test_api.py rotate-schedule --auth-type=supabase
```

### Test All Endpoints

```bash
# Test all endpoints with appropriate authentication
python tests/test_api.py all --auth-type=supabase
```

## Environment Variables Required

Make sure you have these environment variables set in your `.env` file:

```env
# Supabase Configuration
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# Admin Access Control
ADMIN_EMAILS=auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com

# Google OAuth (for Supabase Auth)
GOOGLE_OAUTH_CLIENT_ID=your-google-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-google-client-secret

# Google Service Account (for Google Sheets)
GOOGLE_APPLICATION_CREDENTIALS=path/to/your/service-account-key.json
```

## Troubleshooting

### "Failed to get Supabase authentication token"

1. Check that `SUPABASE_SERVICE_ROLE_KEY` is set in your `.env` file
2. Verify the service role key is correct
3. Make sure PyJWT is installed: `poetry install`

### "Access denied" for admin endpoints

1. Check that your service account email is in `ADMIN_EMAILS`
2. Verify the email spelling (no extra spaces)
3. Restart the application after changing `ADMIN_EMAILS`

### "Connection error"

1. Make sure the application is running on the expected URL
2. Check the `API_BASE_URL` environment variable
3. Verify the application is accessible

## Security Considerations

### For Production

1. **Use HTTPS**: Always use HTTPS in production
2. **Secure Service Role Key**: Keep the Supabase service role key secure
3. **Limit Admin Access**: Only add necessary emails to `ADMIN_EMAILS`
4. **Regular Review**: Review admin access regularly
5. **Monitor Logs**: Monitor authentication logs for suspicious activity

### For Development

1. **Use Environment Variables**: Don't hardcode credentials
2. **Separate Environments**: Use different credentials for dev/staging/prod
3. **Mock Authentication**: Use mock auth for testing when possible

## Example Usage in CI/CD

For automated testing in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Test API Endpoints
  run: |
    python tests/test_api.py health
    python tests/test_api.py all --auth-type=supabase
  env:
    SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
    SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
    ADMIN_EMAILS: auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com
```

## Next Steps

After setting up your service account:

1. **Test all endpoints** to ensure they work correctly
2. **Set up monitoring** to track API usage
3. **Configure alerts** for failed authentication attempts
4. **Document access patterns** for your team
5. **Plan for scaling** as your application grows 