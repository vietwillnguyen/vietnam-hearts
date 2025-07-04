"""
Intent detection for the Vietnam Hearts Agent.
"""

import re
import logging
from typing import Dict, List, Tuple, Optional
from .config import (
    VOLUNTEER_KEYWORDS, FAQ_KEYWORDS,
)
from .gemini_client import GeminiClient

logger = logging.getLogger(__name__)


class IntentDetector:
    """Handles intent detection using both keywords and AI"""
    
    def __init__(self, gemini_client: GeminiClient):
        self.gemini_client = gemini_client
        
    def detect_intent_keywords(self, message: str) -> Tuple[str, float, Dict]:
        """Detect intent using keyword matching"""
        message_lower = message.lower()
        
        
        volunteer_matches = sum(1 for keyword in VOLUNTEER_KEYWORDS if keyword in message_lower)
        faq_matches = sum(1 for keyword in FAQ_KEYWORDS if keyword in message_lower)
        
        total_words = len(message.split())
        volunteer_confidence = volunteer_matches / max(total_words, 1)
        faq_confidence = faq_matches / max(total_words, 1)
        
        if volunteer_matches > faq_matches and volunteer_confidence > 0.1:
            intent = "volunteer"
            confidence = min(volunteer_confidence * 2, 0.9)
            details = {
                "method": "keyword",
                "keywords_found": [kw for kw in VOLUNTEER_KEYWORDS if kw in message_lower]
            }
        elif faq_matches > volunteer_matches and faq_confidence > 0.1:
            intent = "faq"
            confidence = min(faq_confidence * 2, 0.9)
            details = {
                "method": "keyword",
                "keywords_found": [kw for kw in FAQ_KEYWORDS if kw in message_lower]
            }
        else:
            intent = "unknown"
            confidence = 0.0
            details = {"method": "keyword", "keywords_found": []}
            
        return intent, confidence, details
    
    def detect_intent_ai(self, message: str) -> Tuple[str, float, Dict]:
        """Detect intent using AI"""
        return self.gemini_client.detect_intent(message)
    
    def detect_intent_hybrid(self, message: str) -> Tuple[str, float, Dict]:
        """Detect intent using both keyword and AI methods"""
        keyword_intent, keyword_confidence, keyword_details = self.detect_intent_keywords(message)
        ai_intent, ai_confidence, ai_details = self.detect_intent_ai(message)
        
        if keyword_confidence > 0.7 and ai_confidence > 0.7:
            if keyword_intent == ai_intent:
                final_intent = ai_intent
                final_confidence = min(ai_confidence * 1.1, 1.0)
                method = "hybrid_agreement"
            else:
                final_intent = ai_intent
                final_confidence = ai_confidence * 0.8
                method = "hybrid_disagreement"
        elif ai_confidence > keyword_confidence:
            final_intent = ai_intent
            final_confidence = ai_confidence
            method = "ai_preferred"
        else:
            final_intent = keyword_intent
            final_confidence = keyword_confidence
            method = "keyword_preferred"
        
        combined_details = {
            "method": method,
            "keyword_intent": keyword_intent,
            "keyword_confidence": keyword_confidence,
            "ai_intent": ai_intent,
            "ai_confidence": ai_confidence
        }
        
        return final_intent, final_confidence, combined_details
    
    def detect_intent(self, message: str, method: str = "hybrid") -> Tuple[str, float, Dict]:
        """Main intent detection method"""
        if method == "keyword":
            return self.detect_intent_keywords(message)
        elif method == "ai":
            return self.detect_intent_ai(message)
        elif method == "hybrid":
            return self.detect_intent_hybrid(message)
        else:
            raise ValueError(f"Unknown detection method: {method}") 