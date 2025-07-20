# Supabase Auth Setup Guide

This guide will help you set up Supabase authentication with Google OAuth for the Vietnam Hearts Scheduler application.

## Prerequisites

1. A Supabase account (free tier available)
2. A Google Cloud project with OAuth 2.0 configured
3. The Vietnam Hearts application code

## Step 1: Set up Supabase

### 1.1 Create a Supabase Project

1. Go to [supabase.com](https://supabase.com) and sign up/login
2. Create a new project
3. Note down your project URL and anon key from the API settings

### 1.2 Configure Google OAuth in Supabase

1. In your Supabase dashboard, go to **Authentication** > **Providers**
2. Enable **Google** provider
3. Configure the Google OAuth settings:
   - **Client ID**: Your Google OAuth client ID
   - **Client Secret**: Your Google OAuth client secret
   - **Redirect URL**: `https://your-project.supabase.co/auth/v1/callback`

### 1.3 Set up Admin Users

You have several options to make users admin:

#### Option A: Using Environment Variables
Add admin emails to your `.env` file:
```
ADMIN_EMAILS=admin@vietnamhearts.org,another-admin@example.com
```

#### Option B: Using Supabase Dashboard
1. Go to **Authentication** > **Users**
2. Find the user you want to make admin
3. Click on the user and edit their metadata
4. Add: `{"is_admin": true}`

#### Option C: Using SQL
Run this SQL in the Supabase SQL editor:
```sql
UPDATE auth.users 
SET user_metadata = jsonb_set(
  COALESCE(user_metadata, '{}'), 
  '{is_admin}', 
  'true'
) 
WHERE email = 'admin@vietnamhearts.org';
```

## Step 2: Configure Google OAuth

### 2.1 Create Google OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project or select existing one
3. Enable the Google+ API
4. Go to **Credentials** > **Create Credentials** > **OAuth 2.0 Client IDs**
5. Configure the OAuth consent screen
6. Create a web application client
7. Add authorized redirect URIs:
   - `https://your-project.supabase.co/auth/v1/callback`
   - `http://localhost:8080/auth/callback` (for development)

### 2.2 Get Service Account for Google Sheets

1. In Google Cloud Console, go to **IAM & Admin** > **Service Accounts**
2. Create a new service account or use existing one
3. Download the JSON key file
4. Note the service account email

## Step 3: Environment Configuration

Create a `.env` file in your project root with these variables:

```env
# Database Configuration
DATABASE_URL=sqlite:///./dev.db

# API Configuration
PORT=8080
API_URL=http://localhost:8080
ENVIRONMENT=development

# Email Configuration
EMAIL_SENDER=your-email@gmail.com
GMAIL_APP_PASSWORD=your-gmail-app-password

# Google Sheets Configuration
GOOGLE_APPLICATION_CREDENTIALS=./secrets/google_credentials.json

# Google OAuth Configuration
GOOGLE_OAUTH_CLIENT_ID=your-google-oauth-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-google-oauth-client-secret
SERVICE_ACCOUNT_EMAIL=your-service-account@your-project.iam.gserviceaccount.com

# Supabase Configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-supabase-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-supabase-service-role-key

# Admin Configuration
ADMIN_EMAILS=admin@vietnamhearts.org
```

## Step 4: Install Dependencies

The Supabase dependency is already included in `pyproject.toml`. Install dependencies:

```bash
poetry install
```

## Step 5: Test the Setup

### 5.1 Start the Application

```bash
poetry run uvicorn app.main:app --reload --port 8080
```

### 5.2 Test Authentication

1. Go to `http://localhost:8080/auth/test`
2. Click "Login with Google"
3. Complete the OAuth flow
4. You should see a success page with your access token

### 5.3 Test API Access

1. Go to `http://localhost:8080/docs`
2. Click "Authorize" at the top
3. Enter your access token with `Bearer ` prefix
4. Test the protected endpoints

### 5.4 Test Admin Dashboard

1. Go to `http://localhost:8080/admin/dashboard`
2. You should be redirected to login if not authenticated
3. After login, you should see the admin dashboard

## Step 6: Production Deployment

### 6.1 Update Environment Variables

For production, update these variables:
```env
ENVIRONMENT=production
API_URL=https://your-domain.com
```

### 6.2 Update OAuth Redirect URLs

In both Supabase and Google Cloud Console, update redirect URLs to use your production domain.

### 6.3 Set up Google Cloud Scheduler

The application still supports Google Cloud Scheduler for automated tasks. Configure your scheduler jobs to use the same endpoints with OIDC authentication.

## Troubleshooting

### Common Issues

1. **"Invalid token" errors**: Make sure your Supabase URL and keys are correct
2. **"Admin access required" errors**: Check that the user has admin privileges
3. **OAuth redirect errors**: Verify redirect URLs in both Supabase and Google Cloud Console
4. **CORS errors**: Make sure your Supabase project allows your domain

### Debug Mode

Enable debug logging by setting:
```env
ENVIRONMENT=development
```

### Check Logs

The application logs authentication events. Check the logs for detailed error messages.

## Security Notes

1. Never commit your `.env` file to version control
2. Use strong, unique passwords for all services
3. Regularly rotate your API keys and tokens
4. Monitor your Supabase dashboard for suspicious activity
5. Use HTTPS in production

## Support

If you encounter issues:

1. Check the application logs
2. Verify all environment variables are set correctly
3. Test the OAuth flow step by step
4. Check Supabase and Google Cloud Console for errors
5. Review the authentication flow in the browser developer tools 