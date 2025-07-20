# Email Access Control Setup Guide

This guide shows you how to control which Google accounts can access your Vietnam Hearts admin system.

## Quick Setup

### 1. Set Admin Emails in Environment

Add this to your `.env` file:

```env
# Admin Access Control
ADMIN_EMAILS=admin@vietnamhearts.org,coordinator@vietnamhearts.org,volunteer-manager@vietnamhearts.org
```

**Important**: Separate multiple emails with commas, no spaces.

### 2. Test Your Configuration

Visit: `http://localhost:8080/auth/debug`

This will show you:
- Which emails are currently allowed
- Your Supabase configuration status
- Current environment settings

## Access Control Methods

### Method 1: Environment Variable (Recommended)

**Pros**: Easy to manage, version controlled, clear audit trail
**Cons**: Requires restart to change

```env
ADMIN_EMAILS=email1@gmail.com,email2@gmail.com,email3@gmail.com
```

### Method 2: Supabase User Metadata

**Pros**: Can be changed without restart, managed through Supabase dashboard
**Cons**: Requires manual setup for each user

1. Go to your Supabase dashboard
2. Navigate to **Authentication** > **Users**
3. Find the user you want to make admin
4. Edit their metadata and add: `{"is_admin": true}`

### Method 3: Supabase App Metadata

**Pros**: Can be set programmatically
**Cons**: Requires admin access to set

```sql
UPDATE auth.users 
SET app_metadata = jsonb_set(
  COALESCE(app_metadata, '{}'), 
  '{role}', 
  '"admin"'
) 
WHERE email = 'admin@vietnamhearts.org';
```

## Testing Access Control

### Test Allowed Users

1. Add your test email to `ADMIN_EMAILS`:
   ```env
   ADMIN_EMAILS=your-test-email@gmail.com
   ```

2. Restart the application

3. Visit `http://localhost:8080`

4. Click "Sign in with Google"

5. Use your test email - should get access to dashboard

### Test Denied Users

1. Use an email NOT in `ADMIN_EMAILS`

2. Visit `http://localhost:8080`

3. Click "Sign in with Google"

4. Use the non-admin email - should get "Access denied" message

## Example Configurations

### Development Team Access
```env
ADMIN_EMAILS=developer1@gmail.com,developer2@gmail.com,project-manager@gmail.com
```

### Production Team Access
```env
ADMIN_EMAILS=admin@vietnamhearts.org,coordinator@vietnamhearts.org,volunteer-manager@vietnamhearts.org
```

### Single Admin Access
```env
ADMIN_EMAILS=admin@vietnamhearts.org
```

## Security Best Practices

### 1. Use Specific Emails
❌ **Bad**: `ADMIN_EMAILS=*@gmail.com`
✅ **Good**: `ADMIN_EMAILS=specific-admin@gmail.com`

### 2. Regular Review
- Review admin access monthly
- Remove access for team members who leave
- Add access for new team members

### 3. Environment Separation
```env
# Development
ADMIN_EMAILS=dev1@gmail.com,dev2@gmail.com

# Production  
ADMIN_EMAILS=admin@vietnamhearts.org,coordinator@vietnamhearts.org
```

### 4. Backup Admin
Always have at least 2 admin emails in case one account is compromised:
```env
ADMIN_EMAILS=primary-admin@gmail.com,backup-admin@gmail.com
```

## Troubleshooting

### "Access Denied" for Valid Admin

1. Check the debug endpoint: `http://localhost:8080/auth/debug`
2. Verify email spelling in `ADMIN_EMAILS`
3. Check for extra spaces or commas
4. Restart the application after changes

### Can't Access Debug Endpoint

1. Make sure the application is running
2. Check if Supabase environment variables are set
3. Check application logs for errors

### Multiple Admin Methods

The system checks admin status in this order:
1. **User Metadata** (`{"is_admin": true}`)
2. **App Metadata** (`{"role": "admin"}`)
3. **Environment Variable** (`ADMIN_EMAILS`)

If any method returns `true`, the user gets access.

## Logging

The system logs all authentication attempts. Check your logs for:
- `"Checking admin status for user: email@example.com"`
- `"User email@example.com is admin via ADMIN_EMAILS environment variable"`
- `"User email@example.com is NOT an admin"`

## Emergency Access

If you lose access to all admin accounts:

1. Stop the application
2. Add your email to `ADMIN_EMAILS`
3. Restart the application
4. Login and fix the configuration
5. Remove your temporary access

## Support

If you need help:
1. Check the debug endpoint first
2. Review the application logs
3. Verify your environment variables
4. Test with a known working email 