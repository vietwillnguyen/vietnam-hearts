"""
Main bot service for Vietnam Hearts chatbot

Orchestrates document processing, knowledge base queries, and generates intelligent responses.
"""

import logging
from typing import List, Dict, Any, Optional
from .knowledge_service import KnowledgeService
from .document_service import DocumentService
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

class BotService:
    """Main service for chatbot functionality"""
    
    def __init__(self, supabase_client=None):
        """
        Initialize bot service
        
        Args:
            supabase_client: Supabase client instance
        """
        self.knowledge_service = KnowledgeService(supabase_client)
        self.document_service = DocumentService()
        self.supabase = supabase_client
        logger.info("Bot service initialized")
    
    async def sync_documents(self, doc_id: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Sync Google Doc to knowledge base
        
        Args:
            doc_id: Google Doc ID to sync
            metadata: Additional metadata for the document
            
        Returns:
            Sync result information
        """
        try:
            logger.info(f"Starting document sync for: {doc_id}")
            
            # Validate document access
            if not self.document_service.validate_doc_id(doc_id):
                return {
                    "status": "error",
                    "message": f"Document {doc_id} not accessible",
                    "doc_id": doc_id
                }
            
            # Fetch document content
            try:
                content = await self.document_service.fetch_google_doc(doc_id)
                logger.info(f"Fetched document with {len(content)} characters")
            except Exception as e:
                logger.error(f"Failed to fetch document {doc_id}: {e}")
                return {
                    "status": "error",
                    "message": f"Failed to fetch document: {str(e)}",
                    "doc_id": doc_id
                }
            
            # Split into chunks
            try:
                chunks = self.document_service.split_into_chunks(content, chunk_size=1000, overlap=100)
                logger.info(f"Split document into {len(chunks)} chunks")
            except Exception as e:
                logger.error(f"Failed to split document into chunks: {e}")
                return {
                    "status": "error",
                    "message": f"Failed to split document: {str(e)}",
                    "doc_id": doc_id
                }
            
            if not chunks:
                return {
                    "status": "error",
                    "message": "No content chunks generated",
                    "doc_id": doc_id
                }
            
            # Create embeddings
            try:
                embeddings = await self.knowledge_service.create_embeddings(chunks)
                logger.info(f"Created {len(embeddings)} embeddings")
            except Exception as e:
                logger.error(f"Failed to create embeddings: {e}")
                return {
                    "status": "error",
                    "message": f"Failed to create embeddings: {str(e)}",
                    "doc_id": doc_id
                }
            
            # Store in knowledge base
            try:
                store_result = await self.knowledge_service.store_chunks(
                    chunks, embeddings, doc_id, metadata
                )
                logger.debug(f"Stored chunks: {store_result}")
            except Exception as e:
                logger.error(f"Failed to store chunks: {e}")
                return {
                    "status": "error",
                    "message": f"Failed to store chunks: {str(e)}",
                    "doc_id": doc_id
                }
            
            return {
                "status": "success",
                "message": f"Document {doc_id} synced successfully",
                "doc_id": doc_id,
                "chunks": len(chunks),
                "embeddings": len(embeddings),
                "store_result": store_result
            }
            
        except Exception as e:
            logger.error(f"Document sync failed: {e}")
            return {
                "status": "error",
                "message": f"Document sync failed: {str(e)}",
                "doc_id": doc_id
            }
    
    async def chat(self, message: str, user_context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process chat message and return intelligent response
        
        Args:
            message: User's message
            user_context: Optional user context (role, experience, etc.)
            
        Returns:
            Chat response with answer and metadata
        """
        try:
            logger.info(f"Processing chat message: {message[:100]}...")
            
            # Find relevant context from knowledge base
            relevant_chunks = await self.knowledge_service.similarity_search(message, limit=3)
            
            if not relevant_chunks:
                logger.info("No relevant context found, using fallback response")
                return await self._generate_fallback_response(message, user_context)
            
            # Build context from relevant chunks
            context = self._build_context(relevant_chunks)
            logger.info(f"Found {len(relevant_chunks)} relevant chunks")
            
            # Generate response using context
            response = await self._generate_contextual_response(message, context, user_context)
            
            return {
                "response": response,
                "context_used": len(relevant_chunks),
                "confidence": "high" if relevant_chunks else "low",
                "sources": [chunk.get('source_document_id') for chunk in relevant_chunks]
            }
            
        except Exception as e:
            logger.error(f"Chat processing failed: {e}")
            return await self._generate_fallback_response(message, user_context, error=str(e))
    
    def _build_context(self, chunks: List[Dict[str, Any]]) -> str:
        """
        Build context string from relevant chunks
        
        Args:
            chunks: List of relevant document chunks
            
        Returns:
            Formatted context string
        """
        try:
            context_parts = []
            
            for i, chunk in enumerate(chunks):
                content = chunk.get('content', '').strip()
                if content:
                    context_parts.append(f"Context {i+1}:\n{content}\n")
            
            return "\n".join(context_parts)
            
        except Exception as e:
            logger.error(f"Error building context: {e}")
            return ""
    
    async def _generate_contextual_response(self, message: str, context: str, user_context: Optional[Dict] = None) -> str:
        """
        Generate response using context and Gemini
        
        Args:
            message: User's message
            context: Relevant context from knowledge base
            user_context: Optional user context
            
        Returns:
            Generated response
        """
        try:
            if not self.knowledge_service.gemini_client:
                # Fallback to simple response without AI
                return self._generate_simple_response(message, context)
            
            # Build prompt with context
            prompt = self._build_prompt(message, context, user_context)
            
            # Generate response using Gemini
            response = self.knowledge_service.gemini_client.generate_content(prompt)
            
            ai_response = response.text.strip()
            logger.info(f"Generated Gemini response: {ai_response[:100]}...")
            
            return ai_response
            
        except Exception as e:
            logger.error(f"Gemini response generation failed: {e}")
            return self._generate_simple_response(message, context)
    
    def _build_prompt(self, message: str, context: str, user_context: Optional[Dict] = None) -> str:
        """
        Build prompt for AI response generation
        
        Args:
            message: User's message
            context: Relevant context
            user_context: Optional user context
            
        Returns:
            Formatted prompt
        """
        prompt_parts = [
            "You are a helpful assistant for Vietnam Hearts, a volunteer organization that teaches English to underprivileged children in Vietnam.",
            "Your role is to help volunteers with information about volunteering, teaching, and the organization.",
            "Always be encouraging and supportive of people wanting to volunteer.",
            "Use the following context to answer questions accurately:",
            "",
            f"Context:\n{context}",
            "",
            f"Question: {message}",
            "",
            "Answer:"
        ]
        
        if user_context:
            prompt_parts.insert(2, f"User context: {user_context}")
        
        return "\n".join(prompt_parts)
    
    def _generate_simple_response(self, message: str, context: str) -> str:
        """
        Generate simple response without AI
        
        Args:
            message: User's message
            context: Available context
            
        Returns:
            Simple response
        """
        # Simple keyword-based responses
        message_lower = message.lower()
        
        if any(word in message_lower for word in ['volunteer', 'help', 'teach']):
            return "Thank you for your interest in volunteering with Vietnam Hearts! We're always looking for dedicated people to help teach English to children in Ho Chi Minh City. Based on our information, you don't need a formal teaching certificate - just enthusiasm and a desire to help!"
        
        if any(word in message_lower for word in ['location', 'where', 'address']):
            return "Our classes are held in Binh Thanh, Ho Chi Minh City. We can provide more specific location details once you're registered as a volunteer."
        
        if any(word in message_lower for word in ['experience', 'qualification', 'certificate']):
            return "No formal teaching experience is required! We welcome volunteers of all backgrounds. What matters most is your enthusiasm and commitment to helping children learn English."
        
        if any(word in message_lower for word in ['time', 'schedule', 'when']):
            return "We have classes throughout the week. The exact schedule varies, but we're flexible and can work with your availability. Most volunteers commit to 1-2 sessions per week."
        
        # Default response
        return "Thank you for your question! I'd be happy to help you learn more about volunteering with Vietnam Hearts. Could you please provide more specific details about what you'd like to know?"
    
    async def _generate_fallback_response(self, message: str, user_context: Optional[Dict] = None, error: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate fallback response when knowledge base is unavailable
        
        Args:
            message: User's message
            user_context: Optional user context
            error: Optional error message
            
        Returns:
            Fallback response
        """
        fallback_response = self._generate_simple_response(message, "")
        
        return {
            "response": fallback_response,
            "context_used": 0,
            "confidence": "low",
            "sources": [],
            "note": "Using fallback response - knowledge base unavailable" + (f" (Error: {error})" if error else "")
        }
    
    async def get_knowledge_status(self) -> Dict[str, Any]:
        """
        Get status of knowledge base and services
        
        Returns:
            Status information
        """
        try:
            documents = await self.knowledge_service.list_documents()
            
            return {
                "knowledge_service_available": self.knowledge_service.is_available(),
                "embeddings_available": self.knowledge_service.embedding_model is not None,
                "gemini_available": self.knowledge_service.gemini_client is not None,
                "supabase_available": self.supabase is not None,
                "document_service_available": self.document_service.docs_service is not None,
                "documents_count": len(documents),
                "documents": documents
            }
            
        except Exception as e:
            logger.error(f"Error getting knowledge status: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
    
    async def list_available_docs(self, folder_id: Optional[str] = None) -> List[Dict[str, str]]:
        """
        List available Google Docs for syncing
        
        Args:
            folder_id: Optional folder ID to search in
            
        Returns:
            List of available documents
        """
        try:
            return await self.document_service.list_available_docs(folder_id)
        except Exception as e:
            logger.error(f"Error listing available docs: {e}")
            return []
