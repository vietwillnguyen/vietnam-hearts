"""
Google Gemini client for the Vietnam Hearts Agent.
Simplified for single LLM call approach.
"""

import time
import logging
from typing import Dict, List, Optional

# Try to import google.generativeai, but don't fail if not available
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None

from .config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)


class GeminiClient:
    """Client for interacting with Google Gemini AI"""
    
    def __init__(self):
        """Initialize the Gemini client"""
        if not GEMINI_AVAILABLE:
            logger.warning("google.generativeai not available. AI features will be disabled.")
            self.model = None
            return
        
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not found. AI features will be disabled.")
            self.model = None
            return
        
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            self.model = genai.GenerativeModel(GEMINI_MODEL)
            logger.info("Gemini client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            self.model = None
    
    def generate_response(self, prompt: str) -> str:
        """
        Generate a response using Gemini AI with a simple prompt
        
        Args:
            prompt: The prompt to send to Gemini
            
        Returns:
            Generated response text
        """
        start_time = time.time()
        
        # Check if AI model is available
        if not self.model:
            logger.warning("AI model not available, using fallback response")
            return "I'm sorry, I'm having trouble processing your request right now. Please contact our team directly and they'll be happy to help!"
        
        try:
            response = self.model.generate_content(prompt)
            processing_time = time.time() - start_time
            
            # Log the AI response for debugging
            logger.debug(f"Raw AI response: {response.text}")
            logger.info(f"Response generated in {processing_time:.2f}s")
            logger.info(f"Response length: {len(response.text)} characters")
            
            return response.text.strip()
            
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return "I'm sorry, I'm having trouble processing your request right now. Please contact our team directly and they'll be happy to help!"
    
    def is_available(self) -> bool:
        """
        Check if the Gemini client is available and working
        
        Returns:
            True if available, False otherwise
        """
        return self.model is not None 