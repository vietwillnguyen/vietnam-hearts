"""
Message sender for Facebook Messenger
"""

import requests
import logging
from typing import Dict, Any, Optional
from app.config import FACEBOOK_ACCESS_TOKEN

logger = logging.getLogger(__name__)

class MessageSender:
    """Sends messages to Facebook Messenger users"""
    
    def __init__(self):
        self.page_access_token = FACEBOOK_ACCESS_TOKEN
        self.api_url = "https://graph.facebook.com/v18.0/me/messages"
    
    def send_text_message(self, recipient_id: str, text: str) -> bool:
        """
        Send a text message to a user
        
        Args:
            recipient_id: The recipient's Facebook ID
            text: The message text to send
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self.page_access_token:
            logger.error("No Facebook access token configured")
            return False
            
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text}
        }
        
        return self._send_message(payload)
    
    def send_quick_reply(self, recipient_id: str, text: str, quick_replies: list) -> bool:
        """
        Send a message with quick reply buttons
        
        Args:
            recipient_id: The recipient's Facebook ID
            text: The message text
            quick_replies: List of quick reply options
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        payload = {
            "recipient": {"id": recipient_id},
            "message": {
                "text": text,
                "quick_replies": quick_replies
            }
        }
        
        return self._send_message(payload)
    
    def send_button_template(self, recipient_id: str, text: str, buttons: list) -> bool:
        """
        Send a button template message
        
        Args:
            recipient_id: The recipient's Facebook ID
            text: The message text
            buttons: List of button options
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        payload = {
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "button",
                        "text": text,
                        "buttons": buttons
                    }
                }
            }
        }
        
        return self._send_message(payload)
    
    def send_generic_template(self, recipient_id: str, elements: list) -> bool:
        """
        Send a generic template message (for cards, etc.)
        
        Args:
            recipient_id: The recipient's Facebook ID
            elements: List of template elements
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        payload = {
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "generic",
                        "elements": elements
                    }
                }
            }
        }
        
        return self._send_message(payload)
    
    def _send_message(self, payload: Dict[str, Any]) -> bool:
        """
        Send a message to Facebook Messenger API
        
        Args:
            payload: The message payload
            
        Returns:
            True if successful, False otherwise
        """
        try:
            params = {"access_token": self.page_access_token}
            response = requests.post(
                self.api_url,
                json=payload,
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if "message_id" in result:
                    logger.info(f"Message sent successfully: {result['message_id']}")
                    return True
                else:
                    logger.warning(f"Unexpected response format: {result}")
                    return False
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error sending message: {e}")
            return False
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False
    
    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get user profile information
        
        Args:
            user_id: The user's Facebook ID
            
        Returns:
            User profile data or None if failed
        """
        try:
            url = f"https://graph.facebook.com/v18.0/{user_id}"
            params = {
                "access_token": self.page_access_token,
                "fields": "first_name,last_name,profile_pic"
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get user profile: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting user profile: {e}")
            return None
    
    def get_page_info(self) -> Optional[Dict[str, Any]]:
        """
        Get Facebook Page information using the page access token
        
        Returns:
            Page information or None if failed
        """
        try:
            url = "https://graph.facebook.com/v18.0/me"
            params = {
                "access_token": self.page_access_token,
                "fields": "id,name,access_token"
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                page_info = response.json()
                logger.info(f"Retrieved page info: {page_info.get('name', 'Unknown')} (ID: {page_info.get('id', 'Unknown')})")
                return page_info
            else:
                logger.error(f"Failed to get page info: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting page info: {e}")
            return None 