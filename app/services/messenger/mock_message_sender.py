"""
Mock message sender for local testing without Facebook API calls

This allows testing the webhook logic locally without needing valid Facebook credentials.
"""

import logging
from typing import Dict, Any, Optional

# Use the same logging configuration as the main app
from app.utils.logging_config import get_api_logger
logger = get_api_logger()

class MockMessageSender:
    """Mock message sender that logs messages instead of sending to Facebook"""
    
    def __init__(self):
        self.sent_messages = []
        logger.info("MockMessageSender initialized for local testing")
    
    def send_text_message(self, recipient_id: str, text: str) -> bool:
        """
        Mock sending a text message (logs instead of sending)
        
        Args:
            recipient_id: The recipient's Facebook ID
            text: The message text to send
            
        Returns:
            Always True for testing purposes
        """
        message = {
            "recipient_id": recipient_id,
            "text": text,
            "type": "text",
            "timestamp": "2024-01-01T00:00:Z"
        }
        
        self.sent_messages.append(message)
        logger.info(f"[MOCK] Message sent to {recipient_id}: {text}")
        logger.info(f"[MOCK] Total messages sent: {len(self.sent_messages)}")
        
        return True
    
    def send_quick_reply(self, recipient_id: str, text: str, quick_replies: list) -> bool:
        """Mock sending a quick reply message"""
        message = {
            "recipient_id": recipient_id,
            "text": text,
            "quick_replies": quick_replies,
            "type": "quick_reply",
            "timestamp": "2024-01-01T00:00:00Z"
        }
        
        self.sent_messages.append(message)
        logger.info(f"[MOCK] Quick reply sent to {recipient_id}: {text} with {len(quick_replies)} options")
        
        return True
    
    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Mock getting user profile"""
        return {
            "id": user_id,
            "first_name": "Test",
            "last_name": "User",
            "profile_pic": "https://example.com/test.jpg"
        }
    
    def get_page_info(self) -> Optional[Dict[str, Any]]:
        """Mock getting page info"""
        return {
            "id": "mock_page_id",
            "name": "Vietnam Hearts (Test)",
            "access_token": "mock_token"
        }
    
    def get_sent_messages(self) -> list:
        """Get all sent messages for testing verification"""
        return self.sent_messages.copy()
    
    def clear_sent_messages(self):
        """Clear sent messages history"""
        self.sent_messages.clear()
