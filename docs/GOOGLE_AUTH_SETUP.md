# Google OAuth Authentication Setup Guide

This guide will walk you through setting up Google OAuth authentication for the Vietnam Hearts application using Supabase.

## Prerequisites

- A Google Cloud Console account
- A Supabase project
- Access to the Vietnam Hearts application codebase

## Step 1: Set Up Google Cloud Console

### 1.1 Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Google+ API (if not already enabled)

### 1.2 Configure OAuth Consent Screen

1. Go to **APIs & Services** > **OAuth consent screen**
2. Choose **External** user type (unless you have a Google Workspace organization)
3. Fill in the required information:
   - **App name**: Vietnam Hearts
   - **User support email**: Your email address
   - **Developer contact information**: Your email address
4. Add the following scopes:
   - `openid`
   - `email`
   - `profile`
5. Add test users (your email addresses) if in testing mode

### 1.3 Create OAuth 2.0 Credentials

1. Go to **APIs & Services** > **Credentials**
2. Click **Create Credentials** > **OAuth 2.0 Client IDs**
3. Choose **Web application** as the application type
4. Set the following redirect URIs:
   - `http://localhost:8080/auth/callback` (for development)
   - `https://your-domain.com/auth/callback` (for production)
5. Note down the **Client ID** and **Client Secret**

## Step 2: Configure Supabase

### 2.1 Set Up Supabase Project

1. Go to [Supabase](https://supabase.com/) and create a new project
2. Note down your project URL and API keys from **Settings** > **API**

### 2.2 Configure Google OAuth Provider

1. In your Supabase dashboard, go to **Authentication** > **Providers**
2. Find **Google** and click **Edit**
3. Enable Google authentication
4. Enter your Google OAuth credentials:
   - **Client ID**: The Client ID from Google Cloud Console
   - **Client Secret**: The Client Secret from Google Cloud Console
5. Save the configuration

### 2.3 Configure Redirect URLs

1. In Supabase, go to **Authentication** > **URL Configuration**
2. Add your redirect URLs:
   - `http://localhost:8080/auth/callback` (for development)
   - `https://your-domain.com/auth/callback` (for production)

## Step 3: Update Environment Variables

1. Copy the environment template:
   ```bash
   cp env.template .env
   ```

2. Update your `.env` file with the following values:

   ```env
   # Supabase Configuration
   SUPABASE_URL=https://your-project-id.supabase.co
   SUPABASE_ANON_KEY=your-anon-key
   SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
   
   # Google OAuth for Supabase Auth
   GOOGLE_OAUTH_CLIENT_ID=your-google-client-id
   GOOGLE_OAUTH_CLIENT_SECRET=your-google-client-secret
   
   # Admin emails (comma-separated)
   ADMIN_EMAILS=admin1@example.com,admin2@example.com
   ```

## Step 4: Install Dependencies

1. Install the required Python packages:
   ```bash
   poetry install
   ```

2. Make sure the following packages are installed:
   - `supabase`
   - `supabase-py`

## Step 5: Test the Authentication

### 5.1 Start the Application

1. Start the development server:
   ```bash
   poetry run uvicorn app.main:app --reload --port 8080
   ```

2. Navigate to `http://localhost:8080/auth/login`

### 5.2 Test the Sign-In Flow

1. Click "Sign in with Google"
2. You should be redirected to Google's consent screen
3. After signing in, you should be redirected back to the callback page
4. The callback page should display your user information and tokens

### 5.3 Test Protected Routes

1. After signing in, navigate to `http://localhost:8080/admin/dashboard`
2. The dashboard should load with your user information
3. Try accessing the API endpoints to ensure authentication is working

## Step 6: Production Deployment

### 6.1 Update Redirect URLs

1. In Google Cloud Console, add your production domain to the authorized redirect URIs
2. In Supabase, update the redirect URLs to include your production domain

### 6.2 Environment Variables

1. Set all required environment variables in your production environment
2. Ensure `ENVIRONMENT=production` is set

### 6.3 Security Considerations

1. Use HTTPS in production
2. Keep your service role key secure
3. Regularly rotate your OAuth credentials
4. Monitor authentication logs in Supabase

## Troubleshooting

### Common Issues

1. **"Invalid redirect URI" error**
   - Check that your redirect URIs match exactly in both Google Cloud Console and Supabase
   - Ensure the protocol (http/https) matches

2. **"Client ID not found" error**
   - Verify your Google OAuth credentials are correct
   - Check that the OAuth consent screen is properly configured

3. **"Authentication failed" error**
   - Check your Supabase configuration
   - Verify your environment variables are set correctly
   - Check the application logs for more details

4. **Token validation errors**
   - Ensure your Supabase URL and keys are correct
   - Check that the JWT tokens are being properly stored and sent

### Debug Mode

To enable debug logging, set the following environment variable:
```env
LOG_LEVEL=DEBUG
```

## API Endpoints

The authentication system provides the following endpoints:

- `POST /auth/signin/google` - Initiate Google OAuth sign-in
- `GET /auth/callback` - Handle OAuth callback
- `GET /auth/me` - Get current user information
- `POST /auth/signout` - Sign out user
- `POST /auth/refresh` - Refresh session token
- `GET /auth/login` - Login page
- `GET /auth/admin/users` - List all users (admin only)

## Security Best Practices

1. **Token Storage**: Store tokens securely in the frontend (localStorage/sessionStorage)
2. **Token Refresh**: Implement automatic token refresh before expiration
3. **HTTPS**: Always use HTTPS in production
4. **CORS**: Configure CORS properly for your domains
5. **Rate Limiting**: Implement rate limiting on authentication endpoints
6. **Logging**: Log authentication events for security monitoring

## Support

If you encounter issues:

1. Check the application logs for error messages
2. Verify your configuration matches this guide
3. Test with a simple OAuth flow first
4. Consult the [Supabase Auth documentation](https://supabase.com/docs/guides/auth)
5. Check the [Google OAuth documentation](https://developers.google.com/identity/protocols/oauth2)

## Next Steps

After setting up authentication:

1. Implement user role management
2. Add user profile management
3. Set up email verification workflows
4. Implement password reset functionality
5. Add multi-factor authentication if needed
6. Set up user analytics and monitoring 