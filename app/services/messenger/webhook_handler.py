"""
Webhook handler for Facebook Messenger events
"""

import json
import logging
from typing import Dict, Any, Optional
from app.config import FACEBOOK_VERIFY_TOKEN

logger = logging.getLogger(__name__)

class WebhookHandler:
    """Handles incoming webhook events from Facebook Messenger"""
    
    def __init__(self):
        self.verify_token = FACEBOOK_VERIFY_TOKEN
        if not self.verify_token:
            logger.warning("FACEBOOK_VERIFY_TOKEN not set in environment variables")
    
    def verify_webhook(self, mode: str, token: str, challenge: str) -> Optional[str]:
        """
        Verify webhook for Facebook Messenger
        
        Args:
            mode: The mode parameter from Facebook
            token: The verify token from Facebook
            challenge: The challenge string from Facebook
            
        Returns:
            The challenge string if verification is successful, None otherwise
        """
        logger.info(f"Verifying webhook: mode={mode}, token={token[:10] if token else 'None'}..., challenge={challenge[:10] if challenge else 'None'}...")
        
        if not self.verify_token:
            logger.error("FACEBOOK_VERIFY_TOKEN not configured")
            return None
            
        if mode == "subscribe" and token == self.verify_token:
            logger.info("Webhook verified successfully")
            return challenge
        else:
            logger.warning(f"Webhook verification failed: mode={mode}, token_match={token == self.verify_token}")
            return None
    
    def process_webhook(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process incoming webhook events
        
        Args:
            body: The webhook payload from Facebook
            
        Returns:
            Dictionary with processing results
        """
        try:
            # Extract the messaging events
            if "object" in body and body["object"] == "page":
                for entry in body.get("entry", []):
                    for messaging_event in entry.get("messaging", []):
                        self._handle_messaging_event(messaging_event)
                
                return {"status": "success", "message": "Webhook processed"}
            else:
                logger.warning("Invalid webhook object type")
                return {"status": "error", "message": "Invalid webhook object"}
                
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            return {"status": "error", "message": str(e)}
    
    def _handle_messaging_event(self, event: Dict[str, Any]) -> None:
        """
        Handle individual messaging events
        
        Args:
            event: The messaging event from Facebook
        """
        sender_id = event.get("sender", {}).get("id")
        
        if not sender_id:
            logger.warning("No sender ID in messaging event")
            return
        
        # Handle different types of events
        if "message" in event:
            self._handle_message(sender_id, event["message"])
        elif "postback" in event:
            self._handle_postback(sender_id, event["postback"])
        else:
            logger.info(f"Unhandled event type: {event.keys()}")
    
    def _handle_message(self, sender_id: str, message: Dict[str, Any]) -> None:
        """
        Handle text messages
        
        Args:
            sender_id: The sender's Facebook ID
            message: The message object
        """
        if "text" in message:
            text = message["text"]
            logger.info(f"Received message from {sender_id}: {text}")
            
            # For Phase 1, just echo the message back
            from .message_sender import MessageSender
            sender = MessageSender()
            sender.send_text_message(sender_id, f"Echo: {text}")
        else:
            logger.info(f"Received non-text message from {sender_id}")
    
    def _handle_postback(self, sender_id: str, postback: Dict[str, Any]) -> None:
        """
        Handle postback events (button clicks, etc.)
        
        Args:
            sender_id: The sender's Facebook ID
            postback: The postback object
        """
        payload = postback.get("payload", "")
        logger.info(f"Received postback from {sender_id}: {payload}")
        
        # For Phase 1, just acknowledge the postback
        from .message_sender import MessageSender
        sender = MessageSender()
        sender.send_text_message(sender_id, f"Postback received: {payload}") 