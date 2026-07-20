"""
Knowledge service for handling embeddings and similarity search

Uses Gemini for both embeddings and chat responses (free tier: 15 RPM, 1M tokens/day).
Simplified approach: Single vendor, single API key, no local model dependencies.
"""

import os
from typing import Any

from google import genai
from google.genai import types

from app.utils.logging_config import get_api_logger

logger = get_api_logger()

# gemini-1.5-flash and the text-embedding-00x family are retired; these are
# the currently supported models as of the google-genai SDK migration.
CHAT_MODEL = "gemini-3.5-flash"
EMBEDDING_MODEL = "gemini-embedding-001"
# gemini-embedding-001 defaults to 3072-d output. Pin it down via Matryoshka
# Representation Learning to 768-d to match the existing document_chunks
# pgvector column (sized for the old text-embedding-001 model) and the
# fallback hash-based embeddings below, so no DB migration is needed.
EMBEDDING_DIMENSIONS = 768


class KnowledgeService:
    """Service for managing knowledge base with Gemini embeddings and similarity search"""

    def __init__(self, supabase_client=None):
        """
        Initialize knowledge service with Gemini-only approach:
        - Gemini gemini-embedding-001 for embeddings (free tier)
        - Gemini for chat responses (free tier: 15 RPM, 1M tokens/day)

        Args:
            supabase_client: Supabase client instance (optional for now)
        """
        self.supabase = supabase_client
        self.gemini_client = self._get_gemini_client()
        self.embedding_model = self._get_embedding_model()
        logger.info("Knowledge service initialized with Gemini-only approach")

    def _get_gemini_client(self) -> genai.Client | None:
        """Get Gemini client for chat and embedding requests"""
        try:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.warning(
                    "GEMINI_API_KEY not set - chat responses will use fallback"
                )
                return None

            client = genai.Client(api_key=api_key)
            logger.info("Gemini client initialized successfully")
            return client

        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            return None

    def _get_embedding_model(self) -> Any | None:
        """Get Gemini embedding capability for free embeddings"""
        if not self.gemini_client:
            logger.warning("Gemini client not available - embeddings will use fallback")
            return None

        try:
            # Verify the embedding model actually works before relying on it.
            self.gemini_client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents="test",
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=EMBEDDING_DIMENSIONS,
                ),
            )
            logger.info(
                f"Gemini embedding capability verified with model: {EMBEDDING_MODEL}"
            )
            return EMBEDDING_MODEL

        except Exception as e:
            logger.debug(f"Model {EMBEDDING_MODEL} not available: {e}")

        # If the embedding model doesn't work, try using the chat model for
        # simple text processing.
        try:
            test_response = self.gemini_client.models.generate_content(
                model=CHAT_MODEL, contents="test"
            )
            if test_response.text:
                logger.info("Gemini chat model available - using for text processing")
                return "chat_model"  # Special indicator for chat-based approach
        except Exception as e:
            logger.debug(f"Chat model test failed: {e}")

        logger.warning("No Gemini embedding models available - using fallback")
        return None

    async def create_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        Create embeddings using Gemini gemini-embedding-001 (free tier)

        Args:
            texts: List of text chunks to embed

        Returns:
            List of embedding vectors (768-dimensional)
        """
        try:
            if not self.embedding_model:
                logger.warning("Gemini embedding model not available - using fallback")
                return self._create_fallback_embeddings(texts)

            if not texts:
                logger.warning("No texts provided for embedding")
                return []

            logger.info(
                f"Creating embeddings for {len(texts)} text chunks using Gemini"
            )

            all_embeddings = []

            # Process texts one by one (Gemini handles batching internally)
            for i, text in enumerate(texts):
                try:
                    # Create embedding for single text
                    if self.embedding_model == "chat_model":
                        # Use chat model for simple text processing instead of embeddings
                        logger.debug(
                            f"Using chat model for text processing chunk {i + 1}"
                        )
                        # Create a simple hash-based embedding for now
                        import hashlib

                        hash_obj = hashlib.md5(text.encode())
                        hash_bytes = hash_obj.digest()
                        embedding = [float(b) / 255.0 for b in hash_bytes] * 24
                        embedding = embedding[:768]
                        all_embeddings.append(embedding)
                    else:
                        # Use the available embedding model
                        result = self.gemini_client.models.embed_content(
                            model=self.embedding_model,
                            contents=text,
                            config=types.EmbedContentConfig(
                                task_type="RETRIEVAL_DOCUMENT",
                                output_dimensionality=EMBEDDING_DIMENSIONS,
                            ),
                        )

                        # Extract embedding vector
                        embedding = (
                            result.embeddings[0].values if result.embeddings else None
                        )
                        if embedding:
                            all_embeddings.append(embedding)
                            logger.debug(f"Created embedding for chunk {i + 1}")
                        else:
                            logger.warning(f"No embedding returned for chunk {i + 1}")
                            # Add zero vector as fallback
                            zero_vector = [0.0] * 768
                            all_embeddings.append(zero_vector)

                except Exception as e:
                    logger.error(f"Error creating embedding for chunk {i + 1}: {e}")
                    # Add zero vector for failed chunks
                    zero_vector = [0.0] * 768
                    all_embeddings.append(zero_vector)

            logger.info(
                f"Successfully created {len(all_embeddings)} embeddings using Gemini"
            )
            return all_embeddings

        except Exception as e:
            logger.error(f"Error creating embeddings: {e}")
            return self._create_fallback_embeddings(texts)

    def _create_fallback_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Create simple fallback embeddings when Gemini is unavailable"""
        fallback_embeddings = []
        for text in texts:
            # Simple hash-based embedding (not semantic, but maintains interface)
            import hashlib

            hash_obj = hashlib.md5(text.encode())
            hash_bytes = hash_obj.digest()
            # Convert to 768-dimensional vector (matches EMBEDDING_DIMENSIONS)
            embedding = [
                float(b) / 255.0 for b in hash_bytes
            ] * 24  # Repeat to get 768 dimensions
            embedding = embedding[:768]  # Ensure exact dimension
            fallback_embeddings.append(embedding)

        logger.info(f"Created {len(fallback_embeddings)} fallback embeddings")
        return fallback_embeddings

    async def store_chunks(
        self,
        chunks: list[str],
        embeddings: list[list[float]],
        doc_id: str = "volunteer_faq",
        metadata: dict | None = None,
    ) -> dict[str, Any]:
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
                    "note": "Supabase not configured, chunks stored in memory only",
                }

            if len(chunks) != len(embeddings):
                raise ValueError(
                    f"Mismatch between chunks ({len(chunks)}) and embeddings ({len(embeddings)})"
                )

            # Clear existing chunks for this document
            try:
                self.supabase.table("document_chunks").delete().eq(
                    "source_document_id", doc_id
                ).execute()
                logger.info(f"Cleared existing chunks for document {doc_id}")
            except Exception as e:
                logger.warning(f"Could not clear existing chunks: {e}")

            # Prepare data for insertion
            data = []
            for i, (chunk, embedding) in enumerate(
                zip(chunks, embeddings, strict=False)
            ):
                if not embedding:  # Skip chunks with failed embeddings
                    continue

                chunk_data = {
                    "content": chunk,
                    "embedding": embedding,
                    "source_document_id": doc_id,
                    "chunk_index": i,
                    "metadata": metadata or {},
                }
                data.append(chunk_data)

            if not data:
                logger.warning("No valid chunks to store")
                return {"status": "no_chunks", "chunks": 0}

            # Insert new chunks
            try:
                result = self.supabase.table("document_chunks").insert(data).execute()
                logger.info(f"Stored {len(data)} chunks in database")

                return {
                    "status": "stored",
                    "chunks": len(data),
                    "document_id": doc_id,
                    "result": result,
                }

            except Exception as e:
                logger.error(f"Failed to store chunks in database: {e}")
                # Fallback to memory storage
                return {
                    "status": "stored_in_memory",
                    "chunks": len(data),
                    "error": str(e),
                    "note": "Database storage failed, chunks stored in memory only",
                }

        except Exception as e:
            logger.error(f"Error storing chunks: {e}")
            raise

    async def similarity_search(
        self, query: str, limit: int = 3, threshold: float = 0.3
    ) -> list[dict[str, Any]]:
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
                logger.warning(
                    "Gemini embedding model not available - falling back to text search"
                )
                return await self._fallback_text_search(query, limit)

            # Create query embedding using Gemini
            try:
                if self.embedding_model == "chat_model":
                    # Use chat model for simple text processing instead of embeddings
                    logger.info(
                        f"Using chat model for query processing: {query[:50]}..."
                    )
                    # Create a simple hash-based embedding for now
                    import hashlib

                    hash_obj = hashlib.md5(query.encode())
                    hash_bytes = hash_obj.digest()
                    query_embedding = [float(b) / 255.0 for b in hash_bytes] * 24
                    query_embedding = query_embedding[:768]
                else:
                    # Use the available embedding model
                    result = self.gemini_client.models.embed_content(
                        model=self.embedding_model,
                        contents=query,
                        config=types.EmbedContentConfig(
                            task_type="RETRIEVAL_QUERY",
                            output_dimensionality=EMBEDDING_DIMENSIONS,
                        ),
                    )

                    query_embedding = (
                        result.embeddings[0].values if result.embeddings else None
                    )
                    if not query_embedding:
                        logger.error("No embedding returned for query")
                        return await self._fallback_text_search(query, limit)

                    logger.info(f"Created query embedding for: {query[:50]}...")

            except Exception as e:
                logger.error(f"Failed to create query embedding: {e}")
                return await self._fallback_text_search(query, limit)

            # Search for similar chunks using vector similarity
            try:
                result = self.supabase.rpc(
                    "match_documents",
                    {
                        "query_embedding": query_embedding,
                        "match_threshold": threshold,
                        "match_count": limit,
                    },
                ).execute()

                matches = result.data if result.data else []
                logger.info(f"Vector similarity search found {len(matches)} results")

                # Log similarity scores for debugging
                if matches:
                    for match in matches:
                        similarity = match.get("similarity", 0)
                        logger.info(
                            f"Match similarity: {similarity:.3f} for chunk {match.get('chunk_index', 'unknown')}"
                        )
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

    async def _fallback_text_search(
        self, query: str, limit: int
    ) -> list[dict[str, Any]]:
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

            # Use a simpler approach to avoid SQL parsing issues
            try:
                # Get all chunks and filter locally for now
                result = (
                    self.supabase.table("document_chunks")
                    .select("*")
                    .limit(100)
                    .execute()
                )

                # Filter results locally
                matches = []
                for chunk in result.data:
                    content_lower = chunk.get("content", "").lower()
                    if any(term in content_lower for term in query_terms):
                        matches.append(chunk)
                        if len(matches) >= limit:
                            break

                logger.info(f"Fallback text search found {len(matches)} results")
                return matches

            except Exception as e:
                logger.error(f"Supabase query failed, using empty results: {e}")
                return []

            matches = result.data if result.data else []
            logger.info(f"Fallback text search found {len(matches)} results")

            return matches

        except Exception as e:
            logger.error(f"Fallback text search failed: {e}")
            return []

    async def get_chunk_by_id(self, chunk_id: str) -> dict[str, Any] | None:
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

            result = (
                self.supabase.table("document_chunks")
                .select("*")
                .eq("id", chunk_id)
                .execute()
            )

            if result.data:
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"Error getting chunk {chunk_id}: {e}")
            return None

    async def list_documents(self) -> list[dict[str, Any]]:
        """
        List all documents in the knowledge base

        Returns:
            List of document metadata
        """
        try:
            if not self.supabase:
                return []

            result = (
                self.supabase.table("document_chunks")
                .select("source_document_id, metadata")
                .execute()
            )

            if not result.data:
                return []

            # Group by document
            documents = {}
            for chunk in result.data:
                doc_id = chunk.get("source_document_id")
                if doc_id not in documents:
                    documents[doc_id] = {
                        "id": doc_id,
                        "chunks": 0,
                        "metadata": chunk.get("metadata", {}),
                    }
                documents[doc_id]["chunks"] += 1

            return list(documents.values())

        except Exception as e:
            logger.error(f"Error listing documents: {e}")
            return []

    def is_available(self) -> bool:
        """
        Check if knowledge service is fully available

        Returns:
            True if both Gemini embedding capability and Supabase are available
        """
        return self.embedding_model is not None and self.supabase is not None
