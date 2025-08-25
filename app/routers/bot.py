"""
Bot API endpoints for Vietnam Hearts chatbot

Provides bot functionality including:
- Public chatbot interactions (/admin/bot/chat)
- Admin-only knowledge base management (/admin/bot/*)
- Bot service testing and monitoring

The chat endpoint is public for external integrations, while knowledge base management requires admin privileges.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.bot_service import BotService
from app.services.supabase_auth import get_current_admin_user
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

# Bot router - Bot-specific admin functions for knowledge base and chatbot management
bot_router = APIRouter(prefix="/admin/bot", tags=["bot", "admin"])

# Request/Response models
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="User's message")
    user_context: Optional[Dict[str, Any]] = Field(None, description="Optional user context")

class ChatResponse(BaseModel):
    response: str = Field(..., description="Bot's response")
    context_used: int = Field(..., description="Number of context chunks used")
    confidence: str = Field(..., description="Confidence level of response")
    sources: List[str] = Field(..., description="Source documents used")
    note: Optional[str] = Field(None, description="Additional notes")

class SyncDocumentRequest(BaseModel):
    doc_id: str = Field(..., description="Google Doc ID to sync")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")

class SyncDocumentResponse(BaseModel):
    status: str = Field(..., description="Sync status")
    message: str = Field(..., description="Sync result message")
    doc_id: str = Field(..., description="Document ID")
    chunks: int = Field(..., description="Number of chunks created")
    embeddings: int = Field(..., description="Number of embeddings created")

class KnowledgeStatusResponse(BaseModel):
    knowledge_service_available: bool
    embeddings_available: bool
    gemini_available: bool
    supabase_available: bool
    document_service_available: bool
    documents_count: int
    documents: List[Dict[str, Any]]

# Initialize bot service
def get_bot_service() -> BotService:
    """Get bot service instance with Supabase client"""
    try:
        from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        from supabase import create_client
        
        if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
            supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            logger.info("Bot service initialized with Supabase client")
        else:
            supabase_client = None
            logger.warning("Supabase credentials not configured - bot service will use memory storage")
        
        return BotService(supabase_client)
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client for bot service: {e}")
        return BotService(None)

# Public chat endpoint - no authentication required
@bot_router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    bot_service: BotService = Depends(get_bot_service)
):
    """
    Chat with the Vietnam Hearts bot
    
    Send a message and get an intelligent response based on the knowledge base.
    This is a public endpoint for chatbot interactions.
    """
    try:
        logger.info(f"Chat request received from public user")
        
        # Process the chat message
        result = await bot_service.chat(request.message, request.user_context)
        
        # Convert to response model
        response = ChatResponse(
            response=result["response"],
            context_used=result["context_used"],
            confidence=result["confidence"],
            sources=result["sources"],
            note=result.get("note")
        )
        
        logger.info(f"Chat response generated with {result['context_used']} context chunks")
        return response
        
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {str(e)}")

@bot_router.post("/knowledge-sync", response_model=SyncDocumentResponse)
async def sync_documents(
    request: SyncDocumentRequest,
    bot_service: BotService = Depends(get_bot_service),
    current_user: Dict[str, Any] = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Admin endpoint to sync Google Docs to knowledge base

    Requires admin privileges to sync documents and update the knowledge base.
    Also attempts to fetch the document name from Google Drive using the document ID.
    """
    try:
        logger.info(f"Document sync request from: {current_user.get('email')} for doc: {request.doc_id}")

        # Sync the document
        result = await bot_service.sync_documents(request.doc_id, request.metadata)

        document_name: str = ""
        # Try to fetch the document name from Google Drive if possible
        try:
            # Access the document_service from bot_service
            document_service = getattr(bot_service, "document_service", None)
            if document_service and hasattr(document_service, "drive_service") and document_service.drive_service:
                drive_service = document_service.drive_service
                file = drive_service.files().get(fileId=request.doc_id, fields="name").execute()
                document_name = file.get("name", "")
                logger.info(f"Fetched document name for {request.doc_id}: {document_name}")
            else:
                logger.warning("Document service or drive service not available, cannot fetch document name.")
        except Exception as doc_name_exc:
            logger.error(f"Failed to fetch document name for {request.doc_id}: {doc_name_exc}")

        if result["status"] == "success":
            response = SyncDocumentResponse(
                status="success",
                message=result["message"],
                doc_id=result["doc_id"],
                chunks=result["chunks"],
                embeddings=result["embeddings"],
                document_name=document_name if document_name else None
            )
            logger.info(f"Document {request.doc_id} synced successfully (name: {document_name})")
            return response
        else:
            raise HTTPException(status_code=400, detail=result["message"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document sync error: {e}")
        raise HTTPException(status_code=500, detail=f"Document sync failed: {str(e)}")

@bot_router.get("/knowledge-sync/status", response_model=KnowledgeStatusResponse)
async def get_knowledge_status(
    bot_service: BotService = Depends(get_bot_service),
    current_user: Dict[str, Any] = Depends(get_current_admin_user)
):
    """
    Get knowledge base status and document information
    
    Admin endpoint to check the health and status of the knowledge base.
    """
    try:
        logger.info(f"Knowledge status request from admin: {current_user.get('email')}")
        
        status = await bot_service.get_knowledge_status()
        
        response = KnowledgeStatusResponse(
            knowledge_service_available=status["knowledge_service_available"],
            embeddings_available=status["embeddings_available"],
            gemini_available=status["gemini_available"],
            supabase_available=status["supabase_available"],
            document_service_available=status["document_service_available"],
            documents_count=status["documents_count"],
            documents=status["documents"]
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Knowledge status error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get knowledge status: {str(e)}")

@bot_router.get("/documents")
async def list_available_documents(
    folder_id: Optional[str] = None,
    bot_service: BotService = Depends(get_bot_service),
    current_user: Dict[str, Any] = Depends(get_current_admin_user)
):
    """
    List available Google Docs for syncing
    
    Admin endpoint to see what documents are available to sync.
    """
    try:
        logger.info(f"Document list request from admin: {current_user.get('email')}")
        
        documents = await bot_service.list_available_docs(folder_id)
        
        return {
            "status": "success",
            "documents": documents,
            "count": len(documents)
        }
        
    except Exception as e:
        logger.error(f"Document list error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")

# Bot health check removed - consolidated into main admin health endpoint at /admin/health

# Test endpoint for development
@bot_router.post("/test")
async def test_bot(
    request: ChatRequest,
    bot_service: BotService = Depends(get_bot_service)
):
    """
    Test endpoint for bot functionality (development only)
    
    This endpoint doesn't require authentication for testing purposes.
    """
    try:
        logger.info("Bot test request received")
        
        # Process the test message
        result = await bot_service.chat(request.message, request.user_context)
        
        return {
            "status": "success",
            "test_message": request.message,
            "response": result["response"],
            "context_used": result["context_used"],
            "confidence": result["confidence"],
            "note": "This is a test response - no authentication required"
        }
        
    except Exception as e:
        logger.error(f"Bot test error: {e}")
        return {
            "status": "error",
            "error": str(e)
        }
