# Supabase Authentication Setup Guide

This guide will help you set up real Supabase authentication to replace the mock authentication currently in use.

## Current Status

The application is currently using **mock authentication** for testing. This allows you to test the complete authentication flow without configuring Supabase.

## Step 1: Check Current Configuration

First, check your current authentication configuration:

```bash
curl http://localhost:8080/auth/health
```

You should see something like:
```json
{
  "status": "configured_with_fallback",
  "service": "authentication",
  "message": "Authentication service is running (using mock auth)",
  "configuration": {
    "supabase_url": false,
    "supabase_anon_key": false,
    "google_client_id": false,
    "google_client_secret": false
  },
  "all_configured": false
}
```

## Step 2: Set Up Supabase Project

### 2.1 Create Supabase Project

1. Go to [Supabase](https://supabase.com/) and sign up/login
2. Click "New Project"
3. Choose your organization
4. Enter project details:
   - **Name**: `vietnam-hearts-auth`
   - **Database Password**: Choose a strong password
   - **Region**: Choose closest to your users
5. Click "Create new project"
6. Wait for the project to be created (this may take a few minutes)

### 2.2 Get Project Credentials

1. In your Supabase dashboard, go to **Settings** > **API**
2. Copy the following values:
   - **Project URL** (e.g., `https://your-project-id.supabase.co`)
   - **anon public** key
   - **service_role** key (keep this secret!)

## Step 3: Configure Google OAuth

### 3.1 Set Up Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Google+ API:
   - Go to **APIs & Services** > **Library**
   - Search for "Google+ API" and enable it

### 3.2 Configure OAuth Consent Screen

1. Go to **APIs & Services** > **OAuth consent screen**
2. Choose **External** user type
3. Fill in the required information:
   - **App name**: `Vietnam Hearts`
   - **User support email**: Your email
   - **Developer contact information**: Your email
4. Add scopes:
   - `openid`
   - `email`
   - `profile`
5. Add test users (your email addresses)
6. Save and continue

### 3.3 Create OAuth Credentials

1. Go to **APIs & Services** > **Credentials**
2. Click **Create Credentials** > **OAuth 2.0 Client IDs**
3. Choose **Web application**
4. Set authorized redirect URIs:
   - `http://localhost:8080/auth/callback` (for development)
   - `https://your-domain.com/auth/callback` (for production)
5. Note down the **Client ID** and **Client Secret**

## Step 4: Configure Supabase Auth

### 4.1 Enable Google Provider

1. In your Supabase dashboard, go to **Authentication** > **Providers**
2. Find **Google** and click **Edit**
3. Enable Google authentication
4. Enter your Google OAuth credentials:
   - **Client ID**: From Google Cloud Console
   - **Client Secret**: From Google Cloud Console
5. Save the configuration

### 4.2 Configure Redirect URLs

1. In Supabase, go to **Authentication** > **URL Configuration**
2. Add your redirect URLs:
   - `http://localhost:8080/auth/callback` (for development)
   - `https://your-domain.com/auth/callback` (for production)

## Step 5: Update Environment Variables

1. Copy the environment template if you haven't already:
   ```bash
   cp env.template .env
   ```

2. Update your `.env` file with the real values:

   ```env
   # Supabase Configuration
   SUPABASE_URL=https://your-project-id.supabase.co
   SUPABASE_ANON_KEY=your-anon-key-here
   SUPABASE_SERVICE_ROLE_KEY=your-service-role-key-here
   
   # Google OAuth for Supabase Auth
   GOOGLE_CLIENT_ID=your-google-client-id-here
   GOOGLE_CLIENT_SECRET=your-google-client-secret-here
   
   # Admin emails (comma-separated)
   ADMIN_EMAILS=your-email@example.com
   ```

3. Restart your application:
   ```bash
   poetry run uvicorn app.main:app --reload --port 8080
   ```

## Step 6: Test Real Authentication

### 6.1 Check Configuration

```bash
curl http://localhost:8080/auth/health
```

You should now see:
```json
{
  "status": "healthy",
  "service": "authentication",
  "message": "Authentication service is running",
  "configuration": {
    "supabase_url": true,
    "supabase_anon_key": true,
    "google_client_id": true,
    "google_client_secret": true
  },
  "all_configured": true
}
```

### 6.2 Test the Flow

1. Go to `http://localhost:8080/auth/login`
2. Click "Sign in with Google"
3. You should be redirected to Google's consent screen
4. After signing in, you should be redirected back to the callback
5. The callback should now use real Supabase authentication
6. You should be redirected to the dashboard with real user data

## Troubleshooting

### Common Issues

1. **"Invalid redirect URI" error**
   - Check that redirect URIs match exactly in both Google Cloud Console and Supabase
   - Ensure protocol (http/https) matches

2. **"Client ID not found" error**
   - Verify Google OAuth credentials are correct
   - Check that OAuth consent screen is properly configured

3. **"Authentication failed" error**
   - Check Supabase configuration
   - Verify environment variables are set correctly
   - Check application logs for details

4. **"Supabase not configured" warning**
   - Ensure all environment variables are set
   - Restart the application after updating `.env`

### Debug Mode

To enable detailed logging:

```env
LOG_LEVEL=DEBUG
```

### Fallback to Mock Auth

If you encounter issues with real authentication, the system will automatically fall back to mock authentication. You can see this in the logs:

```
Supabase not configured, using mock response
```

## Production Deployment

### 1. Update Redirect URLs

1. In Google Cloud Console, add your production domain
2. In Supabase, update redirect URLs for production

### 2. Environment Variables

1. Set all environment variables in your production environment
2. Ensure `ENVIRONMENT=production`

### 3. Security Considerations

1. Use HTTPS in production
2. Keep service role key secure
3. Regularly rotate OAuth credentials
4. Monitor authentication logs

## Next Steps

After setting up real authentication:

1. **Test with real users**: Try signing in with different Google accounts
2. **Configure admin access**: Add admin email addresses to `ADMIN_EMAILS`
3. **Set up user management**: Implement user roles and permissions
4. **Add security features**: Implement rate limiting, audit logging
5. **Monitor usage**: Set up monitoring for authentication events

## Support

If you encounter issues:

1. Check the application logs for error messages
2. Verify your configuration matches this guide
3. Test with a simple OAuth flow first
4. Consult the [Supabase Auth documentation](https://supabase.com/docs/guides/auth)
5. Check the [Google OAuth documentation](https://developers.google.com/identity/protocols/oauth2) 