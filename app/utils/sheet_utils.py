"""
Google Sheets URL utilities

This module provides functions to parse Google Sheets URLs and extract sheet IDs.
"""

import re
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs


def extract_sheet_id_from_url(url: str) -> Optional[str]:
    """
    Extract the sheet ID from a Google Sheets URL
    
    Args:
        url: Google Sheets URL (e.g., https://docs.google.com/spreadsheets/d/1234567890/edit#gid=0)
        
    Returns:
        The sheet ID if found, None otherwise
        
    Examples:
        >>> extract_sheet_id_from_url("https://docs.google.com/spreadsheets/d/1234567890/edit#gid=0")
        "1234567890"
        >>> extract_sheet_id_from_url("https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit")
        "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
    """
    if not url or not isinstance(url, str):
        return None
    
    # Remove any whitespace
    url = url.strip()
    
    # Pattern to match Google Sheets URLs
    # Matches: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit
    pattern = r'https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)'
    
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    
    return None


def validate_google_sheets_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate a Google Sheets URL and return the sheet ID if valid
    
    Args:
        url: Google Sheets URL to validate
        
    Returns:
        Tuple of (is_valid, sheet_id)
        
    Examples:
        >>> validate_google_sheets_url("https://docs.google.com/spreadsheets/d/1234567890/edit#gid=0")
        (True, "1234567890")
        >>> validate_google_sheets_url("https://example.com")
        (False, None)
    """
    if not url or not isinstance(url, str):
        return False, None
    
    sheet_id = extract_sheet_id_from_url(url)
    if sheet_id:
        return True, sheet_id
    
    return False, None


def format_google_sheets_url(sheet_id: str) -> str:
    """
    Format a sheet ID into a full Google Sheets URL
    
    Args:
        sheet_id: The Google Sheets ID
        
    Returns:
        The full Google Sheets URL
        
    Examples:
        >>> format_google_sheets_url("1234567890")
        "https://docs.google.com/spreadsheets/d/1234567890/edit"
    """
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def get_sheet_id_or_url(value: str) -> Tuple[str, bool]:
    """
    Determine if a value is a sheet ID or a full URL and return the appropriate format
    
    Args:
        value: Either a sheet ID or a full Google Sheets URL
        
    Returns:
        Tuple of (sheet_id, is_url)
        
    Examples:
        >>> get_sheet_id_or_url("1234567890")
        ("1234567890", False)
        >>> get_sheet_id_or_url("https://docs.google.com/spreadsheets/d/1234567890/edit")
        ("1234567890", True)
    """
    if not value or not isinstance(value, str):
        return "", False
    
    value = value.strip()
    
    # Check if it's already a valid sheet ID (alphanumeric with hyphens/underscores)
    if re.match(r'^[a-zA-Z0-9-_]+$', value):
        return value, False
    
    # Try to extract from URL
    sheet_id = extract_sheet_id_from_url(value)
    if sheet_id:
        return sheet_id, True
    
    # If neither, return as-is (will be treated as invalid)
    return value, False 