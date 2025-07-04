"""
Main agent class for the Vietnam Hearts Messenger/Instagram bot.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

from .config import (
    AGENT_NAME, AGENT_VERSION, MOCK_CONVERSATION_ID, MOCK_INCOMING_MESSAGE_ID,
    MOCK_OUTGOING_MESSAGE_ID, RECENT_MESSAGES_LIMIT, MESSAGE_TEXT_TRUNCATE_LENGTH,
    ERROR_CONFIDENCE_THRESHOLD
)
from .gemini_client import GeminiClient
from .intent_detector import IntentDetector
from .response_generator import ResponseGenerator
from .models import Conversation, Message, MessageRequest, MessageResponse

logger = logging.getLogger(__name__)


class VietnamHeartsAgent:
    """Main agent class for processing messages"""
    
    def __init__(self, database_session=None):
        """
        Initialize the agent
        
        Args:
            database_session: SQLAlchemy database session for logging
        """
        self.database_session = database_session
        self.gemini_client = GeminiClient()
        self.intent_detector = IntentDetector(self.gemini_client)
        self.response_generator = ResponseGenerator(self.gemini_client)
        
        logger.info(f"Initialized {AGENT_NAME} v{AGENT_VERSION}")
    
    def process_message(self, request: MessageRequest) -> MessageResponse:
        """
        Main method to process incoming messages
        
        Args:
            request: Message request containing user info and message
            
        Returns:
            Message response with generated reply
        """
        start_time = time.time()
        
        try:
            # Step 1: Get or create conversation
            conversation = self._get_or_create_conversation(request)
            
            # Step 2: Log incoming message
            incoming_message = self._log_incoming_message(conversation, request)
            
            # Step 3: Detect intent
            intent, confidence, intent_details = self.intent_detector.detect_intent(
                request.message_text
            )
            
            # Step 4: Check if escalation is needed
            should_escalate = self.gemini_client.should_escalate(
                request.message_text, intent, confidence
            )
            
            # Step 5: Generate response
            context = self._get_conversation_context(conversation)
            response_data = self.response_generator.generate_response(
                request.message_text, intent, confidence, context, should_escalate
            )
            
            # Step 6: Log outgoing message
            outgoing_message = self._log_outgoing_message(
                conversation, response_data, intent_details
            )
            
            # Step 7: Update conversation
            self._update_conversation(conversation, intent, response_data)
            
            # Step 8: Handle escalation if needed
            if should_escalate:
                self._handle_escalation(conversation, request, response_data)
            
            processing_time = time.time() - start_time
            logger.info(f"Message processed in {processing_time:.2f}s: {intent} (confidence: {confidence:.2f})")
            
            return MessageResponse(
                response_text=response_data["response_text"],
                intent=response_data["intent"],
                confidence=response_data["confidence"],
                quick_replies=response_data.get("quick_replies", []),
                should_escalate=should_escalate
            )
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            processing_time = time.time() - start_time
            
            # Return fallback response
            return MessageResponse(
                response_text="I'm sorry, I'm having trouble processing your message right now. Please try again or contact our team directly.",
                intent="error",
                confidence=ERROR_CONFIDENCE_THRESHOLD,
                quick_replies=[
                    {"text": "Contact Team", "payload": "CONTACT"},
                    {"text": "Try Again", "payload": "RETRY"}
                ],
                should_escalate=True
            )
    
    def process_quick_reply(self, user_id: str, platform: str, payload: str) -> MessageResponse:
        """
        Process quick reply button clicks
        
        Args:
            user_id: User ID
            platform: Platform (messenger, instagram)
            payload: Quick reply payload
            
        Returns:
            Message response
        """
        try:
            # Get conversation (or create mock for testing)
            conversation = self._get_conversation(user_id, platform)
            if not conversation and not self.database_session:
                # Create mock conversation for testing
                conversation = type('MockConversation', (), {
                    'id': MOCK_CONVERSATION_ID,
                    'user_id': user_id,
                    'platform': platform,
                    'conversation_context': {}
                })()
            
            if not conversation:
                return self._create_error_response("Conversation not found")
            
            # Generate response for quick reply
            context = self._get_conversation_context(conversation)
            response_data = self.response_generator.generate_quick_reply_response(payload, context)
            
            # Log the interaction (only if database is available)
            if self.database_session:
                self._log_quick_reply(conversation, payload, response_data)
            
            return MessageResponse(
                response_text=response_data["response_text"],
                intent=response_data["intent"],
                confidence=response_data["confidence"],
                quick_replies=response_data.get("quick_replies", []),
                should_escalate=response_data.get("should_escalate", False)
            )
            
        except Exception as e:
            logger.error(f"Error processing quick reply: {e}")
            return self._create_error_response("Error processing quick reply")
    
    def _get_or_create_conversation(self, request: MessageRequest) -> Conversation:
        """Get existing conversation or create new one"""
        if not self.database_session:
            # Mock conversation for testing
            return Conversation(
                id=MOCK_CONVERSATION_ID,
                user_id=request.user_id,
                platform=request.platform,
                user_name=request.user_name,
                started_at=datetime.now(timezone.utc),
                last_message_at=datetime.now(timezone.utc),
                is_active=True
            )
        
        conversation = self.database_session.query(Conversation).filter(
            Conversation.user_id == request.user_id,
            Conversation.platform == request.platform,
            Conversation.is_active == True
        ).first()
        
        if not conversation:
            conversation = Conversation(
                user_id=request.user_id,
                platform=request.platform,
                user_name=request.user_name,
                started_at=datetime.now(timezone.utc),
                last_message_at=datetime.now(timezone.utc),
                is_active=True
            )
            self.database_session.add(conversation)
            self.database_session.commit()
        
        return conversation
    
    def _get_conversation(self, user_id: str, platform: str) -> Optional[Conversation]:
        """Get existing conversation"""
        if not self.database_session:
            return None
        
        return self.database_session.query(Conversation).filter(
            Conversation.user_id == user_id,
            Conversation.platform == platform,
            Conversation.is_active == True
        ).first()
    
    def _log_incoming_message(self, conversation: Conversation, request: MessageRequest) -> Message:
        """Log incoming message to database"""
        if not self.database_session:
            return Message(
                id=MOCK_INCOMING_MESSAGE_ID,
                conversation_id=conversation.id,
                message_text=request.message_text,
                message_type="incoming",
                timestamp=datetime.now(timezone.utc)
            )
        
        message = Message(
            conversation_id=conversation.id,
            message_text=request.message_text,
            message_type="incoming",
            platform_message_id=request.platform_message_id,
            timestamp=datetime.now(timezone.utc)
        )
        
        self.database_session.add(message)
        self.database_session.commit()
        return message
    
    def _log_outgoing_message(self, conversation: Conversation, response_data: Dict, intent_details: Dict) -> Message:
        """Log outgoing message to database"""
        if not self.database_session:
            return Message(
                id=MOCK_OUTGOING_MESSAGE_ID,
                conversation_id=conversation.id,
                message_text=response_data["response_text"],
                message_type="outgoing",
                detected_intent=response_data["intent"],
                intent_confidence=response_data["confidence"],
                ai_response=response_data["response_text"],
                timestamp=datetime.now(timezone.utc)
            )
        
        message = Message(
            conversation_id=conversation.id,
            message_text=response_data["response_text"],
            message_type="outgoing",
            detected_intent=response_data["intent"],
            intent_confidence=response_data["confidence"],
            ai_response=response_data["response_text"],
            timestamp=datetime.now(timezone.utc)
        )
        
        self.database_session.add(message)
        self.database_session.commit()
        return message
    
    def _log_quick_reply(self, conversation: Conversation, payload: str, response_data: Dict) -> None:
        """Log quick reply interaction"""
        if not self.database_session:
            return
        
        # Log the quick reply as an incoming message
        incoming = Message(
            conversation_id=conversation.id,
            message_text=f"[Quick Reply: {payload}]",
            message_type="incoming",
            timestamp=datetime.now(timezone.utc)
        )
        
        # Log the response as outgoing message
        outgoing = Message(
            conversation_id=conversation.id,
            message_text=response_data["response_text"],
            message_type="outgoing",
            detected_intent=response_data["intent"],
            intent_confidence=response_data["confidence"],
            timestamp=datetime.now(timezone.utc)
        )
        
        self.database_session.add(incoming)
        self.database_session.add(outgoing)
        self.database_session.commit()
    
    def _get_conversation_context(self, conversation: Conversation) -> str:
        """Get conversation context for AI processing"""
        if not self.database_session:
            return ""
        
        # Get recent messages
        recent_messages = self.database_session.query(Message).filter(
            Message.conversation_id == conversation.id
        ).order_by(Message.timestamp.desc()).limit(RECENT_MESSAGES_LIMIT).all()
        
        if not recent_messages:
            return ""
        
        context = "Recent conversation:\n"
        for msg in reversed(recent_messages):
            context += f"- {msg.message_type}: {msg.message_text[:MESSAGE_TEXT_TRUNCATE_LENGTH]}...\n"
        
        return context
    
    def _update_conversation(self, conversation: Conversation, intent: str, response_data: Dict) -> None:
        """Update conversation with latest intent and context"""
        if not self.database_session:
            return
        
        conversation.last_message_at = datetime.now(timezone.utc)
        conversation.current_intent = intent
        
        # Update conversation context
        if not conversation.conversation_context:
            conversation.conversation_context = {}
        
        conversation.conversation_context.update({
            "last_intent": intent,
            "last_response_type": response_data["intent"],
            "last_updated": datetime.now(timezone.utc).isoformat()
        })
        
        self.database_session.commit()
    
    def _handle_escalation(self, conversation: Conversation, request: MessageRequest, response_data: Dict) -> None:
        """Handle escalation to human team"""
        logger.info(f"Escalating conversation {conversation.id} for user {request.user_id}")
        
        # TODO: Implement escalation logic
        # - Send email to team
        # - Create ticket in support system
        # - Send notification to Slack/Discord
        
        if self.database_session:
            # Mark message as escalated
            message = self.database_session.query(Message).filter(
                Message.conversation_id == conversation.id,
                Message.message_text == request.message_text
            ).first()
            
            if message:
                message.is_escalated = True
                self.database_session.commit()
    
    def _create_error_response(self, error_message: str) -> MessageResponse:
        """Create error response"""
        return MessageResponse(
            response_text=f"I'm sorry, {error_message}. Please try again or contact our team directly.",
            intent="error",
            confidence=ERROR_CONFIDENCE_THRESHOLD,
            quick_replies=[
                {"text": "Contact Team", "payload": "CONTACT"},
                {"text": "Try Again", "payload": "RETRY"}
            ],
            should_escalate=True
        ) 