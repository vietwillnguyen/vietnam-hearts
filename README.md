# Vietnam Hearts Automations

[![Tests](https://github.com/vietwillnguyen/vietnam-hearts/actions/workflows/test.yml/badge.svg)](https://github.com/vietwillnguyen/vietnam-hearts/actions/workflows/test.yml)

Public URL: https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app

A comprehensive scheduling and communication system for Vietnam Hearts volunteer management, built with FastAPI and integrated with Google Sheets.

## Features

- 📅 **Automated Scheduling**: Sync with Google Sheets for class schedules
- 📧 **Email Notifications**: Email Announcement Blast and automated weekly reminders and communications
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
./scripts/setup-dev-env.sh
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
./run.sh -e .env.prod

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
- Community & social links (`INVITE_LINK_ZALO`, `INSTAGRAM_LINK`, `FACEBOOK_PAGE_LINK`)

#### Google Sheets Integration
- `SCHEDULE_SIGNUP_LINK` - Main schedule Google Sheets ID
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to service account JSON. Optional: if the file doesn't exist (e.g. on Cloud Run), the app falls back to Application Default Credentials, self-impersonating the runtime service account to get Sheets/Drive/Docs-scoped tokens
- `SERVICE_ACCOUNT_EMAIL` - Google service account email

#### AI & Knowledge Base
- `GEMINI_API_KEY` - Google API key for Gemini AI integration (free tier: 15 RPM, 1M tokens/day)
- `SUPABASE_URL` - Supabase project URL for knowledge base storage
- `SUPABASE_PUBLISHABLE_KEY` - Publishable key for client-facing Supabase auth (replaces legacy "anon" key)
- `SUPABASE_SECRET_KEY` - Secret key for privileged database operations (replaces legacy "service_role" key)
- `SUPABASE_JWKS_URL` - JWKS endpoint for verifying Supabase-issued user access tokens
- **Note**: Uses hybrid approach - Sentence Transformers for embeddings (free, local) + Gemini for chat responses

#### Optional
- `PORT` - API server port (default: 8080)
- `ENVIRONMENT` - Environment mode (development/production)
- `DRY_RUN` - Enable dry run mode for testing
- `SENTRY_DSN` - Sentry DSN for error tracking. If unset, Sentry is disabled entirely
- `SENTRY_TRACES_SAMPLE_RATE` - Fraction of requests to sample for Sentry performance tracing (default: 0.1)

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
   - On Cloud Run, you can skip the key file entirely: grant the Cloud Run
     service's runtime service account the `roles/iam.serviceAccountTokenCreator`
     role on itself, and the app will self-impersonate to mint Sheets/Drive/Docs-scoped
     tokens from Application Default Credentials (see `app/utils/google_credentials.py`)

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
./run.sh -e .env.prod -E production
```

### API Endpoints

Once running, the API will be available at:

- **API Documentation**: `http://localhost:8080/docs`
- **Health Check**: `http://localhost:8080/public/health`
- **Admin Endpoints**: `http://localhost:8080/admin/*` (development only)

## Deploy Configuration

All non-secret deployment settings live in `deploy.config` at the project root.
This file is committed to version control — it contains no secrets.

```bash
# deploy.config controls:
GCP_PROJECT_ID        # GCP project for container registry
GCR_HOSTNAME          # Container registry hostname (gcr.io)
GCR_REGION            # GCR region (asia-southeast1)
IMAGE_NAME            # Docker image name
IMAGE_VERSION         # Current image version (bump on release)
CLOUD_RUN_SERVICE     # Cloud Run service identifier
CLOUD_RUN_REGION      # Cloud Run region (northamerica-northeast1)
BASE_URL              # Public service URL
SCHEDULER_REGION      # Cloud Scheduler region
SCHEDULER_TIMEZONE    # Cron job timezone
```

To change the deployment target (e.g. bump version or change region), edit `deploy.config`.
Both `docker.sh` and `scripts/create-or-update-scheduler-jobs.sh` source this file automatically.

## Scripts

### `scripts/setup-dev-env.sh`
Initial setup script that:
- Checks dependencies
- Installs Python packages
- Creates necessary directories
- Sets up `.env` file
- Validates configuration

### `docker.sh`
Docker management CLI for build/push/pull/run:
```bash
./scripts/docker.sh build [TAG]    # Build image (default: IMAGE_VERSION from deploy.config)
./scripts/docker.sh push [TAG]     # Push to GCR
./scripts/docker.sh deploy [TAG]   # Build + push (production workflow)
./scripts/docker.sh run [-d] [-p PORT]  # Run container locally
./scripts/docker.sh stop           # Stop running container
./scripts/docker.sh logs           # Tail container logs
./scripts/docker.sh clean          # Remove containers and images
./scripts/docker.sh help           # Show all options
```

### `scripts/create-or-update-scheduler-jobs.sh`
Sets up Cloud Scheduler cron jobs (sync-volunteers, send-weekly-reminders, rotate-schedule).
Reads scheduler region and timezone from `deploy.config`.

### `run.sh`
Local application runner (no Docker):
- `-e, --env-file FILE` - Use custom environment file
- `-p, --port PORT` - Set port number
- `-E, --environment ENV` - Set environment mode
- `-d, --dry-run` - Enable dry run mode
- `-i, --install` - Install dependencies before running
- `-c, --check` - Check configuration only
- `-h, --help` - Show help

### `env.template`
Template for secret environment variables. Copy to `.env` and fill in values.
Never commit `.env` to version control.

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
├── docs/                  # Extended documentation
├── scripts/               # Deployment and setup scripts
│   ├── create-or-update-scheduler-jobs.sh          # Cloud Scheduler job setup
│   └── setup-dev-env.sh   # Developer environment setup
├── templates/             # Email and HTML templates
├── secrets/               # Credentials (not in git)
├── deploy.config          # Non-secret deployment settings (GCP, Docker)
├── env.template           # Secret environment variable template
├── docker.sh              # Docker build/push/run management
├── run.sh                 # Local application runner
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
3. **Linting**: Runs `ruff check` and `ruff format --check`
4. **Testing**: Runs pytest with proper environment variables
5. **Artifacts**: Saves test results for 7 days

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
   - Ensure `secrets/google_credentials.json` exists, or that Application Default
     Credentials are available (e.g. Cloud Run's attached service account)
   - Check file permissions
   - Verify service account setup
   - On Cloud Run with no key file, confirm the runtime service account has
     `roles/iam.serviceAccountTokenCreator` on itself (required for self-impersonation)

3. **"Email sending failed"**:
   - Verify Gmail app password is correct
   - Check 2FA is enabled on Gmail account
   - Test with dry run mode first

4. **"Google Sheets access denied"**:
   - Share sheets with service account email
   - Verify API is enabled in Google Cloud Console
   - Check credentials file path in `.env`, or Application Default Credentials
     setup if no credentials file is used

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
3. Install pre-commit hooks (once, after `poetry install`): `poetry run pre-commit install`
4. Make your changes
5. Test with `./run.sh -c`
6. Submit a pull request

### Linting and formatting

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.
Pre-commit hooks run it automatically on `git commit`; to run it manually:

```bash
poetry run ruff check .          # lint
poetry run ruff check . --fix    # lint and auto-fix
poetry run ruff format .         # format
```

CI (`test.yml`) enforces both `ruff check` and `ruff format --check` on every PR.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For support and questions:
- Check the troubleshooting section above
- Review the API documentation at `/docs`
- Open an issue on GitHub 