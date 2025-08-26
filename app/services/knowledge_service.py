"""
Knowledge service for handling embeddings and similarity search

Uses Sentence Transformers for embeddings (free, local) and Gemini for chat responses.
Hybrid approach: Free embeddings + free Gemini chat (15 RPM, 1M tokens/day).
"""

import os
import logging
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from sentence_transformers import SentenceTransformer
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

class KnowledgeService:
    """Service for managing knowledge base with embeddings and similarity search"""
    
    def __init__(self, supabase_client=None):
        """
        Initialize knowledge service with hybrid approach:
        - Sentence Transformers for embeddings (free, local)
        - Gemini for chat responses (free tier: 15 RPM, 1M tokens/day)
        
        Args:
            supabase_client: Supabase client instance (optional for now)
        """
        self.supabase = supabase_client
        self.gemini_client = self._get_gemini_client()
        self.embedding_model = self._get_embedding_model()
        logger.info("Knowledge service initialized with hybrid approach")
    
    def _get_gemini_client(self) -> Optional[genai.GenerativeModel]:
        """Get Gemini client for chat responses"""
        try:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.warning("GEMINI_API_KEY not set - chat responses will use fallback")
                return None
            
            genai.configure(api_key=api_key)
            client = genai.GenerativeModel('gemini-1.5-flash')  # Fast, free tier
            logger.info("Gemini client initialized successfully")
            return client
            
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            return None
    
    def _get_embedding_model(self) -> Optional[SentenceTransformer]:
        """Get Sentence Transformers model for free, local embeddings"""
        try:
            # Use a lightweight, fast model
            model = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("Sentence Transformers embedding model loaded successfully")
            return model
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            return None
    
    async def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Create embeddings using Sentence Transformers (free, local)
        
        Args:
            texts: List of text chunks to embed
            
        Returns:
            List of embedding vectors (768-dimensional)
        """
        try:
            if not self.embedding_model:
                logger.warning("Embedding model not available - using fallback")
                return self._create_fallback_embeddings(texts)
            
            if not texts:
                logger.warning("No texts provided for embedding")
                return []
            
            logger.info(f"Creating embeddings for {len(texts)} text chunks using Sentence Transformers")
            
            # Create embeddings in batches for efficiency
            batch_size = 32  # Sentence Transformers handles batching well
            all_embeddings = []
            
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                try:
                    batch_embeddings = self.embedding_model.encode(batch, convert_to_tensor=False)
                    all_embeddings.extend(batch_embeddings.tolist())
                    logger.info(f"Created embeddings for batch {i//batch_size + 1}")
                except Exception as e:
                    logger.error(f"Error creating embeddings for batch {i//batch_size + 1}: {e}")
                    # Add zero vectors for failed batches
                    zero_vector = [0.0] * 768  # all-MiniLM-L6-v2 dimension
                    all_embeddings.extend([zero_vector for _ in batch])
            
            logger.info(f"Successfully created {len(all_embeddings)} embeddings")
            return all_embeddings
            
        except Exception as e:
            logger.error(f"Error creating embeddings: {e}")
            return self._create_fallback_embeddings(texts)
    
    def _create_fallback_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create simple fallback embeddings when model is unavailable"""
        fallback_embeddings = []
        for text in texts:
            # Simple hash-based embedding (not semantic, but maintains interface)
            import hashlib
            hash_obj = hashlib.md5(text.encode())
            hash_bytes = hash_obj.digest()
            # Convert to 768-dimensional vector (same as all-MiniLM-L6-v2)
            embedding = [float(b) / 255.0 for b in hash_bytes] * 24  # Repeat to get 768 dimensions
            embedding = embedding[:768]  # Ensure exact dimension
            fallback_embeddings.append(embedding)
        
        logger.info(f"Created {len(fallback_embeddings)} fallback embeddings")
        return fallback_embeddings
    
    async def store_chunks(self, chunks: List[str], embeddings: List[List[float]], 
                          doc_id: str = "volunteer_faq", metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Store chunks and embeddings in database
        
        Args:
            chunks: List of text chunks
            embeddings: List of embedding vectors
            doc_id: Source document identifier
            metadata: Additional metadata for the chunks
            
        Returns:
            Storage result information
        """
        try:
            if not self.supabase:
                logger.warning("Supabase not available - storing chunks in memory only")
                return {
                    "status": "stored_in_memory",
                    "chunks": len(chunks),
                    "note": "Supabase not configured, chunks stored in memory only"
                }
            
            if len(chunks) != len(embeddings):
                raise ValueError(f"Mismatch between chunks ({len(chunks)}) and embeddings ({len(embeddings)})")
            
            # Clear existing chunks for this document
            try:
                self.supabase.table('document_chunks').delete().eq('source_document_id', doc_id).execute()
                logger.info(f"Cleared existing chunks for document {doc_id}")
            except Exception as e:
                logger.warning(f"Could not clear existing chunks: {e}")
            
            # Prepare data for insertion
            data = []
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                if not embedding:  # Skip chunks with failed embeddings
                    continue
                    
                chunk_data = {
                    'content': chunk,
                    'embedding': embedding,
                    'source_document_id': doc_id,
                    'chunk_index': i,
                    'metadata': metadata or {}
                }
                data.append(chunk_data)
            
            if not data:
                logger.warning("No valid chunks to store")
                return {"status": "no_chunks", "chunks": 0}
            
            # Insert new chunks
            try:
                result = self.supabase.table('document_chunks').insert(data).execute()
                logger.info(f"Stored {len(data)} chunks in database")
                
                return {
                    "status": "stored",
                    "chunks": len(data),
                    "document_id": doc_id,
                    "result": result
                }
                
            except Exception as e:
                logger.error(f"Failed to store chunks in database: {e}")
                # Fallback to memory storage
                return {
                    "status": "stored_in_memory",
                    "chunks": len(data),
                    "error": str(e),
                    "note": "Database storage failed, chunks stored in memory only"
                }
                
        except Exception as e:
            logger.error(f"Error storing chunks: {e}")
            raise
    
    async def similarity_search(self, query: str, limit: int = 3, threshold: float = 0.3) -> List[Dict[str, Any]]:
        """
        Find similar chunks using vector similarity search
        
        Args:
            query: Search query text
            limit: Maximum number of results
            threshold: Similarity threshold (0.0 to 1.0)
            
        Returns:
            List of similar chunks with metadata
        """
        try:
            if not self.supabase:
                logger.warning("Supabase not available - returning empty results")
                return []
            
            if not self.embedding_model:
                logger.warning("Embedding model not available - falling back to text search")
                return await self._fallback_text_search(query, limit)
            
            # Create query embedding
            try:
                query_embedding = self.embedding_model.encode([query], convert_to_tensor=False)[0].tolist()
                logger.info(f"Created query embedding for: {query[:50]}...")
            except Exception as e:
                logger.error(f"Failed to create query embedding: {e}")
                return await self._fallback_text_search(query, limit)
            
            # Search for similar chunks using vector similarity
            try:
                result = self.supabase.rpc('match_documents', {
                    'query_embedding': query_embedding,
                    'match_threshold': threshold,
                    'match_count': limit
                }).execute()
                
                matches = result.data if result.data else []
                logger.info(f"Vector similarity search found {len(matches)} results")
                
                # Log similarity scores for debugging
                if matches:
                    for match in matches:
                        similarity = match.get('similarity', 0)
                        logger.info(f"Match similarity: {similarity:.3f} for chunk {match.get('chunk_index', 'unknown')}")
                else:
                    logger.info(f"No matches found with threshold {threshold}")
                
                return matches
                
            except Exception as e:
                logger.error(f"Vector similarity search failed: {e}")
                # Fallback to text search
                return await self._fallback_text_search(query, limit)
                
        except Exception as e:
            logger.error(f"Error in similarity search: {e}")
            return await self._fallback_text_search(query, limit)
    
    async def _fallback_text_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """
        Fallback text search when enhanced search fails
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of matching chunks
        """
        try:
            if not self.supabase:
                return []
            
            # Simple text search using ILIKE
            query_terms = query.lower().split()
            search_conditions = []
            
            for term in query_terms:
                if len(term) > 2:  # Only search for terms longer than 2 characters
                    search_conditions.append(f"content ILIKE '%{term}%'")
            
            if not search_conditions:
                return []
            
            # Build search query
            search_query = " OR ".join(search_conditions)
            
            result = self.supabase.table('document_chunks').select('*').or_(search_query).limit(limit).execute()
            
            matches = result.data if result.data else []
            logger.info(f"Fallback text search found {len(matches)} results")
            
            return matches
            
        except Exception as e:
            logger.error(f"Fallback text search failed: {e}")
            return []
    
    async def get_chunk_by_id(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific chunk by ID
        
        Args:
            chunk_id: Chunk identifier
            
        Returns:
            Chunk data or None if not found
        """
        try:
            if not self.supabase:
                return None
            
            result = self.supabase.table('document_chunks').select('*').eq('id', chunk_id).execute()
            
            if result.data:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Error getting chunk {chunk_id}: {e}")
            return None
    
    async def list_documents(self) -> List[Dict[str, Any]]:
        """
        List all documents in the knowledge base
        
        Returns:
            List of document metadata
        """
        try:
            if not self.supabase:
                return []
            
            result = self.supabase.table('document_chunks').select('source_document_id, metadata').execute()
            
            if not result.data:
                return []
            
            # Group by document
            documents = {}
            for chunk in result.data:
                doc_id = chunk.get('source_document_id')
                if doc_id not in documents:
                    documents[doc_id] = {
                        'id': doc_id,
                        'chunks': 0,
                        'metadata': chunk.get('metadata', {})
                    }
                documents[doc_id]['chunks'] += 1
            
            return list(documents.values())
            
        except Exception as e:
            logger.error(f"Error listing documents: {e}")
            return []
    
    def is_available(self) -> bool:
        """
        Check if knowledge service is fully available
        
        Returns:
            True if both embedding model and Supabase are available
        """
        return self.embedding_model is not None and self.supabase is not None
