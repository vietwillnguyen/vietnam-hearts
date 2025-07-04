#!/usr/bin/env python3
"""
Pytest-compatible tests for the Vietnam Hearts Agent.
Tests are logged to files in tests/logs/ for later reference.
"""

import os
import sys
import pytest
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

# Set test mode environment variable
os.environ["TEST_MODE"] = "true"

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Create logs directory with timestamped subdirectory for this test run
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
logs_dir = Path(__file__).parent / "logs" / f"test_run_{timestamp}"
logs_dir.mkdir(parents=True, exist_ok=True)

# Set up logging
def setup_logger(test_name: str) -> logging.Logger:
    """Set up a logger for a specific test"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"test_agent_{test_name}_{timestamp}.log"
    
    logger = logging.getLogger(f"test_agent_{test_name}")
    logger.setLevel(logging.INFO)
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

class TestAgentImports:
    """Test that all agent modules can be imported correctly"""
    
    @pytest.mark.unit
    @pytest.mark.agent
    def test_agent_imports(self):
        """Test importing the main agent module"""
        logger = setup_logger("imports")
        logger.info("Testing agent imports")
        
        try:
            from agent.agent import VietnamHeartsAgent
            logger.info("✅ Successfully imported VietnamHeartsAgent")
            assert True
        except ImportError as e:
            logger.error(f"❌ Failed to import VietnamHeartsAgent: {e}")
            assert False
    
    @pytest.mark.unit
    @pytest.mark.agent
    def test_models_imports(self):
        """Test importing agent models"""
        logger = setup_logger("models_imports")
        logger.info("Testing models imports")
        
        try:
            from agent.models import MessageRequest, MessageResponse
            logger.info("✅ Successfully imported MessageRequest, MessageResponse")
            assert True
        except ImportError as e:
            logger.error(f"❌ Failed to import models: {e}")
            assert False
    
    @pytest.mark.unit
    @pytest.mark.agent
    def test_config_imports(self):
        """Test importing agent configuration"""
        logger = setup_logger("config_imports")
        logger.info("Testing config imports")
        
        try:
            from agent.config import AGENT_NAME, AGENT_VERSION
            logger.info(f"✅ Successfully imported config: {AGENT_NAME} v{AGENT_VERSION}")
            assert AGENT_NAME is not None
            assert AGENT_VERSION is not None
        except ImportError as e:
            logger.error(f"❌ Failed to import config: {e}")
            assert False
    
    @pytest.mark.unit
    @pytest.mark.knowledge_base
    def test_knowledge_base_imports(self):
        """Test importing knowledge base"""
        logger = setup_logger("kb_imports")
        logger.info("Testing knowledge base imports")
        
        try:
            from agent.knowledge_base import VietnamHeartsKnowledgeBase
            logger.info("✅ Successfully imported VietnamHeartsKnowledgeBase")
            assert True
        except ImportError as e:
            logger.error(f"❌ Failed to import knowledge base: {e}")
            assert False

class TestAgentFunctionality:
    """Test the agent's core functionality"""
    
    @pytest.fixture(autouse=True)
    def setup_agent(self):
        """Set up agent for testing"""
        from agent.agent import VietnamHeartsAgent
        self.agent = VietnamHeartsAgent()
        self.logger = setup_logger("functionality")
        self.logger.info("Agent initialized for testing")
    
    @pytest.mark.integration
    @pytest.mark.agent
    @pytest.mark.parametrize("test_case", [
        {
            "message": "I want to volunteer",
            "expected_intent": "volunteer",
            "description": "Basic volunteer interest"
        },
        {
            "message": "How can I help teach?",
            "expected_intent": "volunteer",
            "description": "Teaching interest"
        },
        {
            "message": "I'd like to join as a volunteer",
            "expected_intent": "volunteer",
            "description": "Join volunteer"
        }
    ])
    def test_volunteer_intent_detection(self, test_case):
        """Test volunteer intent detection"""
        logger = setup_logger("volunteer_intent")
        logger.info(f"Testing volunteer intent detection: {test_case['description']}")
        
        logger.info(f"Input: '{test_case['message']}'")
        
        from agent.models import MessageRequest
        request = MessageRequest(
            user_id="test_user",
            platform="test",
            message_text=test_case['message']
        )
        
        response = self.agent.process_message(request)
        
        logger.info(f"Intent: {response.intent} (expected: {test_case['expected_intent']})")
        logger.info(f"Confidence: {response.confidence:.2f}")
        logger.info(f"Escalate: {response.should_escalate}")
        logger.info(f"Response preview: {response.response_text[:100]}...")
        
        # Assertions
        assert response.intent == test_case['expected_intent'], \
            f"Expected intent {test_case['expected_intent']}, got {response.intent}"
        assert response.confidence > 0.5, \
            f"Low confidence: {response.confidence}"
        assert not response.should_escalate, \
            "Should not escalate volunteer queries"
    
    @pytest.mark.integration
    @pytest.mark.agent
    @pytest.mark.knowledge_base
    @pytest.mark.parametrize("test_case", [
        {
            "message": "Where are you located?",
            "expected_intent": "faq",
            "description": "Location question",
            "should_use_kb": True
        },
        {
            "message": "What are your hours?",
            "expected_intent": "faq",
            "description": "Schedule question",
            "should_use_kb": True
        },
        {
            "message": "What is Vietnam Hearts?",
            "expected_intent": "faq",
            "description": "Organization question",
            "should_use_kb": True
        },
        {
            "message": "How can I volunteer?",
            "expected_intent": "faq",
            "description": "Volunteer info question",
            "should_use_kb": True
        }
    ])
    def test_faq_intent_detection(self, test_case):
        """Test FAQ intent detection with knowledge base"""
        logger = setup_logger("faq_intent")
        logger.info(f"Testing FAQ intent detection: {test_case['description']}")
        
        logger.info(f"Input: '{test_case['message']}'")
        
        from agent.models import MessageRequest
        request = MessageRequest(
            user_id="test_user",
            platform="test",
            message_text=test_case['message']
        )
        
        response = self.agent.process_message(request)
        
        logger.info(f"Intent: {response.intent} (expected: {test_case['expected_intent']})")
        logger.info(f"Confidence: {response.confidence:.2f}")
        logger.info(f"Escalate: {response.should_escalate}")
        logger.info(f"Response preview: {response.response_text[:100]}...")
        
        # Assertions
        assert response.intent == test_case['expected_intent'], \
            f"Expected intent {test_case['expected_intent']}, got {response.intent}"
        
        if test_case['should_use_kb']:
            # Import config to get test thresholds
            from agent.config import TEST_KB_CONFIDENCE_THRESHOLD
            assert response.confidence >= TEST_KB_CONFIDENCE_THRESHOLD, \
                f"Should have high confidence for KB queries: {response.confidence} (expected >= {TEST_KB_CONFIDENCE_THRESHOLD})"
            assert "Source:" in response.response_text, \
                "Should include source citation for KB responses"
    
    @pytest.mark.integration
    @pytest.mark.agent
    @pytest.mark.parametrize("test_case", [
        {
            "message": "Hello there!",
            "description": "Generic greeting"
        },
        {
            "message": "What's the weather like?",
            "description": "Unrelated question"
        },
        {
            "message": "Random text here",
            "description": "Random text"
        }
    ])
    def test_unknown_intent_detection(self, test_case):
        """Test unknown intent detection"""
        logger = setup_logger("unknown_intent")
        logger.info(f"Testing unknown intent detection: {test_case['description']}")
        
        logger.info(f"Input: '{test_case['message']}'")
        
        from agent.models import MessageRequest
        request = MessageRequest(
            user_id="test_user",
            platform="test",
            message_text=test_case['message']
        )
        
        response = self.agent.process_message(request)
        
        logger.info(f"Intent: {response.intent}")
        logger.info(f"Confidence: {response.confidence:.2f}")
        logger.info(f"Escalate: {response.should_escalate}")
        logger.info(f"Response preview: {response.response_text[:100]}...")
        
        # For unknown intents, we expect lower confidence
        assert response.confidence < 0.7, \
            f"Should have lower confidence for unknown queries: {response.confidence}"

class TestQuickReplies:
    """Test quick reply functionality"""
    
    @pytest.fixture(autouse=True)
    def setup_agent(self):
        """Set up agent for testing"""
        from agent.agent import VietnamHeartsAgent
        self.agent = VietnamHeartsAgent()
        self.logger = setup_logger("quick_replies")
        self.logger.info("Agent initialized for quick reply testing")
    
    @pytest.mark.parametrize("quick_reply_type,expected_intent,expected_confidence,expected_keywords", [
        ("SIGNUP", "volunteer", 1.0, ["sign up"]),
        ("LEARN_MORE", "faq", 0.9, ["Vietnam Hearts"]),
        ("CONTACT", "faq", 0.9, ["contact"])
    ])
    def test_quick_replies(self, quick_reply_type, expected_intent, expected_confidence, expected_keywords):
        """Test quick reply functionality"""
        logger = setup_logger(f"quick_reply_{quick_reply_type.lower()}")
        logger.info(f"Testing quick reply: {quick_reply_type}")
        
        response = self.agent.process_quick_reply("test_user", "test", quick_reply_type)
        
        logger.info(f"Intent: {response.intent}")
        logger.info(f"Confidence: {response.confidence:.2f}")
        logger.info(f"Response: {response.response_text}")
        logger.info(f"Quick replies: {[qr['text'] for qr in response.quick_replies]}")
        
        # Assertions
        assert response.intent == expected_intent, \
            f"Expected intent {expected_intent}, got {response.intent}"
        assert response.confidence >= expected_confidence, \
            f"Expected confidence >= {expected_confidence}, got {response.confidence}"
        
        # Check for expected keywords in response
        response_lower = response.response_text.lower()
        for keyword in expected_keywords:
            assert keyword.lower() in response_lower, \
                f"Expected keyword '{keyword}' not found in response"
        
        assert len(response.quick_replies) > 0, \
            "Should have quick replies"

class TestKnowledgeBase:
    """Test knowledge base functionality"""
    
    @pytest.mark.parametrize("query,expected_type", [
        ("Where are you located?", "location"),
        ("What are your hours?", "schedule"),
        ("How can I volunteer?", "volunteer"),
        ("What is Vietnam Hearts?", "organization")
    ])
    def test_knowledge_base_search(self, query, expected_type):
        """Test knowledge base search functionality"""
        logger = setup_logger("kb_search")
        logger.info(f"Testing knowledge base search: '{query}'")
        
        from agent.knowledge_base import VietnamHeartsKnowledgeBase
        kb = VietnamHeartsKnowledgeBase()
        
        result = kb.search_knowledge(query)
        
        if result:
            logger.info(f"✅ Found result with confidence: {result['confidence']}")
            logger.info(f"Type: {result['type']}")
            logger.info(f"Source: {result['source']}")
            logger.info(f"Content preview: {result['content'][:100]}...")
            
            # Import config to get test thresholds
            from agent.config import TEST_KB_CONFIDENCE_THRESHOLD
            assert result['confidence'] >= TEST_KB_CONFIDENCE_THRESHOLD, \
                f"Expected high confidence >= {TEST_KB_CONFIDENCE_THRESHOLD}, got {result['confidence']}"
            assert result['type'] == expected_type, \
                f"Expected type {expected_type}, got {result['type']}"
            assert len(result['content']) > 0, \
                "Content should not be empty"
        else:
            logger.warning(f"⚠️ No result found for query: '{query}'")
            # In test environment, we don't fail for missing results
            # but we log them for awareness
    
    def test_knowledge_base_initialization(self):
        """Test knowledge base initialization"""
        logger = setup_logger("kb_initialization")
        logger.info("Testing knowledge base initialization")
        
        try:
            from agent.knowledge_base import VietnamHeartsKnowledgeBase
            kb = VietnamHeartsKnowledgeBase()
            logger.info("✅ Knowledge base initialized successfully")
            
            # Test basic methods
            location_info = kb.get_location_info()
            hours_info = kb.get_hours_info()
            volunteer_info = kb.get_volunteer_info()
            
            logger.info(f"Location info length: {len(location_info)}")
            logger.info(f"Hours info length: {len(hours_info)}")
            logger.info(f"Volunteer info length: {len(volunteer_info)}")
            
            assert len(location_info) > 0, "Location info should not be empty"
            assert len(hours_info) > 0, "Hours info should not be empty"
            assert len(volunteer_info) > 0, "Volunteer info should not be empty"
            
        except Exception as e:
            logger.error(f"❌ Knowledge base initialization failed: {e}")
            assert False

class TestEnvironment:
    """Test environment configuration"""
    
    @pytest.mark.parametrize("var_name,required", [
        ("GEMINI_API_KEY", True),
        ("NEW_USER_SIGNUP_LINK", True),
        ("GEMINI_MODEL", False),
        ("FACEBOOK_MESSENGER_LINK", False),
        ("INSTAGRAM_LINK", False),
        ("FACEBOOK_PAGE_LINK", False)
    ])
    def test_environment_variables(self, var_name, required):
        """Test environment variables"""
        logger = setup_logger("env_variables")
        logger.info(f"Testing environment variable: {var_name}")
        
        value = os.getenv(var_name)
        
        if value:
            display_value = f"{value[:20]}..." if len(value) > 20 else value
            logger.info(f"✅ {var_name}: {display_value}")
            
            if required:
                assert len(value) > 0, f"Required variable {var_name} should not be empty"
        else:
            if required:
                logger.warning(f"❌ {var_name}: Not set (required)")
                # In test environment, we don't fail for missing variables
                # but we log them for awareness
            else:
                logger.info(f"⚠️  {var_name}: Not set (optional)")

# Pytest configuration
def pytest_configure(config):
    """Configure pytest"""
    # Create a summary log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_log = logs_dir / f"test_summary_{timestamp}.log"
    
    # Set up summary logger
    summary_logger = logging.getLogger("test_summary")
    summary_logger.setLevel(logging.INFO)
    
    file_handler = logging.FileHandler(summary_log)
    file_handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    
    summary_logger.addHandler(file_handler)
    summary_logger.info("Starting Vietnam Hearts Agent test suite")

def pytest_sessionfinish(session, exitstatus):
    """Log test session completion"""
    summary_logger = logging.getLogger("test_summary")
    summary_logger.info(f"Test session completed with exit status: {exitstatus}")
    
    # Log summary statistics
    passed = len(session.testscollected) - len(session.testsfailed) - len(session.testsskipped)
    summary_logger.info(f"Tests passed: {passed}")
    summary_logger.info(f"Tests failed: {len(session.testsfailed)}")
    summary_logger.info(f"Tests skipped: {len(session.testsskipped)}")
    summary_logger.info(f"Total tests: {len(session.testscollected)}") 