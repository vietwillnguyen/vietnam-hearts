# Vietnam Hearts Scheduler

[![Tests](https://github.com/vietwillnguyen/vietnam-hearts/actions/workflows/test.yml/badge.svg)](https://github.com/vietwillnguyen/vietnam-hearts/actions/workflows/test.yml)

A comprehensive scheduling and communication system for Vietnam Hearts volunteer management, built with FastAPI and integrated with Google Sheets.

## Features

- 📅 **Automated Scheduling**: Sync with Google Sheets for class schedules
- 📧 **Email Notifications**: Automated weekly reminders and communications
- 👥 **Volunteer Management**: Track new signups and preferences
- 🔐 **OAuth Integration**: Secure Google authentication
- 📊 **API Endpoints**: RESTful API for external integrations
- 🧪 **Dry Run Mode**: Test functionality without sending emails
- 🔧 **Configuration Management**: Environment-based configuration

## Quick Start

### 1. Prerequisites

- Python 3.12 or later
- Poetry (Python package manager)
- Google Cloud Console access (for Google Sheets integration)

### 2. Setup

Run the setup script to automatically configure your environment:

```bash
./setup.sh
```

This will:
- Check and install dependencies
- Create necessary directories
- Set up your `.env` file from template
- Validate your configuration

### 3. Configuration

Edit your `.env` file with your actual values:

```bash
# Copy the template
cp env.template .env

# Edit with your values
nano .env
```

### 4. Run the Application

```bash
# Basic run
./run.sh

# With custom environment file
./run.sh -e .env.production

# In dry-run mode (no emails sent)
./run.sh -d

# Check configuration only
./run.sh -c

# Get help
./run.sh --help
```

## Configuration

### Environment Variables

The application uses the following environment variables (see `env.template` for complete list):

#### Required for Production
- `GMAIL_APP_PASSWORD` - Gmail app password for sending emails
- `SCHEDULE_SIGNUP_LINK` - Link for volunteer signups
- `EMAIL_PREFERENCES_LINK` - Link for email preferences
- `NEW_SIGNUPS_RESPONSES_LINK` - Google Sheets ID for new signups
- Social media links (Facebook, Discord, Instagram, etc.)

#### Google Sheets Integration
- `SCHEDULE_SIGNUP_LINK` - Main schedule Google Sheets ID
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to service account JSON
- `SERVICE_ACCOUNT_EMAIL` - Google service account email

#### Facebook Messenger Integration
- `FACEBOOK_VERIFY_TOKEN` - Custom verification token for webhook
- `FACEBOOK_ACCESS_TOKEN` - Page access token for sending messages
- `FACEBOOK_APP_ID` - Facebook app ID
- `FACEBOOK_APP_SECRET` - Facebook app secret

#### Optional
- `PORT` - API server port (default: 8080)
- `ENVIRONMENT` - Environment mode (development/production)
- `DRY_RUN` - Enable dry run mode for testing

### Facebook Messenger Setup

1. **Create a Facebook App**:
   - Go to [Facebook Developers](https://developers.facebook.com/)
   - Create a new app or select existing one
   - Add Messenger product to your app

2. **Configure Webhook**:
   - Set webhook URL to: `https://your-domain.com/webhook/messenger`
   - Set verify token to match your `FACEBOOK_VERIFY_TOKEN`
   - Subscribe to `messages` and `messaging_postbacks` events

3. **Get Page Access Token**:
   - Connect your Facebook page to the app
   - Generate a page access token
   - Set this as `FACEBOOK_ACCESS_TOKEN`

### Google Sheets Setup

1. **Create a Google Cloud Project**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select existing one

2. **Enable APIs**:
   - Enable Google Sheets API
   - Enable Gmail API (for email sending)

3. **Create Service Account**:
   - Go to IAM & Admin > Service Accounts
   - Create a new service account
   - Download the JSON credentials file
   - Place it at `secrets/google_credentials.json`

4. **Share Google Sheets**:
   - Share your schedule and signup sheets with the service account email
   - Grant Editor permissions

### Gmail Setup

1. **Enable 2-Factor Authentication** on your Gmail account
2. **Generate App Password**:
   - Go to Google Account settings
   - Security > 2-Step Verification > App passwords
   - Generate a new app password for "Mail"
3. **Use the app password** in your `.env` file (not your regular password)

## Usage

### Development Mode

```bash
# Start with auto-reload
./run.sh

# Start on custom port
./run.sh -p 9000

# Enable dry run mode
./run.sh -d
```

### Production Mode

```bash
# Run in production with multiple workers
./run.sh -E production -w 4

# Use production environment file
./run.sh -e .env.production -E production
```

### API Endpoints

Once running, the API will be available at:

- **API Documentation**: `http://localhost:8080/docs`
- **Health Check**: `http://localhost:8080/public/health`
- **Admin Endpoints**: `http://localhost:8080/admin/*` (development only)

## Scripts

### `setup.sh`
Initial setup script that:
- Checks dependencies
- Installs Python packages
- Creates necessary directories
- Sets up `.env` file
- Validates configuration

### `run.sh`
Main application runner with options:
- `-e, --env-file FILE` - Use custom environment file
- `-p, --port PORT` - Set port number
- `-E, --environment ENV` - Set environment mode
- `-d, --dry-run` - Enable dry run mode
- `-i, --install` - Install dependencies before running
- `-c, --check` - Check configuration only
- `-h, --help` - Show help

### `env.template`
Template file containing all environment variables with:
- Default values
- Explanatory comments
- Setup instructions
- Required vs optional variables

## Project Structure

```
vietnam-hearts/
├── app/                    # Main application code
│   ├── config.py          # Configuration management
│   ├── main.py            # FastAPI application
│   ├── models.py          # Database models
│   ├── routers/           # API route handlers
│   ├── services/          # Business logic
│   └── utils/             # Utility functions
├── templates/             # Email templates
├── secrets/               # Credentials (not in git)
├── env.template           # Environment template
├── run.sh                 # Application runner
├── setup.sh               # Setup script
└── pyproject.toml         # Poetry configuration
```

## Testing & CI/CD

### Running Tests Locally

```bash
# Run all tests
poetry run pytest tests/ -v

# Run specific test file
poetry run pytest tests/test_form_submissions.py -v

# Run with coverage
poetry run pytest tests/ --cov=app --cov-report=html
```

### Testing Facebook Messenger Webhook

1. **Test Configuration**:
   ```bash
   # Check if your app is running
   curl http://localhost:8080/test-messenger
   ```

2. **Test Webhook Verification** (Facebook will do this automatically):
   ```bash
   # Simulate Facebook's verification request
   curl "http://localhost:8080/webhook/messenger?mode=subscribe&verify_token=YOUR_VERIFY_TOKEN&challenge=1234567890"
   ```

3. **Test Message Handling** (requires Facebook app setup):
   - Set up your Facebook app with the webhook URL
   - Send a message to your Facebook page
   - Check logs for webhook processing

### GitHub Actions

This project includes GitHub Actions for automated testing. The workflow will:

- **Run on every push** to main/master/develop branches
- **Run on pull requests** to main/master/develop branches
- **Test against Python 3.10 and 3.11** for compatibility
- **Use Poetry** for dependency management
- **Cache dependencies** for faster builds
- **Upload test results** as artifacts

#### GitHub Actions Costs

**GitHub Actions is completely FREE for:**

- **Public repositories**: Unlimited minutes
- **Private repositories**: 2,000 minutes/month free
- **Team/Enterprise**: 3,000 minutes/month free

**For this project:**
- Each test run takes ~1-2 minutes
- You get 2,000 free minutes per month
- That's ~1,000 test runs per month for free!
- Only costs money if you exceed the free tier

#### Workflow Details

The workflow (`.github/workflows/test.yml`) includes:

1. **Setup**: Python environment with Poetry
2. **Caching**: Dependencies are cached between runs
3. **Testing**: Runs pytest with proper environment variables
4. **Artifacts**: Saves test results for 7 days

#### Viewing Results

- Go to your GitHub repository
- Click the "Actions" tab
- See all test runs and their results
- Download test artifacts if needed

## Troubleshooting

### Common Issues

1. **"Poetry not found"**:
   ```bash
   curl -sSL https://install.python-poetry.org | python3 -
   ```

2. **"Google credentials not found"**:
   - Ensure `secrets/google_credentials.json` exists
   - Check file permissions
   - Verify service account setup

3. **"Email sending failed"**:
   - Verify Gmail app password is correct
   - Check 2FA is enabled on Gmail account
   - Test with dry run mode first

4. **"Google Sheets access denied"**:
   - Share sheets with service account email
   - Verify API is enabled in Google Cloud Console
   - Check credentials file path in `.env`

### Debug Mode

```bash
# Run with verbose logging
ENVIRONMENT=development ./run.sh

# Check configuration
./run.sh -c
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with `./run.sh -c`
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For support and questions:
- Check the troubleshooting section above
- Review the API documentation at `/docs`
- Open an issue on GitHub 