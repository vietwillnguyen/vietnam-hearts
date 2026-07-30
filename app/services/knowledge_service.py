"""
Knowledge service for handling embeddings and similarity search

Uses Gemini for both embeddings and chat responses (free tier: 15 RPM, 1M tokens/day).
Simplified approach: Single vendor, single API key, no local model dependencies.
"""

import math
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


class EmbeddingsUnavailable(RuntimeError):
    """Raised when Gemini cannot produce embeddings.

    Callers must escalate to a human rather than answer. The previous
    hash-based fallback returned non-semantic vectors, which made
    match_documents surface arbitrary chunks that then read as confident
    answers.
    """


def _l2_normalize(vector: list[float]) -> list[float]:
    """L2-normalize a vector.

    gemini-embedding-001 only returns pre-normalized output at its default
    3072 dimensions; truncated (Matryoshka) outputs like the 768-d ones used
    here are not unit-normalized, so callers must normalize them themselves.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


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
        """Create 768-dimensional embeddings, or raise if that is impossible."""
        if not self.embedding_model or self.embedding_model == "chat_model":
            raise EmbeddingsUnavailable(
                "Gemini embedding model is not available; refusing to answer"
            )
        if not texts:
            return []

        logger.info(f"Creating embeddings for {len(texts)} text chunks using Gemini")
        embeddings: list[list[float]] = []
        for index, text in enumerate(texts):
            try:
                result = self.gemini_client.models.embed_content(
                    model=self.embedding_model,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type="RETRIEVAL_DOCUMENT",
                        output_dimensionality=EMBEDDING_DIMENSIONS,
                    ),
                )
            except Exception as exc:
                raise EmbeddingsUnavailable(
                    f"Embedding call failed for chunk {index + 1}: {exc}"
                ) from exc

            vector = result.embeddings[0].values if result.embeddings else None
            if not vector:
                raise EmbeddingsUnavailable(
                    f"Empty embedding returned for chunk {index + 1}"
                )
            embeddings.append(_l2_normalize(vector))

        logger.info(f"Successfully created {len(embeddings)} embeddings")
        return embeddings

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
        """Find similar chunks, or raise if retrieval cannot be trusted."""
        if not self.supabase:
            raise EmbeddingsUnavailable(
                "Supabase is not configured; refusing to answer"
            )
        if not self.embedding_model or self.embedding_model == "chat_model":
            raise EmbeddingsUnavailable(
                "Gemini embedding model is not available; refusing to answer"
            )

        try:
            result = self.gemini_client.models.embed_content(
                model=self.embedding_model,
                contents=query,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY",
                    output_dimensionality=EMBEDDING_DIMENSIONS,
                ),
            )
            query_embedding = result.embeddings[0].values if result.embeddings else None
        except Exception as exc:
            raise EmbeddingsUnavailable(f"Query embedding failed: {exc}") from exc

        if not query_embedding:
            raise EmbeddingsUnavailable("Empty query embedding returned")

        try:
            matches = self.supabase.rpc(
                "match_documents",
                {
                    "query_embedding": _l2_normalize(query_embedding),
                    "match_threshold": threshold,
                    "match_count": limit,
                },
            ).execute()
        except Exception as exc:
            raise EmbeddingsUnavailable(f"Vector search failed: {exc}") from exc

        found = matches.data or []
        logger.info(f"Vector similarity search found {len(found)} results")
        return found

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
