"""
FastAPI endpoints for the Vietnam Hearts Agent.
"""

import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .agent import VietnamHeartsAgent
from .models import MessageRequest, MessageResponse
from .config import AGENT_NAME, AGENT_VERSION

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/agent", tags=["agent"])

# Global agent instance (in production, use dependency injection)
_agent: Optional[VietnamHeartsAgent] = None


def get_agent() -> VietnamHeartsAgent:
    """Get or create agent instance"""
    global _agent
    if _agent is None:
        _agent = VietnamHeartsAgent()
    return _agent


class WebhookRequest(BaseModel):
    """Facebook Messenger/Instagram webhook request"""
    object: str
    entry: list


class MessagingEvent(BaseModel):
    """Individual messaging event"""
    sender: Dict[str, str]
    recipient: Dict[str, str]
    timestamp: int
    message: Optional[Dict[str, Any]] = None
    postback: Optional[Dict[str, Any]] = None


class Entry(BaseModel):
    """Webhook entry"""
    id: str
    time: int
    messaging: Optional[list[MessagingEvent]] = None


class AgentHealthResponse(BaseModel):
    """Health check response"""
    status: str
    agent_name: str
    version: str
    timestamp: str


@router.get("/health", response_model=AgentHealthResponse)
async def health_check():
    """Health check endpoint"""
    from datetime import datetime
    
    return AgentHealthResponse(
        status="healthy",
        agent_name=AGENT_NAME,
        version=AGENT_VERSION,
        timestamp=datetime.now().isoformat()
    )


@router.post("/webhook")
async def webhook_handler(request: Request, agent: VietnamHeartsAgent = Depends(get_agent)):
    """
    Handle webhook requests from Facebook Messenger/Instagram
    
    This endpoint receives webhook events and processes them through the agent.
    """
    try:
        # Parse the webhook request
        body = await request.json()
        logger.info(f"Received webhook: {body}")
        
        # Verify it's a page webhook
        if body.get("object") != "page":
            return JSONResponse(content={"status": "ok"})
        
        # Process each entry
        for entry in body.get("entry", []):
            await process_entry(entry, agent)
        
        return JSONResponse(content={"status": "ok"})
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/message", response_model=MessageResponse)
async def process_message(request: MessageRequest, agent: VietnamHeartsAgent = Depends(get_agent)):
    """
    Process a single message through the agent
    
    This endpoint can be used for testing or direct message processing.
    """
    try:
        response = agent.process_message(request)
        return response
        
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/quick-reply", response_model=MessageResponse)
async def process_quick_reply(
    user_id: str,
    platform: str,
    payload: str,
    agent: VietnamHeartsAgent = Depends(get_agent)
):
    """
    Process a quick reply button click
    
    Args:
        user_id: User ID
        platform: Platform (messenger, instagram)
        payload: Quick reply payload
    """
    try:
        response = agent.process_quick_reply(user_id, platform, payload)
        return response
        
    except Exception as e:
        logger.error(f"Error processing quick reply: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


async def process_entry(entry: Dict[str, Any], agent: VietnamHeartsAgent):
    """Process a single webhook entry"""
    try:
        # Extract messaging events
        messaging_events = entry.get("messaging", [])
        
        for event in messaging_events:
            await process_messaging_event(event, agent)
            
    except Exception as e:
        logger.error(f"Error processing entry: {e}")


async def process_messaging_event(event: Dict[str, Any], agent: VietnamHeartsAgent):
    """Process a single messaging event"""
    try:
        sender_id = event.get("sender", {}).get("id")
        recipient_id = event.get("recipient", {}).get("id")
        timestamp = event.get("timestamp")
        
        # Handle message events
        if "message" in event:
            message_data = event["message"]
            message_text = message_data.get("text", "")
            mid = message_data.get("mid")
            
            if message_text:
                # Create message request
                request = MessageRequest(
                    user_id=sender_id,
                    platform="messenger",  # Could be determined from webhook source
                    message_text=message_text,
                    platform_message_id=mid
                )
                
                # Process through agent
                response = agent.process_message(request)
                
                # TODO: Send response back to Facebook/Instagram
                # This would typically be done by calling the Facebook/Instagram API
                logger.info(f"Generated response: {response.response_text}")
                
        # Handle postback events (quick replies)
        elif "postback" in event:
            postback_data = event["postback"]
            payload = postback_data.get("payload", "")
            
            if payload:
                # Process quick reply
                response = agent.process_quick_reply(sender_id, "messenger", payload)
                
                # TODO: Send response back to Facebook/Instagram
                logger.info(f"Generated quick reply response: {response.response_text}")
                
    except Exception as e:
        logger.error(f"Error processing messaging event: {e}")


@router.post("/test")
async def test_agent(agent: VietnamHeartsAgent = Depends(get_agent)):
    """Test endpoint for development"""
    test_messages = [
        "I want to volunteer",
        "Where are you located?",
        "What is Vietnam Hearts?",
        "How can I help teach?",
        "What are your hours?",
        "I have a complaint about something"
    ]
    
    results = []
    for message in test_messages:
        request = MessageRequest(
            user_id="test_user",
            platform="test",
            message_text=message
        )
        
        response = agent.process_message(request)
        results.append({
            "input": message,
            "response": response.response_text,
            "intent": response.intent,
            "confidence": response.confidence,
            "should_escalate": response.should_escalate
        })
    
    return {"test_results": results} 