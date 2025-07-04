"""
Configuration settings for the Vietnam Hearts Agent.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Google Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Agent Configuration
AGENT_NAME = "Vietnam Hearts Assistant"
AGENT_VERSION = "1.0.0"

# Message Processing Configuration
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "1000"))
MESSAGE_TIMEOUT = int(os.getenv("MESSAGE_TIMEOUT", "30"))  # seconds

# Intent Detection Configuration
INTENT_CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "0.5"))

# Confidence thresholds for different response types
KB_CONFIDENCE_THRESHOLD = float(os.getenv("KB_CONFIDENCE_THRESHOLD", "0.9"))
AI_CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.8"))
VOLUNTEER_CONFIDENCE_THRESHOLD = float(os.getenv("VOLUNTEER_CONFIDENCE_THRESHOLD", "0.7"))
FAQ_CONFIDENCE_THRESHOLD = float(os.getenv("FAQ_CONFIDENCE_THRESHOLD", "0.9"))

VOLUNTEER_KEYWORDS = [
    "volunteer", "volunteering", "help", "teach", "teaching", "assist", "join",
    "sign up", "signup", "participate", "contribute", "get involved"
]
FAQ_KEYWORDS = [
    "location", "where", "when", "time", "schedule", "hours", "address",
    "contact", "phone", "email", "website", "info", "information",
    "what", "how", "why", "cost", "price", "free", "donation"
]

# Keywords that should trigger FAQ even if they contain volunteer words
FAQ_OVERRIDE_KEYWORDS = [
    "how can i volunteer", "how to volunteer", "volunteer information", 
    "volunteer details", "volunteer info", "volunteer help",
    "what is", "what are", "tell me about"
]

# Response Templates
RESPONSE_TEMPLATES = {
    "volunteer_interest": {
        "message": "Thank you for your interest in volunteering with Vietnam Hearts! We are always looking for volunteer teachers and assistants 🙌\n\nYou can sign up here: {signup_link}\n\nWe'd love to have you join our community of volunteers making a difference in Vietnam!",
        "quick_replies": [
            {"text": "Sign Up Now", "payload": "SIGNUP"},
            {"text": "Learn More", "payload": "LEARN_MORE"},
            {"text": "Contact Us", "payload": "CONTACT"}
        ]
    },
    "faq_response": {
        "message": "{response}\n\nIs there anything else I can help you with?",
        "quick_replies": [
            {"text": "Volunteer", "payload": "VOLUNTEER"},
            {"text": "More Questions", "payload": "FAQ"},
            {"text": "Contact Team", "payload": "CONTACT"}
        ]
    },
    "fallback": {
        "message": "I'm not sure how to help with that just yet, but someone from our team will get back to you soon! In the meantime, you can:\n\n• Sign up to volunteer: {signup_link}\n• Check our FAQ: {faq_link}\n• Contact us directly: {contact_link}",
        "quick_replies": [
            {"text": "Volunteer", "payload": "VOLUNTEER"},
            {"text": "FAQ", "payload": "FAQ"},
            {"text": "Contact Team", "payload": "CONTACT"}
        ]
    },
    "escalation": {
        "message": "I've forwarded your message to our team. They'll get back to you within 24 hours. Thank you for your patience! 🙏",
        "quick_replies": [
            {"text": "Sign Up to Volunteer", "payload": "SIGNUP"},
            {"text": "Check FAQ", "payload": "FAQ"}
        ]
    }
}

# External Links (from main config)
NEW_USER_SIGNUP_LINK = os.getenv("NEW_USER_SIGNUP_LINK")
FACEBOOK_MESSENGER_LINK = os.getenv("FACEBOOK_MESSENGER_LINK")
INSTAGRAM_LINK = os.getenv("INSTAGRAM_LINK")
FACEBOOK_PAGE_LINK = os.getenv("FACEBOOK_PAGE_LINK")

# Logging Configuration
AGENT_LOG_LEVEL = os.getenv("AGENT_LOG_LEVEL", "INFO")
AGENT_LOG_FILE = PROJECT_ROOT / "logs" / "agent.log"

# Database Configuration (for message logging)
AGENT_DATABASE_URL = os.getenv("AGENT_DATABASE_URL", "sqlite:///./agent.db")

# Rate Limiting
MAX_MESSAGES_PER_MINUTE = int(os.getenv("MAX_MESSAGES_PER_MINUTE", "10"))
MAX_MESSAGES_PER_HOUR = int(os.getenv("MAX_MESSAGES_PER_HOUR", "100"))

# Agent Processing Configuration
MOCK_CONVERSATION_ID = int(os.getenv("MOCK_CONVERSATION_ID", "1"))
MOCK_INCOMING_MESSAGE_ID = int(os.getenv("MOCK_INCOMING_MESSAGE_ID", "1"))
MOCK_OUTGOING_MESSAGE_ID = int(os.getenv("MOCK_OUTGOING_MESSAGE_ID", "2"))
RECENT_MESSAGES_LIMIT = int(os.getenv("RECENT_MESSAGES_LIMIT", "5"))
MESSAGE_TEXT_TRUNCATE_LENGTH = int(os.getenv("MESSAGE_TEXT_TRUNCATE_LENGTH", "100"))
ERROR_CONFIDENCE_THRESHOLD = float(os.getenv("ERROR_CONFIDENCE_THRESHOLD", "0.0"))

# Required Environment Variables
REQUIRED_ENV_VARS = [
    "GEMINI_API_KEY",
    "NEW_USER_SIGNUP_LINK",
]

def validate_agent_config():
    """Validate agent configuration settings."""
    # Only validate in production or when explicitly required
    if os.getenv("ENVIRONMENT") == "production":
        missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
        if missing_vars:
            raise ValueError(
                f"Missing required environment variables for agent: {', '.join(missing_vars)}\n"
                "Please set these variables in your .env file."
            )
    else:
        # In development, just warn about missing variables
        missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
        if missing_vars:
            logger.warning(f"Missing optional environment variables for agent: {', '.join(missing_vars)}")
            logger.warning("Agent will work with reduced functionality (keyword-only detection)")

# Validate configuration on import
validate_agent_config() 