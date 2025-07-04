"""
Facebook Messenger integration for the Vietnam Hearts Agent.
This module provides utilities for sending responses back to Facebook Messenger users.
"""

import requests
import logging
from typing import Dict, List, Optional
from .models import MessageResponse

logger = logging.getLogger(__name__)


class FacebookMessengerClient:
    """Client for sending messages to Facebook Messenger users"""
    
    def __init__(self, page_access_token: str):
        """
        Initialize the Facebook Messenger client
        
        Args:
            page_access_token: Facebook Page Access Token
        """
        self.page_access_token = page_access_token
        self.api_url = "https://graph.facebook.com/v18.0/me/messages"
    
    def send_text_message(self, recipient_id: str, message_text: str) -> bool:
        """
        Send a text message to a user
        
        Args:
            recipient_id: Facebook user ID
            message_text: Message text to send
            
        Returns:
            True if successful, False otherwise
        """
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": message_text}
        }
        
        return self._send_message(payload)
    
    def send_quick_replies(self, recipient_id: str, message_text: str, quick_replies: List[Dict]) -> bool:
        """
        Send a message with quick reply buttons
        
        Args:
            recipient_id: Facebook user ID
            message_text: Message text to send
            quick_replies: List of quick reply options
            
        Returns:
            True if successful, False otherwise
        """
        # Convert our quick reply format to Facebook's format
        fb_quick_replies = []
        for qr in quick_replies:
            fb_quick_replies.append({
                "content_type": "text",
                "title": qr["text"],
                "payload": qr["payload"]
            })
        
        payload = {
            "recipient": {"id": recipient_id},
            "message": {
                "text": message_text,
                "quick_replies": fb_quick_replies
            }
        }
        
        return self._send_message(payload)
    
    def send_response(self, recipient_id: str, response: MessageResponse) -> bool:
        """
        Send an agent response to a user
        
        Args:
            recipient_id: Facebook user ID
            response: Agent response object
            
        Returns:
            True if successful, False otherwise
        """
        if response.quick_replies:
            return self.send_quick_replies(
                recipient_id, 
                response.response_text, 
                response.quick_replies
            )
        else:
            return self.send_text_message(recipient_id, response.response_text)
    
    def _send_message(self, payload: Dict) -> bool:
        """
        Send a message to Facebook Messenger API
        
        Args:
            payload: Message payload
            
        Returns:
            True if successful, False otherwise
        """
        try:
            response = requests.post(
                self.api_url,
                json=payload,
                params={"access_token": self.page_access_token},
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"Message sent successfully to {payload['recipient']['id']}")
                return True
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False


def handle_webhook_verification(mode: str, token: str, challenge: str, verify_token: str) -> Optional[str]:
    """
    Handle Facebook webhook verification
    
    Args:
        mode: Verification mode
        token: Verification token from Facebook
        challenge: Challenge string from Facebook
        verify_token: Your app's verify token
        
    Returns:
        Challenge string if verification successful, None otherwise
    """
    if mode == "subscribe" and token == verify_token:
        logger.info("Webhook verified successfully")
        return challenge
    else:
        logger.error("Webhook verification failed")
        return None


def extract_messaging_events(webhook_data: Dict) -> List[Dict]:
    """
    Extract messaging events from webhook data
    
    Args:
        webhook_data: Raw webhook data from Facebook
        
    Returns:
        List of messaging events
    """
    messaging_events = []
    
    if webhook_data.get("object") == "page":
        for entry in webhook_data.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                messaging_events.append(messaging_event)
    
    return messaging_events


def process_messaging_event(event: Dict, agent, messenger_client: FacebookMessengerClient) -> None:
    """
    Process a single messaging event and send response
    
    Args:
        event: Facebook messaging event
        agent: Vietnam Hearts Agent instance
        messenger_client: Facebook Messenger client
    """
    try:
        sender_id = event.get("sender", {}).get("id")
        
        if not sender_id:
            logger.error("No sender ID in messaging event")
            return
        
        # Handle text messages
        if "message" in event and "text" in event["message"]:
            message_text = event["message"]["text"]
            
            # Process through agent
            from .models import MessageRequest
            request = MessageRequest(
                user_id=sender_id,
                platform="messenger",
                message_text=message_text
            )
            
            response = agent.process_message(request)
            
            # Send response back to user
            messenger_client.send_response(sender_id, response)
        
        # Handle postback events (quick replies)
        elif "postback" in event:
            payload = event["postback"].get("payload", "")
            
            if payload:
                response = agent.process_quick_reply(sender_id, "messenger", payload)
                messenger_client.send_response(sender_id, response)
                
    except Exception as e:
        logger.error(f"Error processing messaging event: {e}")


# Example usage in your webhook handler
def example_webhook_handler(webhook_data: Dict, agent, page_access_token: str):
    """
    Example webhook handler for Facebook Messenger
    
    Args:
        webhook_data: Raw webhook data from Facebook
        agent: Vietnam Hearts Agent instance
        page_access_token: Facebook Page Access Token
    """
    # Initialize messenger client
    messenger_client = FacebookMessengerClient(page_access_token)
    
    # Extract messaging events
    messaging_events = extract_messaging_events(webhook_data)
    
    # Process each event
    for event in messaging_events:
        process_messaging_event(event, agent, messenger_client) 