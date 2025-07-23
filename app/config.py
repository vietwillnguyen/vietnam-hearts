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

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")

# API Configuration
PORT = os.getenv("PORT", "8080")
API_URL = os.getenv("API_URL", f"http://localhost:{PORT}")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

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
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "email" / "weekly-reminder-email.html"

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ADMIN_EMAILS = os.getenv("ADMIN_EMAILS", "").split(",") if os.getenv("ADMIN_EMAILS") else []

# Google OAuth Configuration for Supabase Auth
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

# Required Environment Variables (only in production)
REQUIRED_ENV_VARS = [
    "GMAIL_APP_PASSWORD",  # Required for sending emails
    "GOOGLE_OAUTH_CLIENT_ID",  # Required for Google OAuth
    "GOOGLE_OAUTH_CLIENT_SECRET",  # Required for Google OAuth
    "SERVICE_ACCOUNT_EMAIL",  # Required for Google Sheets access
    "SUPABASE_URL",  # Required for Supabase auth
    "SUPABASE_ANON_KEY",  # Required for Supabase auth
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
