"""
Configuration settings for the Vietnam Hearts Scheduler application.
All environment variables are loaded here with their default values.
"""

import os
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./scheduler.db")

# API Configuration
PORT = os.getenv("PORT", "8080")
API_URL = os.getenv("API_URL", f"http://localhost:{PORT}")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Email Configuration
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Testing/Dry Run Configuration
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
DRY_RUN_EMAIL_RECIPIENT = os.getenv("DRY_RUN_EMAIL_RECIPIENT")

# Google Sheets Configuration
SCHEDULE_SHEET_ID = os.getenv("SCHEDULE_SHEET_ID")
NEW_SIGNUPS_SHEET_ID = os.getenv(
    "NEW_SIGNUPS_SHEET_ID"
)
GOOGLE_SCHEDULE_RANGE = os.getenv("GOOGLE_SCHEDULE_RANGE", "B7:G11")
GOOGLE_SIGNUPS_RANGE = os.getenv("GOOGLE_SIGNUPS_RANGE", "A2:R")
GOOGLE_APPLICATION_CREDENTIALS = Path(
    os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        PROJECT_ROOT / "secrets" / "google_credentials.json",
    )
)

# Google OAuth Configuration
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
SERVICE_ACCOUNT_EMAIL = os.getenv("SERVICE_ACCOUNT_EMAIL")

SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT = int(
    os.getenv("SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT", "4")
)  # Number of weeks to display at a time

# External Links
SCHEDULE_SIGNUP_LINK = os.getenv("SCHEDULE_SIGNUP_LINK")
EMAIL_PREFERENCES_LINK = os.getenv("EMAIL_PREFERENCES_LINK")

# Retry Configuration for External API Calls
GOOGLE_SHEETS_MAX_RETRIES = int(os.getenv("GOOGLE_SHEETS_MAX_RETRIES", "3"))
GOOGLE_SHEETS_BASE_WAIT = float(os.getenv("GOOGLE_SHEETS_BASE_WAIT", "2.0"))
GOOGLE_SHEETS_MAX_WAIT = float(os.getenv("GOOGLE_SHEETS_MAX_WAIT", "15.0"))

# Social Media and Communication Links
FACEBOOK_MESSENGER_LINK = os.getenv("FACEBOOK_MESSENGER_LINK")
DISCORD_INVITE_LINK = os.getenv("DISCORD_INVITE_LINK")
ONBOARDING_GUIDE_LINK = os.getenv("ONBOARDING_GUIDE_LINK")
INSTAGRAM_LINK = os.getenv("INSTAGRAM_LINK")
FACEBOOK_PAGE_LINK = os.getenv("FACEBOOK_PAGE_LINK")

# Email Templates
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "email" / "weekly-reminder-email.html"

# Required Environment Variables (only in production)
REQUIRED_ENV_VARS = [
    "GMAIL_APP_PASSWORD",  # Required for sending emails
    "SCHEDULE_SIGNUP_LINK",  # Required for email templates
    "EMAIL_PREFERENCES_LINK",  # Required for email templates
    "FACEBOOK_MESSENGER_LINK",  # Required for email templates
    "DISCORD_INVITE_LINK",  # Required for email templates
    "ONBOARDING_GUIDE_LINK",  # Required for email templates
    "INSTAGRAM_LINK",  # Required for email templates
    "FACEBOOK_PAGE_LINK",  # Required for email templates
    "NEW_SIGNUPS_SHEET_ID", # Required for syncing new volunteers
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
