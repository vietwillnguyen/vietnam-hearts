"""
Configuration helper that combines static environment variables with dynamic database settings

This module provides a unified interface for accessing both static and dynamic configuration
values throughout the application.
"""

from typing import Optional
from sqlalchemy.orm import Session
from app.config import (
    # Static settings (environment variables)
    DATABASE_URL,
    PORT,
    API_URL,
    ENVIRONMENT,
    EMAIL_SENDER,
    GMAIL_APP_PASSWORD,
    GOOGLE_APPLICATION_CREDENTIALS,
    TEMPLATE_PATH,
)
from app.services.settings_service import get_setting
from app.utils.sheet_utils import extract_sheet_id_from_url, format_google_sheets_url


class ConfigHelper:
    """
    Helper class to access both static and dynamic configuration values
    
    Static values come from environment variables and are available immediately.
    Dynamic values come from the database and require a database session.
    """
    
    # Static configuration (environment variables)
    DATABASE_URL = DATABASE_URL
    PORT = PORT
    API_URL = API_URL
    ENVIRONMENT = ENVIRONMENT
    EMAIL_SENDER = EMAIL_SENDER
    GMAIL_APP_PASSWORD = GMAIL_APP_PASSWORD
    GOOGLE_APPLICATION_CREDENTIALS = GOOGLE_APPLICATION_CREDENTIALS
    TEMPLATE_PATH = TEMPLATE_PATH
    
    @staticmethod
    def get_schedule_signup_link(db: Session, default: str = "") -> str:
        """Get the schedule signup link from database settings"""
        return get_setting(db, "SCHEDULE_SHEETS_LINK", default) or default
    
    @staticmethod
    def get_invite_link_facebook_messenger(db: Session, default: str = "") -> str:
        """Get the Facebook Messenger link from database settings"""
        return get_setting(db, "INVITE_LINK_FACEBOOK_MESSENGER", default) or default
    
    @staticmethod
    def get_invite_link_discord(db: Session, default: str = "") -> str:
        """Get the Discord invite link from database settings"""
        return get_setting(db, "INVITE_LINK_DISCORD", default) or default
    
    @staticmethod
    def get_onboarding_guide_link(db: Session, default: str = "") -> str:
        """Get the onboarding guide link from database settings"""
        return get_setting(db, "ONBOARDING_GUIDE_LINK", default) or default
    
    @staticmethod
    def get_instagram_link(db: Session, default: str = "") -> str:
        """Get the Instagram link from database settings"""
        return get_setting(db, "INSTAGRAM_LINK", default) or default
    
    @staticmethod
    def get_facebook_page_link(db: Session, default: str = "") -> str:
        """Get the Facebook page link from database settings"""
        return get_setting(db, "FACEBOOK_PAGE_LINK", default) or default
    
    @staticmethod
    def get_schedule_sheet_id(db: Session, default: str = "") -> str:
        """Get the schedule sheet ID from database settings"""
        value = get_setting(db, "SCHEDULE_SHEETS_LINK", default) or default
        # Extract sheet ID if it's a full URL
        sheet_id = extract_sheet_id_from_url(value)
        return sheet_id if sheet_id else value
    
    @staticmethod
    def get_new_signups_sheet_id(db: Session, default: str = "") -> str:
        """Get the new signups sheet ID from database settings"""
        value = get_setting(db, "NEW_SIGNUPS_RESPONSES_LINK", default) or default
        # Extract sheet ID if it's a full URL
        sheet_id = extract_sheet_id_from_url(value)
        return sheet_id if sheet_id else value
    
    @staticmethod
    def get_schedule_sheets_display_weeks_count(db: Session, default: int = 4) -> int:
        """Get the schedule sheets display weeks count from database settings"""
        value = get_setting(db, "SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT", str(default))
        try:
            return int(value) if value else default
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def get_google_sheets_max_retries(db: Session, default: int = 3) -> int:
        """Get the Google Sheets max retries from database settings"""
        value = get_setting(db, "GOOGLE_SHEETS_MAX_RETRIES", str(default))
        try:
            return int(value) if value else default
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def get_google_sheets_base_wait(db: Session, default: float = 2.0) -> float:
        """Get the Google Sheets base wait from database settings"""
        value = get_setting(db, "GOOGLE_SHEETS_BASE_WAIT", str(default))
        try:
            return float(value) if value else default
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def get_google_sheets_max_wait(db: Session, default: float = 15.0) -> float:
        """Get the Google Sheets max wait from database settings"""
        value = get_setting(db, "GOOGLE_SHEETS_MAX_WAIT", str(default))
        try:
            return float(value) if value else default
        except (ValueError, TypeError):
            return default

    @staticmethod
    def get_dry_run(db: Session, default: bool = False) -> bool:
        """Get the dry run setting from database settings"""
        value = get_setting(db, "DRY_RUN", str(default))
        return value.lower() == "true" if value else default
    
    @staticmethod
    def get_dry_run_email_recipient(db: Session, default: str = "") -> str:
        """Get the dry run email recipient from database settings"""
        return get_setting(db, "DRY_RUN_EMAIL_RECIPIENT", default) or default
    
    @staticmethod
    def get_weekly_reminders_enabled(db: Session, default: bool = True) -> bool:
        """Get the weekly reminders enabled setting from database settings"""
        value = get_setting(db, "WEEKLY_REMINDERS_ENABLED", str(default))
        return value.lower() == "true" if value else default


# Create a global instance for easy access
config = ConfigHelper() 