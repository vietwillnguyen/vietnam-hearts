"""
Settings service for managing dynamic configuration values

This service provides functions to get and set configuration settings
that are stored in the database rather than environment variables.
"""

from sqlalchemy.orm import Session
from typing import Optional, Dict, List
from app.models import Setting
from datetime import datetime, timezone
from app.utils.sheet_utils import validate_google_sheets_url


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get a setting value from the database
    
    Args:
        db: Database session
        key: Setting key to retrieve
        default: Default value if setting doesn't exist
        
    Returns:
        The setting value or default if not found
    """
    setting = db.query(Setting).filter(Setting.key == key).first()
    return setting.value if setting else default


def set_setting(db: Session, key: str, value: str, description: Optional[str] = None) -> Setting:
    """
    Set a setting value in the database
    
    Args:
        db: Database session
        key: Setting key to set
        value: Value to set
        description: Optional description of the setting
        
    Returns:
        The Setting object that was created or updated
    """
    setting = db.query(Setting).filter(Setting.key == key).first()
    
    if setting:
        setting.value = value
        setting.updated_at = datetime.now(timezone.utc)
        if description:
            setting.description = description
    else:
        setting = Setting(
            key=key, 
            value=value, 
            description=description
        )
        db.add(setting)
    
    db.commit()
    db.refresh(setting)
    return setting


def delete_setting(db: Session, key: str) -> bool:
    """
    Delete a setting from the database
    
    Args:
        db: Database session
        key: Setting key to delete
        
    Returns:
        True if setting was deleted, False if it didn't exist
    """
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting:
        db.delete(setting)
        db.commit()
        return True
    return False


def get_all_settings(db: Session) -> List[Setting]:
    """
    Get all settings from the database
    
    Args:
        db: Database session
        
    Returns:
        List of all Setting objects
    """
    return db.query(Setting).all()


def get_settings_dict(db: Session) -> Dict[str, str]:
    """
    Get all settings as a dictionary
    
    Args:
        db: Database session
        
    Returns:
        Dictionary mapping setting keys to values
    """
    settings = db.query(Setting).all()
    return {setting.key: setting.value for setting in settings}


def initialize_default_settings(db: Session) -> None:
    """
    Initialize default settings in the database if they don't exist
    
    This should be called during application startup to ensure
    all required settings have default values.
    """
    default_settings = {
        "DRY_RUN": {
            "value": "false",
            "description": "If true, the system will only send emails to the dry run email recipient"
        },
        "DRY_RUN_EMAIL_RECIPIENT": {
            "value": "",
            "description": "Email address to send dry run emails to"
        },
        "WEEKLY_REMINDERS_ENABLED": {
            "value": "true",
            "description": "If false, weekly reminder emails will be disabled globally"
        },
        "INVITE_LINK_FACEBOOK_MESSENGER": {
            "value": "",
            "description": "Link to Facebook Messenger group or chat"
        },
        "INVITE_LINK_DISCORD": {
            "value": "",
            "description": "Discord invite link for the community"
        },
        "ONBOARDING_GUIDE_LINK": {
            "value": "",
            "description": "Link to the onboarding guide for new volunteers"
        },
        "INSTAGRAM_LINK": {
            "value": "",
            "description": "Link to Instagram profile"
        },
        "FACEBOOK_PAGE_LINK": {
            "value": "",
            "description": "Link to Facebook page"
        },
        "SCHEDULE_SHEETS_LINK": {
            "value": "",
            "description": "Google Sheets URL for the schedule spreadsheet. You can paste the full URL (e.g. https://docs.google.com/spreadsheets/d/1234567890/edit) or just the sheet ID (1234567890)"
        },
        "NEW_SIGNUPS_RESPONSES_LINK": {
            "value": "",
            "description": "Google Sheets URL for new volunteer signups. You can paste the full URL (e.g. https://docs.google.com/spreadsheets/d/1234567890/edit) or just the sheet ID (1234567890)"
        },
        "SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT": {
            "value": "4",
            "description": "The default number of weeks to display in the schedule sheets"
        }
    }
    
    for key, config in default_settings.items():
        existing = db.query(Setting).filter(Setting.key == key).first()
        if not existing:
            setting = Setting(
                key=key,
                value=config["value"],
                description=config["description"]
            )
            db.add(setting)
    
    db.commit() 