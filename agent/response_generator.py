"""
Response generator for the Vietnam Hearts Agent.
"""

import logging
from typing import Dict, List, Optional
from .config import (
    NEW_USER_SIGNUP_LINK, RESPONSE_TEMPLATES, FACEBOOK_MESSENGER_LINK, INSTAGRAM_LINK,
    KB_CONFIDENCE_THRESHOLD, AI_CONFIDENCE_THRESHOLD
)
from .gemini_client import GeminiClient
from .knowledge_base import VietnamHeartsKnowledgeBase

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """Generates appropriate responses based on detected intents"""
    
    def __init__(self, gemini_client: GeminiClient):
        """Initialize the response generator"""
        self.gemini_client = gemini_client
        self.knowledge_base = VietnamHeartsKnowledgeBase()
        
        self.kb_threshold = KB_CONFIDENCE_THRESHOLD
        self.ai_threshold = AI_CONFIDENCE_THRESHOLD
        
    def generate_volunteer_response(self, message: str, context: str = "") -> Dict:
        """
        Generate response for volunteer interest
        
        Args:
            message: Original user message
            context: Conversation context
            
        Returns:
            Response dictionary with message and quick replies
        """
        template = RESPONSE_TEMPLATES["volunteer_interest"]
        
        # Get volunteer information from knowledge base
        volunteer_info = self.knowledge_base.get_volunteer_info()
        
        # Customize message based on context
        if "teach" in message.lower() or "teaching" in message.lower():
            response_text = template["message"].format(
                signup_link=NEW_USER_SIGNUP_LINK
            ) + "\n\nWe have both teaching and non-teaching opportunities available!"
        else:
            response_text = template["message"].format(
                signup_link=NEW_USER_SIGNUP_LINK
            )
        
        # Add specific volunteer information
        response_text += "\n\n" + volunteer_info
        
        return {
            "response_text": response_text,
            "quick_replies": template["quick_replies"],
            "intent": "volunteer",
            "confidence": 1.0,
            "should_escalate": False
        }
    
    def generate_faq_response(self, message: str, context: str = "") -> Dict:
        """
        Generate response for FAQ questions using hybrid approach:
        1. Check knowledge base for exact/close matches
        2. Return KB answer with source citation (if found with high confidence)
        3. Send to Gemini with document context (if not found or low confidence)
        4. Return Gemini response with document citations
        
        Args:
            message: Original user message
            context: Conversation context
            
        Returns:
            Response dictionary with message and quick replies
        """
        # Step 1: Check knowledge base for exact/close matches
        kb_result = self.knowledge_base.search_knowledge(message)
        
        if kb_result and kb_result["confidence"] >= self.kb_threshold:
            # Step 2: Return KB answer with source citation
            response_text = kb_result["content"]
            response_text += f"\n\n*Source: {kb_result['source']}*"
            confidence = kb_result["confidence"]
            source_type = "knowledge_base"
        else:
            # Step 3: Send to Gemini with document context
            # Prepare context with knowledge base information
            kb_context = self._prepare_kb_context_for_ai()
            enhanced_context = f"{context}\n\nVietnam Hearts Information:\n{kb_context}"
            
            ai_response = self.gemini_client.generate_faq_response(message, enhanced_context)
            response_text = ai_response
            confidence = self.ai_threshold
            source_type = "ai_with_context"
        
        # Add follow-up question
        response_text += "\n\nIs there anything else I can help you with?"
        
        return {
            "response_text": response_text,
            "quick_replies": RESPONSE_TEMPLATES["faq_response"]["quick_replies"],
            "intent": "faq",
            "confidence": confidence,
            "should_escalate": False,
            "source_type": source_type
        }
    
    def _prepare_kb_context_for_ai(self) -> str:
        """
        Prepare knowledge base information as context for AI
        
        Returns:
            Formatted context string
        """
        context_parts = []
        
        # Add organization info
        org_info = self.knowledge_base.get_organization_info()
        context_parts.append(f"Organization: {org_info}")
        
        # Add location info
        location_info = self.knowledge_base.get_location_info()
        context_parts.append(f"Location: {location_info}")
        
        # Add volunteer info
        volunteer_info = self.knowledge_base.get_volunteer_info()
        context_parts.append(f"Volunteer Opportunities: {volunteer_info}")
        
        # Add class schedule
        schedule_info = self.knowledge_base.get_class_schedule()
        context_parts.append(f"Class Schedule: {schedule_info}")
        
        return "\n\n".join(context_parts)
    
    def generate_fallback_response(self, message: str, context: str = "") -> Dict:
        """
        Generate fallback response for unknown intents
        
        Args:
            message: Original user message
            context: Conversation context
            
        Returns:
            Response dictionary with message and quick replies
        """
        template = RESPONSE_TEMPLATES["fallback"]
        
        # Create fallback links
        faq_link = FACEBOOK_MESSENGER_LINK or "https://facebook.com/vietnamhearts"
        contact_link = FACEBOOK_MESSENGER_LINK or "https://facebook.com/vietnamhearts"
        
        response_text = template["message"].format(
            signup_link=NEW_USER_SIGNUP_LINK,
            faq_link=faq_link,
            contact_link=contact_link
        )
        
        return {
            "response_text": response_text,
            "quick_replies": template["quick_replies"],
            "intent": "unknown",
            "confidence": 0.0,
            "should_escalate": True
        }
    
    def generate_escalation_response(self, message: str, context: str = "") -> Dict:
        """
        Generate response when escalating to human team
        
        Args:
            message: Original user message
            context: Conversation context
            
        Returns:
            Response dictionary with message and quick replies
        """
        template = RESPONSE_TEMPLATES["escalation"]
        
        response_text = template["message"]
        
        return {
            "response_text": response_text,
            "quick_replies": template["quick_replies"],
            "intent": "escalation",
            "confidence": 1.0,
            "should_escalate": True
        }
    
    def generate_response(self, message: str, intent: str, confidence: float, 
                         context: str = "", should_escalate: bool = False) -> Dict:
        """
        Main response generation method
        
        Args:
            message: Original user message
            intent: Detected intent
            confidence: Confidence score
            context: Conversation context
            should_escalate: Whether to escalate to human team
            
        Returns:
            Response dictionary
        """
        if should_escalate:
            return self.generate_escalation_response(message, context)
        
        if intent == "volunteer":
            return self.generate_volunteer_response(message, context)
        elif intent == "faq":
            return self.generate_faq_response(message, context)
        else:
            return self.generate_fallback_response(message, context)
    
    def generate_quick_reply_response(self, payload: str, context: str = "") -> Dict:
        """
        Generate response for quick reply button clicks
        
        Args:
            payload: Quick reply payload
            context: Conversation context
            
        Returns:
            Response dictionary
        """
        if payload == "SIGNUP":
            return {
                "response_text": f"Great! You can sign up to volunteer here: {NEW_USER_SIGNUP_LINK}\n\n You will get an email with more information about the next steps.",
                "quick_replies": [
                    {"text": "Learn More", "payload": "LEARN_MORE"},
                    {"text": "Contact Us", "payload": "CONTACT"}
                ],
                "intent": "volunteer",
                "confidence": 1.0,
                "should_escalate": False
            }
        elif payload == "LEARN_MORE":
            org_info = self.knowledge_base.get_organization_info()
            return {
                "response_text": org_info,
                "quick_replies": [
                    {"text": "Sign Up Now", "payload": "SIGNUP"},
                    {"text": "Contact Us", "payload": "CONTACT"}
                ],
                "intent": "faq",
                "confidence": 0.9,
                "should_escalate": False
            }
        elif payload == "CONTACT":
            return {
                "response_text": "You can contact our team through:\n\n• Facebook Messenger: " + (FACEBOOK_MESSENGER_LINK or "https://facebook.com/vietnamhearts") + "\n• Instagram: " + (INSTAGRAM_LINK or "https://instagram.com/vietnamhearts") + "\n\nWe'll get back to you within 24 hours!",
                "quick_replies": [
                    {"text": "Sign Up to Volunteer", "payload": "SIGNUP"},
                    {"text": "Learn More", "payload": "LEARN_MORE"}
                ],
                "intent": "faq",
                "confidence": 0.9,
                "should_escalate": False
            }
        elif payload == "FAQ":
            return {
                "response_text": "I'm here to help! What would you like to know about Vietnam Hearts? You can ask about:\n\n• Our mission and programs\n• Volunteer opportunities\n• Locations and schedules\n• How to get involved",
                "quick_replies": [
                    {"text": "Volunteer", "payload": "SIGNUP"},
                    {"text": "Contact Team", "payload": "CONTACT"}
                ],
                "intent": "faq",
                "confidence": 0.8,
                "should_escalate": False
            }
        else:
            return self.generate_fallback_response("", context) 