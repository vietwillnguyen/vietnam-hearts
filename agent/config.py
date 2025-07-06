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

# Intent Constants
INTENT_VOLUNTEER = "volunteer"
INTENT_FAQ = "faq"
INTENT_UNKNOWN = "unknown"
INTENT_CONTACT_TEAM = "contact_team"

# Available intents list for validation
AVAILABLE_INTENTS = [
    INTENT_VOLUNTEER,
    INTENT_FAQ,
    INTENT_UNKNOWN,
    INTENT_CONTACT_TEAM
]

# Confidence thresholds for different response types
KB_CONFIDENCE_THRESHOLD = float(os.getenv("KB_CONFIDENCE_THRESHOLD", "0.9"))
AI_CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.8"))
VOLUNTEER_CONFIDENCE_THRESHOLD = float(os.getenv("VOLUNTEER_CONFIDENCE_THRESHOLD", "0.7"))
FAQ_CONFIDENCE_THRESHOLD = float(os.getenv("FAQ_CONFIDENCE_THRESHOLD", "0.9"))

VOLUNTEER_KEYWORDS = [
    "volunteer", "volunteering", "teach", "teaching", "join", "helping out", "help", "assist", "assistant"
    "sign up", "signup", "participate", "contribute", "get involved", "apply", "apply now"
]
FAQ_KEYWORDS = [
    "location", "where", "when", "time", "times","schedule", "hours", "address",
    "cost", "price", "free", "donation", "donate", "when do", "weekend",
    "vietnam hearts", "organization", "mission", "program",
    "what is", "what are", "tell me about", "learn more", "want to learn", "details"
]

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

# Response Formatting Configuration
MAX_RESPONSE_LENGTH = int(os.getenv("MAX_RESPONSE_LENGTH", "2000"))
FULL_FAQ_LINK = os.getenv("FULL_FAQ_LINK", "https://facebook.com/vietnamhearts")

# Quick Reply Configuration
CONTACT_ESCALATION_ENABLED = os.getenv("CONTACT_ESCALATION_ENABLED", "true").lower() == "true"

# Centralized Quick Reply Definitions
QUICK_REPLIES = {
    "SIGNUP": {"text": "Sign Up Now", "payload": "SIGNUP"},
    "LEARN MORE": {"text": "Learn More", "payload": "LEARN MORE"},
    "CONTACT US": {"text": "Contact Us", "payload": "CONTACT US"},
    "LOCATION": {"text": "Location Info", "payload": "LOCATION"},
    "SCHEDULE": {"text": "Class Schedule", "payload": "SCHEDULE"},
}

# Quick Reply Sets
QUICK_REPLY_SETS = {
    "volunteer": [
        QUICK_REPLIES["SIGNUP"],
        QUICK_REPLIES["LEARN MORE"],
        QUICK_REPLIES["CONTACT US"]
    ],
    "faq": [
        QUICK_REPLIES["SIGNUP"],
        QUICK_REPLIES["SCHEDULE"],
        QUICK_REPLIES["LEARN MORE"],
        QUICK_REPLIES["CONTACT US"]
    ],
    "fallback": [
        QUICK_REPLIES["SIGNUP"],
        QUICK_REPLIES["LEARN MORE"],
        QUICK_REPLIES["CONTACT US"]
    ],
    "location": [
        QUICK_REPLIES["SCHEDULE"],
        QUICK_REPLIES["SIGNUP"],
        QUICK_REPLIES["CONTACT US"]
    ],
    "schedule": [
        QUICK_REPLIES["SIGNUP"],
        QUICK_REPLIES["LOCATION"],
        QUICK_REPLIES["CONTACT US"]
    ],
    "volunteer_info": [
        QUICK_REPLIES["LOCATION"],
        QUICK_REPLIES["SCHEDULE"],
        QUICK_REPLIES["CONTACT US"]
    ]
}

# Response Templates
RESPONSE_TEMPLATES = {
    "user_signup_interest": {
        "message": "Thank you for your interest in volunteering with Vietnam Hearts! We are always looking for volunteer teachers and assistants 🙌\n\nYou can sign up here: {signup_link}\n\nWe'd love to have you join our community of volunteers making a difference in Vietnam!",
        "quick_replies": QUICK_REPLY_SETS["volunteer"]
    },
    "contact_team": {
        "message": "I've forwarded your message to our team. They'll get back to you within 24 hours. Thank you for your patience! 🙏\n\nIn the meantime, you can:\n• Sign up to volunteer: {signup_link}\n",
        "quick_replies": QUICK_REPLY_SETS["fallback"]
    }
}

# Escalation Configuration
ESCALATION_CONFIDENCE_THRESHOLD = float(os.getenv("ESCALATION_CONFIDENCE_THRESHOLD", "0.5"))
ESCALATION_UNKNOWN_CONFIDENCE_THRESHOLD = float(os.getenv("ESCALATION_UNKNOWN_CONFIDENCE_THRESHOLD", "0.3"))
ESCALATION_MESSAGE_LENGTH_THRESHOLD = int(os.getenv("ESCALATION_MESSAGE_LENGTH_THRESHOLD", "500"))

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

def validate_intent(intent: str) -> bool:
    """Validate that an intent is one of the allowed values"""
    return intent in AVAILABLE_INTENTS

def get_intent_constant(intent_name: str) -> str:
    """Get the intent constant for a given intent name"""
    intent_mapping = {
        "volunteer": INTENT_VOLUNTEER,
        "faq": INTENT_FAQ,
        "unknown": INTENT_UNKNOWN,
        "contact_team": INTENT_CONTACT_TEAM,
    }
    return intent_mapping.get(intent_name, INTENT_UNKNOWN) 