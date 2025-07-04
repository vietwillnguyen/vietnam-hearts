"""
Database models for the Vietnam Hearts Agent.
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, JSON, Text, Float
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime, timezone
from pydantic import BaseModel


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    """Tracks conversations with users across different platforms"""
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True, nullable=False)
    platform = Column(String, nullable=False)
    user_name = Column(String, nullable=True)
    started_at = Column(DateTime, default=datetime.now(timezone.utc))
    last_message_at = Column(DateTime, default=datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)
    current_intent = Column(String, nullable=True)
    conversation_context = Column(JSON, nullable=True)
    
    messages = relationship("Message", back_populates="conversation")
    
    class Config:
        from_attributes = True


class Message(Base):
    """Individual messages in conversations"""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    message_text = Column(Text, nullable=False)
    message_type = Column(String, nullable=False)
    detected_intent = Column(String, nullable=True)
    intent_confidence = Column(Float, nullable=True)
    ai_response = Column(Text, nullable=True)
    ai_processing_time = Column(Float, nullable=True)
    platform_message_id = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.now(timezone.utc))
    processing_error = Column(String, nullable=True)
    is_escalated = Column(Boolean, default=False)
    
    conversation = relationship("Conversation", back_populates="messages")
    
    class Config:
        from_attributes = True


class IntentLog(Base):
    """
    Detailed logging of intent detection for analysis and improvement
    """
    __tablename__ = "intent_logs"

    id = Column(Integer, primary_key=True, index=True)
    
    # Message reference
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    
    # Intent detection details
    detected_intent = Column(String, nullable=False)
    confidence_score = Column(Float, nullable=False)
    detection_method = Column(String, nullable=False)  # "keyword", "ai", "hybrid"
    
    # AI analysis details
    ai_analysis = Column(JSON, nullable=True)  # Full AI response for analysis
    keywords_found = Column(JSON, nullable=True)
    
    # Timestamp
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    
    class Config:
        from_attributes = True


class EscalationLog(Base):
    """
    Tracks when messages are escalated to human team
    """
    __tablename__ = "escalation_logs"

    id = Column(Integer, primary_key=True, index=True)
    
    # Escalation details
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    
    # Escalation reason
    reason = Column(String, nullable=False)  # "unknown_intent", "complex_question", "error"
    escalation_method = Column(String, nullable=False)  # "email", "slack", "dashboard"
    
    # Team notification
    notified_at = Column(DateTime, default=datetime.now(timezone.utc))
    notification_sent = Column(Boolean, default=False)
    notification_error = Column(String, nullable=True)
    
    # Resolution
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)
    resolution_notes = Column(Text, nullable=True)
    
    class Config:
        from_attributes = True


# Pydantic models for API
class MessageRequest(BaseModel):
    user_id: str
    platform: str
    message_text: str
    user_name: str = None
    platform_message_id: str = None


class MessageResponse(BaseModel):
    response_text: str
    intent: str
    confidence: float
    quick_replies: list = []
    should_escalate: bool = False


class ConversationSummary(BaseModel):
    conversation_id: int
    user_id: str
    platform: str
    message_count: int
    started_at: datetime
    last_message_at: datetime
    current_intent: str = None 