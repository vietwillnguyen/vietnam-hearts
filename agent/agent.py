"""
Main agent class for the Vietnam Hearts Messenger/Instagram bot.
Simplified to use single LLM call approach with context only.
"""

import logging
import time
from typing import Dict, List, Optional
from datetime import datetime, timezone

from .config import (
    AGENT_NAME, AGENT_VERSION, MOCK_CONVERSATION_ID, MOCK_INCOMING_MESSAGE_ID,
    MOCK_OUTGOING_MESSAGE_ID, RECENT_MESSAGES_LIMIT, MESSAGE_TEXT_TRUNCATE_LENGTH,
    ERROR_CONFIDENCE_THRESHOLD
)
from .gemini_client import GeminiClient
from .knowledge_base import VietnamHeartsKnowledgeBase
from .models import Conversation, Message, MessageRequest, MessageResponse

logger = logging.getLogger(__name__)


class VietnamHeartsAgent:
    """Main agent class for processing messages using single LLM call approach"""
    
    def __init__(self, database_session=None):
        """
        Initialize the agent
        
        Args:
            database_session: SQLAlchemy database session for logging
        """
        self.database_session = database_session
        self.gemini_client = GeminiClient()
        self.knowledge_base = VietnamHeartsKnowledgeBase()
        
        logger.info(f"Initialized {AGENT_NAME} v{AGENT_VERSION}")
    
    def process_message(self, request: MessageRequest) -> MessageResponse:
        """
        Main method to process incoming messages using single LLM call
        
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
            
            # Step 3: Generate response using single LLM call
            context = self._get_conversation_context(conversation)
            response_data = self._generate_response_single_call(
                request.message_text, context
            )
            
            # Step 4: Log outgoing message
            outgoing_message = self._log_outgoing_message(
                conversation, response_data
            )
            
            # Step 5: Update conversation
            self._update_conversation(conversation, response_data)
            
            processing_time = time.time() - start_time
            logger.info(f"Message processed in {processing_time:.2f}s")
            
            return MessageResponse(
                response_text=response_data["response_text"],
                intent="ai_response",  # Simplified intent
                confidence=response_data.get("confidence", 0.8),
                quick_replies=[],  # No quick replies
                should_escalate=response_data.get("should_escalate", False)
            )
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            processing_time = time.time() - start_time
            
            # Return fallback response
            return MessageResponse(
                response_text="I'm sorry, I'm having trouble processing your message right now. Please try again or contact our team directly.",
                intent="error",
                confidence=ERROR_CONFIDENCE_THRESHOLD,
                quick_replies=[],
                should_escalate=True
            )
    
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
    
    def _log_outgoing_message(self, conversation: Conversation, response_data: Dict) -> Message:
        """Log outgoing message to database"""
        if not self.database_session:
            return Message(
                id=MOCK_OUTGOING_MESSAGE_ID,
                conversation_id=conversation.id,
                message_text=response_data["response_text"],
                message_type="outgoing",
                detected_intent="ai_response",
                intent_confidence=response_data.get("confidence", 0.8),
                ai_response=response_data["response_text"],
                timestamp=datetime.now(timezone.utc)
            )
        
        message = Message(
            conversation_id=conversation.id,
            message_text=response_data["response_text"],
            message_type="outgoing",
            detected_intent="ai_response",
            intent_confidence=response_data.get("confidence", 0.8),
            ai_response=response_data["response_text"],
            timestamp=datetime.now(timezone.utc)
        )
        
        self.database_session.add(message)
        self.database_session.commit()
        return message
    
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
    
    def _update_conversation(self, conversation: Conversation, response_data: Dict) -> None:
        """Update conversation with latest response data"""
        if not self.database_session:
            return
        
        conversation.last_message_at = datetime.now(timezone.utc)
        conversation.current_intent = "ai_response"
        
        # Update conversation context
        if not conversation.conversation_context:
            conversation.conversation_context = {}
        
        conversation.conversation_context.update({
            "last_response_type": "ai_response",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "should_escalate": response_data.get("should_escalate", False)
        })
        
        self.database_session.commit()
    
    def _generate_response_single_call(self, message: str, context: str = "") -> Dict:
        """
        Generate response using single LLM call with system prompt approach
        
        Args:
            message: User message
            context: Conversation context
            
        Returns:
            Response dictionary
        """
        # Prepare knowledge base context
        kb_context = self._prepare_knowledge_base_context()
        
        # Get system and user prompts
        system_prompt = self._get_system_prompt()
        user_prompt = self._get_user_prompt(message, context, kb_context)
        
        # Combine prompts for Gemini
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        
        # Get response from Gemini
        response_text = self.gemini_client.generate_response(full_prompt)
        
        # Determine if escalation is needed based on response content
        should_escalate = self._check_escalation_needed(message, response_text)
        
        return {
            "response_text": response_text,
            "confidence": 0.8,
            "should_escalate": should_escalate
        }
    
    def _get_system_prompt(self) -> str:
        """
        Get the system prompt that defines the AI's role and behavior
        
        Returns:
            System prompt string
        """
        from .config import NEW_USER_SIGNUP_LINK
        
        return f"""You are Vietnam Hearts Assistant, a helpful AI agent for Vietnam Hearts, a volunteering organization that helps children in Vietnam.

YOUR ROLE:
- Respond in the same language as the user's message (English or Vietnamese)
- Be friendly, professional, and encouraging
- Use specific information from the knowledge base when relevant
- Keep responses under 150 words
- Always be helpful and welcoming

VOLUNTEER GUIDANCE:
- If the user is interested in volunteering, guide them to sign up using: {NEW_USER_SIGNUP_LINK}
- Do NOT offer the signup link if they are not interested in volunteering or are unable to volunteer
- Emphasize the impact they can make on children's lives

ESCALATION TRIGGERS (respond with escalation message):
- Complaints or problems
- Urgent or emergency situations
- Legal issues
- Requests to speak with team members
- Collaboration inquiries
- Complex questions beyond your knowledge
- When you're unsure about something

ESCALATION MESSAGE:
"Thank you for your message, I think this requires escalation, and a team member will get back to you shortly on this. In the meantime, feel free to sign up to join our volunteering community with: {NEW_USER_SIGNUP_LINK}"

RESPONSE FORMAT:
- Always end with: "If needed, please text 'CONTACT TEAM' and we will get back to you shortly"
- Use the knowledge base information to provide accurate, helpful responses
- Be conversational and natural in your responses"""
    
    def _get_user_prompt(self, message: str, context: str, kb_context: str) -> str:
        """
        Get the user prompt with context and knowledge base
        
        Args:
            message: User message
            context: Conversation context
            kb_context: Knowledge base context
            
        Returns:
            User prompt string
        """
        return f"""KNOWLEDGE BASE:
{kb_context}

CONVERSATION CONTEXT:
{context if context else "No previous context"}

USER MESSAGE: "{message}"

Please respond based on the system instructions above."""
    
    def _prepare_knowledge_base_context(self) -> str:
        """
        Prepare all knowledge base information as context
        
        Returns:
            Formatted knowledge base context
        """
        context_parts = []
        
        # Add organization info
        org_info = self.knowledge_base.get_organization_info()
        context_parts.append(f"ORGANIZATION INFO:\n{org_info}")
        
        # Add location info
        location_info = self.knowledge_base.get_location_info()
        context_parts.append(f"LOCATION INFO:\n{location_info}")
        
        # Add volunteer info
        volunteer_info = self.knowledge_base.get_volunteer_info()
        context_parts.append(f"VOLUNTEER OPPORTUNITIES:\n{volunteer_info}")
        
        # Add class schedule
        schedule_info = self.knowledge_base.get_class_schedule()
        context_parts.append(f"CLASS SCHEDULE:\n{schedule_info}")
        
        # Add donation info
        donation_info = self.knowledge_base.get_donation_info()
        context_parts.append(f"DONATION INFORMATION:\n{donation_info}")
        
        return "\n\n".join(context_parts)
    
    def _check_escalation_needed(self, message: str, response: str) -> bool:
        """
        Check if escalation is needed based on message and response content
        
        Args:
            message: Original user message
            response: Generated response
            
        Returns:
            True if escalation is needed, False otherwise
        """
        message_lower = message.lower()
        response_lower = response.lower()
        
        # Check for escalation indicators in response (English and Vietnamese)
        # Note: "contact team" alone is NOT an escalation indicator - it's in every response
        escalation_phrases = [
            # English phrases (excluding "contact team" which is in every response)
            "requires escalation",
            "team member will get back to you",
            "i will contact a team member",
            "contact a team member",
            "contact the team directly",
            "escalate",
            "human assistance",
            "get back to you shortly on this",
            "think this requires escalation",
            # Vietnamese phrases
            "cần chuyển tiếp",
            "thành viên nhóm sẽ liên hệ lại",
            "tôi sẽ liên hệ với thành viên nhóm",
            "liên hệ với thành viên nhóm",
            "liên hệ trực tiếp với nhóm",
            "nhân viên sẽ liên hệ lại",
            "sẽ liên hệ lại sớm",
            "cần liên hệ với nhóm",
            "chuyển cho nhóm xử lý"
        ]
        
        if any(phrase in response_lower for phrase in escalation_phrases):
            logger.info(f"Escalation detected due to response phrase: {[phrase for phrase in escalation_phrases if phrase in response_lower]}")
            return True
        
        # Check for sensitive topics in user message (English and Vietnamese)
        sensitive_keywords = [
            # English keywords
            "complaint", "problem", "issue", "urgent", "emergency", "safety",
            "money", "payment", "refund", "legal", "lawyer", "sue", "suing",
            "contact team", "speak to someone", "talk to someone", "team member",
            "real person", "human", "escalate", "collaboration", "partnership",
            # Vietnamese keywords
            "khiếu nại", "phàn nàn", "vấn đề", "khẩn cấp", "khẩn", "an toàn",
            "tiền", "thanh toán", "hoàn tiền", "pháp lý", "luật sư", "kiện",
            "liên hệ nhóm", "nói chuyện với ai đó", "thành viên nhóm",
            "cần gặp", "muốn gặp", "cần nói chuyện", "cần liên hệ",
            "người thật", "con người", "chuyển tiếp", "hợp tác", "đối tác"
        ]
        
        if any(keyword in message_lower for keyword in sensitive_keywords):
            logger.info(f"Escalation detected due to sensitive keyword in message: {[keyword for keyword in sensitive_keywords if keyword in message_lower]}")
            return True
        
        # Check for very long messages that might need human attention
        if len(message) > 500:
            logger.info(f"Escalation detected due to long message length: {len(message)} characters")
            return True
        
        logger.info("No escalation needed")
        return False 