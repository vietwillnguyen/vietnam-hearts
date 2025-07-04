"""
Google Gemini client for the Vietnam Hearts Agent.
"""

import google.generativeai as genai
import time
import logging
from typing import Dict, List, Tuple
from .config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)


class GeminiClient:
    """Client for interacting with Google Gemini AI"""
    
    def __init__(self):
        """Initialize the Gemini client"""
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not found. AI features will be disabled.")
            self.model = None
            self.chat = None
            return
        
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            self.model = genai.GenerativeModel(GEMINI_MODEL)
            self.chat = None
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            self.model = None
            self.chat = None
        
    def _create_system_prompt(self) -> str:
        """Create the system prompt for the agent"""
        return """You are Vietnam Hearts Assistant, a helpful AI agent for Vietnam Hearts, a volunteering organization that helps children in Vietnam.

Your role is to:
1. Help people learn about Vietnam Hearts and our mission
2. Guide people interested in volunteering to sign up
3. Answer general questions about our organization
4. Provide helpful, friendly, and professional responses

Key information about Vietnam Hearts:
- We are a volunteering organization helping children in Vietnam
- We offer teaching and non-teaching volunteer opportunities
- We have locations in Vietnam where volunteers can help
- We provide training and support for volunteers
- Our mission is to make a positive impact on children's lives through education and support

Always be:
- Friendly and welcoming
- Professional and informative
- Encouraging for those interested in volunteering
- Clear about how people can get involved
- Honest about what you know and don't know

If you're unsure about something, suggest contacting the team directly."""

    def detect_intent(self, message: str) -> Tuple[str, float, Dict]:
        """
        Detect the intent of a user message using Gemini AI
        
        Args:
            message: The user's message text
            
        Returns:
            Tuple of (intent, confidence, details)
        """
        start_time = time.time()
        
        # Check if AI model is available
        if not self.model:
            logger.warning("AI model not available, using fallback intent detection")
            return "unknown", 0.0, {
                "reasoning": "AI model not available",
                "keywords_found": [],
                "ai_response": None
            }
        
        try:
            prompt = f"""Analyze this message and determine the user's intent:

Message: "{message}"

Please classify the intent as one of:
1. "volunteer" - User is interested in volunteering, helping, teaching, or getting involved
2. "faq" - User is asking general questions about Vietnam Hearts, locations, schedules, etc.
3. "unknown" - Intent is unclear or doesn't fit the above categories

Respond in this exact JSON format:
{{
    "intent": "volunteer|faq|unknown",
    "confidence": 0.0-1.0,
    "reasoning": "Brief explanation of why this intent was chosen",
    "keywords_found": ["list", "of", "relevant", "keywords"]
}}"""

            response = self.model.generate_content(prompt)
            
            # Parse the response
            try:
                import json
                result = json.loads(response.text)
                intent = result.get("intent", "unknown")
                confidence = result.get("confidence", 0.0)
                details = {
                    "reasoning": result.get("reasoning", ""),
                    "keywords_found": result.get("keywords_found", []),
                    "ai_response": response.text
                }
            except json.JSONDecodeError:
                # Fallback parsing - try to extract intent from text
                response_text = response.text.lower()
                if "volunteer" in response_text:
                    intent = "volunteer"
                    confidence = 0.6
                elif "faq" in response_text or "question" in response_text:
                    intent = "faq"
                    confidence = 0.6
                else:
                    intent = "unknown"
                    confidence = 0.0
                
                details = {
                    "reasoning": f"Failed to parse JSON, extracted from text: {response.text[:100]}",
                    "keywords_found": [],
                    "ai_response": response.text
                }
            
            processing_time = time.time() - start_time
            
            logger.info(f"Intent detection completed in {processing_time:.2f}s: {intent} (confidence: {confidence})")
            
            return intent, confidence, details
            
        except Exception as e:
            logger.error(f"Error in intent detection: {e}")
            processing_time = time.time() - start_time
            return "unknown", 0.0, {
                "reasoning": f"Error occurred: {str(e)}",
                "keywords_found": [],
                "ai_response": None,
                "processing_time": processing_time
            }

    def generate_faq_response(self, question: str, context: str = "") -> str:
        """
        Generate a helpful response for FAQ questions using Gemini with document context
        
        Args:
            question: The user's question
            context: Additional context including knowledge base information
            
        Returns:
            Generated response text with document citations
        """
        start_time = time.time()
        
        # Check if AI model is available
        if not self.model:
            logger.warning("AI model not available, using fallback FAQ response")
            return "I'm sorry, I'm having trouble processing your question right now. Please contact our team directly and they'll be happy to help!"
        
        try:
            system_prompt = self._create_system_prompt()
            
            prompt = f"""{system_prompt}

Document Context (Vietnam Hearts Information):
{context}

User Question: "{question}"

Please provide a helpful, informative response about Vietnam Hearts using the document context provided above. 

Guidelines:
- Use specific information from the provided context when available
- Be encouraging and welcoming
- If you reference specific information, mention it comes from Vietnam Hearts documentation
- If you don't have specific information about something, suggest contacting the team directly
- Keep your response friendly, professional, and under 200 words

At the end of your response, add a brief citation like: "Source: Vietnam Hearts Documentation" if you used specific information from the context."""

            response = self.model.generate_content(prompt)
            processing_time = time.time() - start_time
            
            logger.info(f"FAQ response generated in {processing_time:.2f}s")
            
            return response.text.strip()
            
        except Exception as e:
            logger.error(f"Error generating FAQ response: {e}")
            return "I'm sorry, I'm having trouble processing your question right now. Please contact our team directly and they'll be happy to help!"

    def should_escalate(self, message: str, intent: str, confidence: float) -> bool:
        """
        Determine if a message should be escalated to human team
        
        Args:
            message: The user's message
            intent: Detected intent
            confidence: Confidence score
            
        Returns:
            True if should escalate, False otherwise
        """
        # Don't escalate if we have a clear intent with reasonable confidence
        if intent in ["volunteer", "faq"] and confidence >= 0.5:
            return False
            
        # Complex or sensitive topics that need human attention
        sensitive_keywords = [
            "complaint", "problem", "issue", "urgent", "emergency", "safety",
            "money", "payment", "refund", "legal", "lawyer", "sue", "suing"
        ]
        
        message_lower = message.lower()
        if any(keyword in message_lower for keyword in sensitive_keywords):
            return True
            
        # Very long messages might need human attention
        if len(message) > 500:
            return True
            
        # Only escalate if confidence is very low AND intent is unknown
        if intent == "unknown" and confidence < 0.3:
            return True
            
        # Don't escalate for simple greetings or basic questions
        simple_greetings = ["hello", "hi", "hey", "good morning", "good afternoon", "good evening"]
        if message_lower.strip() in simple_greetings:
            return False
            
        return False

    def get_conversation_summary(self, messages: List[Dict]) -> str:
        """
        Generate a summary of the conversation for context
        
        Args:
            messages: List of message dictionaries
            
        Returns:
            Conversation summary
        """
        if not messages:
            return ""
            
        recent_messages = messages[-5:]  # Last 5 messages
        
        summary = "Recent conversation:\n"
        for msg in recent_messages:
            summary += f"- User: {msg.get('message_text', '')[:100]}...\n"
            
        return summary 