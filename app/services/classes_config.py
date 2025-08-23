"""
Configuration for volunteer reminder system
Reads class configuration from Google Sheets 'Config' tab
"""

from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.utils.logging_config import get_api_logger
from app.utils.config_helper import ConfigHelper

logger = get_api_logger()

# Fallback configuration in case Google Sheets is unavailable
FALLBACK_CLASS_CONFIG = {
    "Grade 1": {"sheet_range": "B7:G11", "time": "9:30 - 10:30 AM", "room": "Downstairs", "max_assistants": 4, "notes": "Pre-A1 Starters (G1)"},
    "Grade 4": {"sheet_range": "B13:G16", "time": "9:30 - 10:30 AM", "room": "Upstairs", "max_assistants": 4, "notes": "A1 Movers (G4)"},
}

def get_class_config(db: Session) -> Dict[str, Dict[str, Any]]:
    """
    Get class configuration from Google Sheets 'Config' tab
    
    Args:
        db: Database session for configuration
        
    Returns:
        Dict mapping grade names to their configuration
    """
    try:
        # Import here to avoid circular import
        from app.services.google_sheets import sheets_service
        
        # Read from the 'Config' tab, starting at A1
        # Expected columns: Grade, SheetRange, Time, Room, Max Assistants, Notes
        config_data = sheets_service.get_range_from_sheet(db, ConfigHelper.get_schedule_sheet_id(db), "Schedule Config!A2:F")
        
        if not config_data:
            logger.warning("No configuration data found in Google Sheets, using fallback config")
            return FALLBACK_CLASS_CONFIG
        
        class_config = {}
        for row in config_data:
            if len(row) >= 6 and row[0].strip():  # Ensure we have at least 6 columns and grade is not empty
                grade = row[0].strip()
                class_config[grade] = {
                    "sheet_range": row[1].strip() if len(row) > 1 and row[1].strip() else "",
                    "time": row[2].strip() if len(row) > 2 and row[2].strip() else "",
                    "room": row[3].strip() if len(row) > 3 and row[3].strip() else "",
                    "max_assistants": int(row[4]) if len(row) > 4 and row[4].strip().isdigit() else 4,
                    "notes": row[5].strip() if len(row) > 5 and row[5].strip() else ""
                }
        
        if not class_config:
            logger.warning("No valid configuration data found in Google Sheets, using fallback config")
            return FALLBACK_CLASS_CONFIG
        
        logger.info(f"Loaded {len(class_config)} class configurations from Google Sheets")
        return class_config
        
    except Exception as e:
        logger.error(f"Failed to load class configuration from Google Sheets: {str(e)}", exc_info=True)
        logger.info("Using fallback configuration")
        return FALLBACK_CLASS_CONFIG

# Legacy CLASS_CONFIG for backward compatibility (deprecated)
CLASS_CONFIG = FALLBACK_CLASS_CONFIG
