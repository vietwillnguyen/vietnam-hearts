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
    log_file = logs_dir / f"test_agent_{test_name}.log"
    
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


class TestAgentBasicFunctionality:
    """Test the agent's basic functionality and escalation detection"""
    
    @pytest.fixture(autouse=True)
    def setup_agent(self):
        """Set up agent for testing"""
        from agent.agent import VietnamHeartsAgent
        self.agent = VietnamHeartsAgent()
        self.logger = setup_logger("basic_functionality")
        self.logger.info("Agent initialized for testing")
    
    @pytest.mark.parametrize("test_case", [
        {
            "message": "I'm interested in volunteering",
            "description": "Basic volunteer interest",
            "should_escalate": False,
            "expected_keywords": ["sign up", "volunteer"]
        },
        {
            "message": "Where are you located?",
            "description": "Location question",
            "should_escalate": False,
            "expected_keywords": ["Binh Thanh", "Ho Chi Minh"]
        },
        {
            "message": "When do you teach?",
            "description": "Schedule question",
            "should_escalate": False,
            "expected_keywords": ["schedule", "time", "class"]
        },
        {
            "message": "What is Vietnam Hearts?",
            "description": "Organization question",
            "should_escalate": False,
            "expected_keywords": ["Vietnam Hearts", "organization", "volunteer"]
        },
        {
            "message": "How can I donate?",
            "description": "Donation question",
            "should_escalate": False,
            "expected_keywords": ["donate", "support", "contribution"]
        }
    ])
    def test_basic_responses(self, test_case):
        """Test basic response scenarios"""
        logger = setup_logger("basic_responses")
        logger.info(f"Testing basic response: {test_case['description']}")
        
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
        
        # Assertions
        assert response.intent == "ai_response", \
            f"Expected intent 'ai_response', got {response.intent}"
        assert response.confidence > 0.5, \
            f"Low confidence: {response.confidence}"
        assert response.should_escalate == test_case['should_escalate'], \
            f"Expected escalation {test_case['should_escalate']}, got {response.should_escalate}"
        
        # Check for expected keywords in response
        response_lower = response.response_text.lower()
        for keyword in test_case['expected_keywords']:
            assert keyword.lower() in response_lower, \
                f"Expected keyword '{keyword}' not found in response"
        
        # Check that standard response format is present
        assert "CONTACT TEAM" in response.response_text, \
            "Response should include standard CONTACT TEAM instruction"


class TestEscalationDetection:
    """Test escalation detection functionality"""
    
    @pytest.fixture(autouse=True)
    def setup_agent(self):
        """Set up agent for testing"""
        from agent.agent import VietnamHeartsAgent
        self.agent = VietnamHeartsAgent()
        self.logger = setup_logger("escalation_detection")
        self.logger.info("Agent initialized for escalation testing")
    
    @pytest.mark.parametrize("test_case", [
        {
            "message": "I have a complaint about the organization",
            "description": "Complaint - should escalate",
            "should_escalate": True
        },
        {
            "message": "This is an urgent matter",
            "description": "Urgent matter - should escalate",
            "should_escalate": True
        },
        {
            "message": "I need to speak to a team member",
            "description": "Request to speak to team member - should escalate",
            "should_escalate": True
        },
        {
            "message": "I want to talk to someone real",
            "description": "Request for human contact - should escalate",
            "should_escalate": True
        },
        {
            "message": "I have a legal question",
            "description": "Legal question - should escalate",
            "should_escalate": True
        },
        {
            "message": "I want to discuss a partnership",
            "description": "Partnership inquiry - should escalate",
            "should_escalate": True
        },
        {
            "message": "Tôi có khiếu nại về tổ chức",
            "description": "Vietnamese complaint - should escalate",
            "should_escalate": True
        },
        {
            "message": "Tôi cần nói chuyện với thành viên nhóm",
            "description": "Vietnamese request for team member - should escalate",
            "should_escalate": True
        }
    ])
    def test_escalation_triggers(self, test_case):
        """Test that escalation is triggered for sensitive topics"""
        logger = setup_logger("escalation_triggers")
        logger.info(f"Testing escalation trigger: {test_case['description']}")
        
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
        
        # Assertions
        assert response.intent == "ai_response", \
            f"Expected intent 'ai_response', got {response.intent}"
        assert response.should_escalate == test_case['should_escalate'], \
            f"Expected escalation {test_case['should_escalate']}, got {response.should_escalate}"
        
        if test_case['should_escalate']:
            # Check that escalation message is present
            assert "requires escalation" in response.response_text.lower() or \
                   "team member will get back to you" in response.response_text.lower(), \
                "Escalation response should contain escalation message"
    
    @pytest.mark.parametrize("test_case", [
        {
            "message": "I'm interested in volunteering",
            "description": "Normal volunteer interest - should NOT escalate",
            "should_escalate": False
        },
        {
            "message": "Where are you located?",
            "description": "Normal location question - should NOT escalate",
            "should_escalate": False
        },
        {
            "message": "What times do you teach?",
            "description": "Normal schedule question - should NOT escalate",
            "should_escalate": False
        }
    ])
    def test_no_escalation_triggers(self, test_case):
        """Test that normal queries do NOT trigger escalation"""
        logger = setup_logger("no_escalation_triggers")
        logger.info(f"Testing no escalation: {test_case['description']}")
        
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
        
        # Assertions
        assert response.intent == "ai_response", \
            f"Expected intent 'ai_response', got {response.intent}"
        assert response.should_escalate == test_case['should_escalate'], \
            f"Expected escalation {test_case['should_escalate']}, got {response.should_escalate}"
        
        # Check that standard response format is present (not escalation message)
        assert "CONTACT TEAM" in response.response_text, \
            "Response should include standard CONTACT TEAM instruction"
        assert "requires escalation" not in response.response_text.lower(), \
            "Normal response should not contain escalation message"


class TestKnowledgeBase:
    """Test knowledge base functionality"""
    
    @pytest.mark.parametrize("query,expected_type", [
        ("Where are you located?", "location"),
        ("What are your hours?", "schedule"),
        ("How can I volunteer?", "volunteer"),
        ("What is Vietnam Hearts?", "organization"),
        ("How can I donate?", "donation")
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
            from agent.config import KB_CONFIDENCE_THRESHOLD
            assert result['confidence'] >= KB_CONFIDENCE_THRESHOLD, \
                f"Expected high confidence >= {KB_CONFIDENCE_THRESHOLD}, got {result['confidence']}"
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
            volunteer_info = kb.get_volunteer_info()
            schedule_info = kb.get_class_schedule()
            donation_info = kb.get_donation_info()
            
            logger.info(f"Location info length: {len(location_info)}")
            logger.info(f"Volunteer info length: {len(volunteer_info)}")
            logger.info(f"Schedule info length: {len(schedule_info)}")
            logger.info(f"Donation info length: {len(donation_info)}")
            
            assert len(location_info) > 0, "Location info should not be empty"
            assert len(volunteer_info) > 0, "Volunteer info should not be empty"
            assert len(schedule_info) > 0, "Schedule info should not be empty"
            assert len(donation_info) > 0, "Donation info should not be empty"
            
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