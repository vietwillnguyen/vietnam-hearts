# Scheduler API Tests

This directory contains test scripts for the Vietnam Hearts Scheduler API endpoints.

## Files

- `test_scheduler_api.py` - Main test script for scheduler API endpoints
- `test_db.py` - Database connection test script
- `requirements.txt` - Python dependencies for tests

## Prerequisites

### 1. Google Cloud CLI Setup

Make sure you have the Google Cloud CLI installed and configured:

```bash
# Install gcloud CLI (if not already installed)
# Follow instructions at: https://cloud.google.com/sdk/docs/install

# Authenticate with Google Cloud
gcloud auth login

# Set the correct project
gcloud config set project refined-vector-457419

# Verify your configuration
gcloud config list
```

### 2. Python Dependencies

Install the required Python packages:

```bash
# From the tests directory
pip install -r requirements.txt

# Or from the project root
pip install -r tests/requirements.txt
```

### 3. Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# API Configuration
API_BASE_URL=http://localhost:8080

# Google OAuth Configuration (REQUIRED for scheduler tests)
GOOGLE_OAUTH_CLIENT_ID=your-oauth-client-id-here

# Database Configuration (if needed)
DATABASE_URL=your_database_url_here

# Other environment variables as needed
```

**Important**: The `GOOGLE_OAUTH_CLIENT_ID` is required for the scheduler API tests to work. This should be the same value that your API uses for OIDC token validation.

## Usage

### Test Individual Endpoints

```bash
# Test health check endpoint
python tests/test_scheduler_api.py health

# Test send confirmation emails
python tests/test_scheduler_api.py send-confirmation-emails

# Test sync volunteers
python tests/test_scheduler_api.py sync-volunteers

# Test send weekly reminders
python tests/test_scheduler_api.py send-weekly-reminders

# Test rotate schedule
python tests/test_scheduler_api.py rotate-schedule
```

### Test All Endpoints

```bash
# Test all scheduler endpoints
python tests/test_scheduler_api.py all
```

### Database Connection Test

```bash
# Test database connectivity
python tests/test_db.py
```

## Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/scheduler/health` | GET | Health check and Google Sheets connectivity test |
| `/api/scheduler/send-confirmation-emails` | POST | Process and send confirmation emails to new volunteers |
| `/api/scheduler/sync-volunteers` | POST | Sync volunteers from Google Sheets signup form |
| `/api/scheduler/send-weekly-reminders` | POST | Send weekly reminder emails to subscribed volunteers |
| `/api/scheduler/rotate-schedule` | POST | Rotate schedule sheets to show next week |

## Authentication

The test script uses Google Cloud authentication with the service account:
`auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com`

The script automatically:
1. Uses `gcloud auth print-identity-token` to get an OIDC token
2. Includes the token in the `Authorization: Bearer <token>` header
3. Validates the token against your API's OAuth client ID

## Troubleshooting

### Authentication Issues

If you encounter authentication errors:

1. **Check gcloud configuration:**
   ```bash
   gcloud config list
   gcloud auth list
   ```

2. **Re-authenticate if needed:**
   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```

3. **Verify project setting:**
   ```bash
   gcloud config set project refined-vector-457419
   ```

### Connection Issues

If you can't connect to the API:

1. **Check if the API server is running:**
   ```bash
   curl http://localhost:8080/api/scheduler/health
   ```

2. **Verify the API_BASE_URL in your .env file**

3. **Check firewall/network settings**

### Permission Issues

If you get permission errors:

1. **Verify service account permissions:**
   - The service account should have the necessary IAM roles
   - Check if the service account can access the required resources

2. **Check API configuration:**
   - Verify the `GOOGLE_OAUTH_CLIENT_ID` matches your API configuration
   - Ensure the service account email is correctly configured

## Example Output

```
🔧 Scheduler API Tester
Base URL: http://localhost:8080
Service Account: auto-scheduler@refined-vector-457419-n6.iam.gserviceaccount.com
API Prefix: /api/scheduler
============================================================

🔑 Getting authentication token...
✅ Authentication token obtained successfully

🏥 Testing Health Check Endpoint
----------------------------------------

🌐 Making GET request to: http://localhost:8080/api/scheduler/health
📊 Response Status: 200
📄 Response Data: {
  "status": "healthy",
  "google_sheets_connectivity": "ok",
  "submissions_count": 5
}
✅ Health check passed!
   Google Sheets connectivity: ok
   Submissions count: 5
```

## Notes

- The test script includes delays between requests to avoid overwhelming the server
- All responses are logged with detailed information for debugging
- The script handles both successful and error responses gracefully
- Make sure your API server is running before executing tests 