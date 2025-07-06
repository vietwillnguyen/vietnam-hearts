#!/usr/bin/env python3
"""
Test script for the simplified Vietnam Hearts Agent.
Demonstrates the single LLM call approach with context only.
"""

import os
import sys
from pathlib import Path

# Add the agent directory to Python path
agent_dir = Path(__file__).parent
sys.path.insert(0, str(agent_dir))

from .agent import VietnamHeartsAgent
from .models import MessageRequest, MessageResponse


def test_simplified_agent():
    """Test the simplified agent with sample messages"""
    
    # Initialize agent (no database session for testing)
    agent = VietnamHeartsAgent()
    
    # Test messages
    test_messages = [
        "Hello! I'm interested in volunteering with Vietnam Hearts",
        "Where are your classes located?",
        "What time are the classes?",
        "Tell me more about your organization",
        "I want to help teach English to children",
        "How can I sign up to volunteer?",
        "Xin chào! Tôi muốn tình nguyện với Vietnam Hearts",
        "I have a complaint about the service",
        "I need to speak to someone directly about an urgent matter",
        "How can I donate to support Vietnam Hearts?",
        "I want to make a financial contribution",
        "What accommodations do you provide for teachers?",
        "Tôi có khiếu nại về dịch vụ",
        "Tôi cần nói chuyện với thành viên nhóm",
        "Có vấn đề khẩn cấp cần xử lý",
        "I want to talk to a real person",
        "Can you help me with a legal issue?",
        "I'm interested in collaboration opportunities"
    ]
    
    print("🤖 Vietnam Hearts Agent - Simplified Single LLM Call Test")
    print("=" * 60)
    
    for i, message in enumerate(test_messages, 1):
        print(f"\n📝 Test {i}: {message}")
        print("-" * 40)
        
        # Create message request
        request = MessageRequest(
            user_id=f"test_user_{i}",
            platform="messenger",
            user_name="Test User",
            message_text=message
        )
        
        try:
            # Process message
            response = agent.process_message(request)
            
            print(f"✅ Response: {response.response_text[:150]}...")
            print(f"📊 Intent: {response.intent}")
            print(f"🎯 Confidence: {response.confidence}")
            print(f"🚨 Escalation: {'Yes' if response.should_escalate else 'No'}")
            print(f"🔘 Quick Replies: {len(response.quick_replies)} (simplified)")
            
        except Exception as e:
            print(f"❌ Error: {e}")
    
    print("\n" + "=" * 60)
    print("✅ Test completed!")


def test_escalation_detection():
    """Test escalation detection with various message types"""
    
    agent = VietnamHeartsAgent()
    
    print("\n🚨 Testing Escalation Detection")
    print("=" * 40)
    
    escalation_test_messages = [
        "I have a serious complaint about your service",
        "I need to speak to a human immediately",
        "This is an emergency situation",
        "I want to contact the team directly",
        "I have a legal issue to discuss",
        "I want to talk to a team member",
        "Hello, how are you today?",
        "Tell me about your volunteer opportunities",
        "Where are your classes located?",
        "Tôi có khiếu nại nghiêm trọng về dịch vụ",
        "Tôi cần nói chuyện với người thật ngay lập tức",
        "Đây là tình huống khẩn cấp",
        "Tôi muốn liên hệ trực tiếp với nhóm",
        "Tôi có vấn đề pháp lý cần thảo luận",
        "Tôi muốn nói chuyện với thành viên nhóm",
        "Xin chào, hôm nay bạn thế nào?",
        "Kể cho tôi nghe về cơ hội tình nguyện",
        "I want to talk to a real person",
        "Can you help me with a legal issue?",
        "I'm interested in collaboration opportunities"
    ]
    
    for i, message in enumerate(escalation_test_messages, 1):
        print(f"\n📝 Test {i}: {message}")
        
        request = MessageRequest(
            user_id=f"escalation_test_{i}",
            platform="messenger",
            user_name="Test User",
            message_text=message
        )
        
        try:
            response = agent.process_message(request)
            escalation_status = "🚨 ESCALATE" if response.should_escalate else "✅ OK"
            print(f"{escalation_status} - {response.response_text[:80]}...")
            
        except Exception as e:
            print(f"❌ Error: {e}")


if __name__ == "__main__":
    # Set up environment for testing
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("NEW_USER_SIGNUP_LINK", "https://example.com/signup")
    
    test_simplified_agent()
    test_escalation_detection() 