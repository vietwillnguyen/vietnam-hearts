"""
Configuration settings for the Vietnam Hearts Scheduler application.
All environment variables are loaded here with their default values.

Static configuration (environment variables) are defined here.
Dynamic configuration (database settings) are managed via the settings service.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

APPLICATION_VERSION = os.getenv("APPLICATION_VERSION", "3.3.0")

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")

# Cloud Scheduler Configuration
# Used to address the cron jobs whose cadence is owned by the CRON_* settings.
# CLOUD_SCHEDULER_LOCATION must match SCHEDULER_REGION in scripts/deploy.config,
# which is where the jobs are actually created.
# GCP_PROJECT_ID is an override: left empty, the project attached to Application
# Default Credentials is used (see cron_sync_service.resolve_project_id), which
# on Cloud Run is already the right one.
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
CLOUD_SCHEDULER_LOCATION = os.getenv("CLOUD_SCHEDULER_LOCATION", "asia-southeast1")

# API Configuration
PORT = os.getenv("PORT", "8080")
API_URL = os.getenv("API_URL", f"http://localhost:{PORT}")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# How far from the right-hand end of X-Forwarded-For the real client IP sits.
# X-Forwarded-For is append-only and unvalidated, so everything to the left of
# the entries our own infrastructure appended is caller-supplied and must not be
# trusted (see app/utils/request_helpers.get_client_ip).
#
# 1 is correct for this deployment: the service is invoked directly on its
# *.run.app URL (scripts/deploy.config BASE_URL, .github/workflows/deploy.yml),
# where Cloud Run's front end appends the peer address it observed as the last
# entry. Put a Google external Application Load Balancer in front and the header
# becomes "<supplied>,<client-ip>,<load-balancer-ip>" - the client is then second
# from the right, so set this to 2. Google documents that two-address append at
# https://cloud.google.com/load-balancing/docs/https#x-forwarded-for_header
# and explicitly does not verify anything preceding those two entries.
TRUSTED_PROXY_HOPS = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))

# Email Configuration
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Google Sheets Configuration
GOOGLE_APPLICATION_CREDENTIALS = Path(
    os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        PROJECT_ROOT / "secrets" / "google_credentials.json",
    )
)

# Email Templates
EMAIL_TEMPLATES_PATH = PROJECT_ROOT / "templates" / "email"

# Supabase Configuration
# Uses Supabase's new API key format (publishable/secret) rather than the
# legacy anon/service_role JWTs. See docs/SUPABASE_SETUP.md.
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL")
ADMIN_EMAILS = (
    os.getenv("ADMIN_EMAILS", "").split(",") if os.getenv("ADMIN_EMAILS") else []
)


# Google OAuth Configuration for Supabase Auth
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

# Facebook Configuration
FACEBOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN")
FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET")

# Sentry Error Tracking (optional - error tracking disabled if unset)
SENTRY_DSN = os.getenv("SENTRY_DSN")
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

# Required Environment Variables (only in production)
REQUIRED_ENV_VARS = [
    "GMAIL_APP_PASSWORD",  # Required for sending emails
    "GOOGLE_OAUTH_CLIENT_ID",  # Required for Google OAuth
    "GOOGLE_OAUTH_CLIENT_SECRET",  # Required for Google OAuth
    "SERVICE_ACCOUNT_EMAIL",  # Required for Google Sheets access
    "SUPABASE_URL",  # Required for Supabase auth
    "SUPABASE_PUBLISHABLE_KEY",  # Required for Supabase auth
    "SUPABASE_SECRET_KEY",  # Required for Supabase auth
    "EMAIL_SENDER",  # Required for sending emails
]


def validate_config():
    """
    Validate all configuration settings.

    In development: Only validates essential config
    In production: Validates all required environment variables
    """
    is_production = os.getenv("ENVIRONMENT", "development") == "production"

    # Only validate other required vars in production
    if is_production:
        _validate_required_env_vars()


def _validate_required_env_vars():
    """Validate that all required environment variables are set"""
    missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}\n"
            "Please set these variables in your .env file."
        )


# Validate configuration on import
validate_config()
