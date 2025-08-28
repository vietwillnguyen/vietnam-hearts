# routers.py
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from functools import lru_cache
import asyncio
import functools
from app.services.bot_service import BotService
from app.services.supabase_auth import get_current_admin_user
from app.database import get_db
from app.utils.logging_config import get_api_logger
from app.config import *  # import all config variables

logger = get_api_logger()

# ---- Models ----

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    user_context: Optional[Dict[str, Any]] = None

class ChatResponse(BaseModel):
    response: str
    context_used: int
    confidence: float  # numeric is nicer for monitoring
    sources: List[str]
    note: Optional[str] = None

class SyncDocumentRequest(BaseModel):
    doc_id: str
    metadata: Optional[Dict[str, Any]] = None

class SyncDocumentResponse(BaseModel):
    status: str
    message: str
    doc_id: str
    chunks: int
    embeddings: int
    document_name: Optional[str] = None

class KnowledgeStatusResponse(BaseModel):
    knowledge_service_available: bool
    embeddings_available: bool
    gemini_available: bool
    supabase_available: bool
    document_service_available: bool
    documents_count: int
    documents: List[Dict[str, Any]]

# ---- Dependencies / Guards ----

def timeout_handler(timeout_seconds: float = 30.0):
    """Decorator to add timeout protection to async functions"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                logger.error(f"Function {func.__name__} timed out after {timeout_seconds} seconds")
                raise HTTPException(
                    status_code=504,
                    detail=f"Operation timed out after {timeout_seconds} seconds"
                )
        return wrapper
    return decorator

@lru_cache
def get_bot_service() -> BotService:
    try:
        from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        from supabase import create_client
        supabase_client = None
        if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
            supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            logger.info("Bot service initialized with Supabase client")
        else:
            logger.warning("Supabase credentials missing; using memory storage")
        return BotService(supabase_client)
    except Exception as e:
        logger.error(f"Supabase init failed: {e}")
        return BotService(None)

# ---- Routers ----

# Public chat router - no authentication required
public_bot_router = APIRouter(
    prefix="/bot", 
    tags=["bot"]
)

# Admin bot router - requires admin authentication
admin_router = APIRouter(
    prefix="/admin/bot", 
    tags=["bot", "admin"],
    dependencies=[Depends(get_current_admin_user)]  # Apply admin auth to all admin bot endpoints
)

# Public chat endpoint - no authentication required
@public_bot_router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True
)
@timeout_handler(timeout_seconds=30.0)
async def chat(
    request: ChatRequest, 
    bot_service: BotService = Depends(get_bot_service)
):
    try:
        # Log interaction for monitoring (without user info)
        logger.info(f"Public chat request: {request.message[:50]}...")
        
        result = await bot_service.chat(request.message, request.user_context)
        
        # Log successful response
        logger.info(f"Public chat response generated with confidence {result.get('confidence', 0)}")
        
        return ChatResponse(
            response=result["response"],
            context_used=result["context_used"],
            confidence=float(result["confidence"]),
            sources=result["sources"],
            note=result.get("note"),
        )
    except Exception as e:
        logger.error(f"Public chat error: {e}")
        raise HTTPException(status_code=500, detail="Chat processing failed")

# Public test endpoint - no authentication required
@public_bot_router.post("/test", response_model=Dict[str, Any])
@timeout_handler(timeout_seconds=30.0)
async def test_bot(
    request: ChatRequest, 
    bot_service: BotService = Depends(get_bot_service)
):
    logger.info("Public test chat request")
    
    result = await bot_service.chat(request.message, request.user_context)
    return {
        "status": "success",
        "test_message": request.message,
        "response": result["response"],
        "context_used": result["context_used"],
        "confidence": result["confidence"],
        "note": "Test response (public endpoint)",
        "timestamp": asyncio.get_event_loop().time()
    }

# Admin endpoints (protected by admin auth)
@admin_router.post("/knowledge-sync", response_model=SyncDocumentResponse, response_model_exclude_none=True)
@timeout_handler(timeout_seconds=60.0)  # Longer timeout for document sync
async def sync_documents(
    request: SyncDocumentRequest,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user),
    bot_service: BotService = Depends(get_bot_service),
    db = Depends(get_db),
):
    try:
        logger.info(f"Admin {current_admin.get('email', 'unknown')} syncing document {request.doc_id}")
        
        result = await bot_service.sync_documents(request.doc_id, request.metadata)
        document_name = None
        
        try:
            document_service = getattr(bot_service, "document_service", None)
            if document_service and getattr(document_service, "drive_service", None):
                file = document_service.drive_service.files().get(fileId=request.doc_id, fields="name").execute()
                document_name = file.get("name")
        except Exception as e:
            logger.warning(f"Doc name fetch failed: {e}")

        if result["status"] != "success":
            raise HTTPException(status_code=400, detail=result["message"])

        logger.info(f"Admin {current_admin.get('email', 'unknown')} successfully synced document {request.doc_id}")
        
        return SyncDocumentResponse(
            status="success",
            message=result["message"],
            doc_id=result["doc_id"],
            chunks=result["chunks"],
            embeddings=result["embeddings"],
            document_name=document_name,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document sync error for admin {current_admin.get('email', 'unknown')}: {e}")
        raise HTTPException(status_code=500, detail="Document sync failed")

@admin_router.get("/knowledge-sync/status", response_model=KnowledgeStatusResponse)
@timeout_handler(timeout_seconds=30.0)
async def get_knowledge_status(
    current_admin: Dict[str, Any] = Depends(get_current_admin_user),
    bot_service: BotService = Depends(get_bot_service),
):
    try:
        logger.info(f"Admin {current_admin.get('email', 'unknown')} checking knowledge status")
        
        status = await bot_service.get_knowledge_status()
        
        logger.info(f"Knowledge status retrieved for admin {current_admin.get('email', 'unknown')}: {status.get('documents_count', 0)} documents")
        
        return KnowledgeStatusResponse(**status)
    except Exception as e:
        logger.error(f"Failed to get knowledge status for admin {current_admin.get('email', 'unknown')}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get knowledge status")

@admin_router.get("/documents")
@timeout_handler(timeout_seconds=30.0)
async def list_available_documents(
    folder_id: Optional[str] = None,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user),
    bot_service: BotService = Depends(get_bot_service),
):
    try:
        logger.info(f"Admin {current_admin.get('email', 'unknown')} listing documents (folder: {folder_id})")
        
        docs = await bot_service.list_available_docs(folder_id)
        
        logger.info(f"Admin {current_admin.get('email', 'unknown')} retrieved {len(docs)} documents")
        
        return {
            "status": "success", 
            "documents": docs, 
            "count": len(docs),
            "requested_by": current_admin.get('email', 'unknown')
        }
    except Exception as e:
        logger.error(f"Failed to list documents for admin {current_admin.get('email', 'unknown')}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list documents")

@admin_router.get("/knowledge-base/chunks")
@timeout_handler(timeout_seconds=30.0)
async def inspect_knowledge_base_chunks(
    limit: Optional[int] = 10,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user),
    bot_service: BotService = Depends(get_bot_service),
):
    """
    Inspect stored chunks in the knowledge base for debugging purposes
    """
    try:
        logger.info(f"Admin {current_admin.get('email', 'unknown')} inspecting knowledge base chunks (limit: {limit})")
        
        # Get the knowledge service from bot service
        knowledge_service = bot_service.knowledge_service
        
        if not knowledge_service.supabase:
            raise HTTPException(status_code=503, detail="Knowledge base not available")
        
        # Query the document_chunks table directly
        result = knowledge_service.supabase.table('document_chunks').select('*').limit(limit or 10).execute()
        
        chunks = result.data if result.data else []
        
        # Format chunks for display (remove embeddings to reduce response size)
        formatted_chunks = []
        for chunk in chunks:
            formatted_chunk = {
                'id': chunk.get('id'),
                'chunk_index': chunk.get('chunk_index'),
                'source_document_id': chunk.get('source_document_id'),
                'content_preview': chunk.get('content', '')[:200] + '...' if len(chunk.get('content', '')) > 200 else chunk.get('content', ''),
                'content_length': len(chunk.get('content', '')),
                'metadata': chunk.get('metadata', {}),
                'created_at': chunk.get('created_at')
            }
            formatted_chunks.append(formatted_chunk)
        
        logger.info(f"Admin {current_admin.get('email', 'unknown')} retrieved {len(formatted_chunks)} chunks from knowledge base")
        
        return {
            "status": "success",
            "chunks": formatted_chunks,
            "total_chunks": len(formatted_chunks),
            "requested_by": current_admin.get('email', 'unknown'),
            "note": "This endpoint shows stored chunks for debugging. Content is truncated to 200 characters."
        }
        
    except Exception as e:
        logger.error(f"Failed to inspect knowledge base chunks for admin {current_admin.get('email', 'unknown')}: {e}")
        raise HTTPException(status_code=500, detail="Failed to inspect knowledge base chunks")

@admin_router.post("/knowledge-base/test-search")
@timeout_handler(timeout_seconds=30.0)
async def test_knowledge_base_search(
    request: ChatRequest,
    limit: Optional[int] = 3,
    threshold: Optional[float] = 0.3,
    current_admin: Dict[str, Any] = Depends(get_current_admin_user),
    bot_service: BotService = Depends(get_bot_service),
):
    """
    Test the knowledge base search functionality with a specific query
    """
    try:
        query = request.message
        logger.info(f"Admin {current_admin.get('email', 'unknown')} testing knowledge base search: '{query}'")
        
        # Get the knowledge service from bot service
        knowledge_service = bot_service.knowledge_service
        
        if not knowledge_service.supabase:
            raise HTTPException(status_code=503, detail="Knowledge base not available")
        
        # Perform similarity search
        relevant_chunks = await knowledge_service.similarity_search(query, limit=limit, threshold=threshold)
        
        # Format results for display
        formatted_results = []
        for chunk in relevant_chunks:
            formatted_chunk = {
                'id': chunk.get('id'),
                'chunk_index': chunk.get('chunk_index'),
                'source_document_id': chunk.get('source_document_id'),
                'similarity': chunk.get('similarity'),
                'content_preview': chunk.get('content', '')[:300] + '...' if len(chunk.get('content', '')) > 300 else chunk.get('content', ''),
                'content_length': len(chunk.get('content', '')),
                'metadata': chunk.get('metadata', {})
            }
            formatted_results.append(formatted_chunk)
        
        logger.info(f"Admin {current_admin.get('email', 'unknown')} search test found {len(formatted_results)} relevant chunks")
        
        return {
            "status": "success",
            "query": query,
            "search_parameters": {
                "limit": limit,
                "threshold": threshold
            },
            "results": formatted_results,
            "total_results": len(formatted_results),
            "requested_by": current_admin.get('email', 'unknown'),
            "note": "This endpoint tests the similarity search. Content is truncated to 300 characters."
        }
        
    except Exception as e:
        logger.error(f"Failed to test knowledge base search for admin {current_admin.get('email', 'unknown')}: {e}")
        raise HTTPException(status_code=500, detail="Failed to test knowledge base search")