"""
Settings router for managing dynamic configuration

This router provides API endpoints for admins to view and update
dynamic configuration settings stored in the database.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.schemas import Setting, SettingCreate, SettingUpdate, SettingsList
from app.models import Setting as SettingModel  # Import the SQLAlchemy model with alias
from app.services.settings_service import (
    get_setting,
    set_setting,
    delete_setting,
    get_all_settings,
    get_settings_dict,
)
from app.utils.logging_config import get_api_logger

# Initialize logger
logger = get_api_logger()

# Create router
router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/", response_model=SettingsList)
async def get_settings(db: Session = Depends(get_db)):
    """
    Get all settings
    
    Returns a list of all configuration settings with their values and descriptions.
    """
    try:
        settings = get_all_settings(db)
        return SettingsList(settings=settings, total=len(settings))
    except Exception as e:
        logger.error(f"Failed to get settings: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve settings"
        )


@router.get("/{key}", response_model=Setting)
async def get_setting_by_key(key: str, db: Session = Depends(get_db)):
    """
    Get a specific setting by key
    
    Args:
        key: The setting key to retrieve
        
    Returns:
        The setting object with value and metadata
    """
    try:
        setting = db.query(SettingModel).filter(SettingModel.key == key).first()
        if not setting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting '{key}' not found"
            )
        return setting
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get setting '{key}': {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve setting"
        )


@router.post("/", response_model=Setting)
async def create_setting(setting_data: SettingCreate, db: Session = Depends(get_db)):
    """
    Create a new setting
    
    Args:
        setting_data: The setting data to create
        
    Returns:
        The created setting object
    """
    try:
        # Check if setting already exists
        existing = db.query(SettingModel).filter(SettingModel.key == setting_data.key).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Setting '{setting_data.key}' already exists"
            )
        
        setting = set_setting(
            db=db,
            key=setting_data.key,
            value=setting_data.value,
            description=setting_data.description
        )
        logger.info(f"Created setting '{setting_data.key}'")
        return setting
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create setting '{setting_data.key}': {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create setting"
        )


@router.put("/{key}", response_model=Setting)
async def update_setting(key: str, setting_data: SettingUpdate, db: Session = Depends(get_db)):
    """
    Update an existing setting
    
    Args:
        key: The setting key to update
        setting_data: The new setting data
        
    Returns:
        The updated setting object
    """
    try:
        # Check if setting exists
        existing = db.query(SettingModel).filter(SettingModel.key == key).first()
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting '{key}' not found"
            )
        
        setting = set_setting(
            db=db,
            key=key,
            value=setting_data.value,
            description=setting_data.description
        )
        logger.info(f"Updated setting '{key}'")
        return setting
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update setting '{key}': {str(e)}", exc_info=True)
        # Return the actual error for debugging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update setting: {str(e)}"
        )


@router.delete("/{key}")
async def delete_setting_by_key(key: str, db: Session = Depends(get_db)):
    """
    Delete a setting
    
    Args:
        key: The setting key to delete
        
    Returns:
        Success message
    """
    try:
        success = delete_setting(db, key)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting '{key}' not found"
            )
        
        logger.info(f"Deleted setting '{key}'")
        return {"message": f"Setting '{key}' deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete setting '{key}': {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete setting"
        )


@router.get("/dict/all")
async def get_settings_as_dict(db: Session = Depends(get_db)):
    """
    Get all settings as a simple key-value dictionary
    
    Returns:
        Dictionary mapping setting keys to values
    """
    try:
        return get_settings_dict(db)
    except Exception as e:
        logger.error(f"Failed to get settings dict: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve settings"
        ) 