"""
Main bot service for Vietnam Hearts chatbot

Orchestrates document processing, knowledge base queries, and generates intelligent responses.
"""

from typing import Any

from app.utils.logging_config import get_api_logger

from .document_service import DocumentService
from .knowledge_service import CHAT_MODEL, KnowledgeService

logger = get_api_logger()


class NoRelevantContext(RuntimeError):
    """Raised when retrieval succeeded but surfaced nothing relevant.

    Distinct from EmbeddingsUnavailable, which means retrieval itself is
    broken. Phase 1 maps both to a holding message plus an escalation; in
    phase 0 both simply mean the bot does not answer, because answering
    without grounding is the failure mode this design exists to prevent.
    """


class GenerationUnavailable(RuntimeError):
    """Raised when retrieval succeeded but the model could not answer.

    Distinct from both EmbeddingsUnavailable, which means retrieval itself is
    broken, and NoRelevantContext, which means retrieval worked and the
    knowledge base simply does not cover the question. Here the grounding
    exists and only generation failed, so the fix is a model or quota problem
    rather than a knowledge base gap. Phase 1 treats it as needs_admin like
    the others while keeping the cause distinguishable in logs and in the
    escalation it raises.
    """


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

    def health_check(self) -> dict[str, Any]:
        """
        Probe the dependencies the bot actually needs to answer a question.

        Returns a dict with:
            status: "healthy", "degraded", or "unhealthy"
            error:  a one-line reason when not healthy, else None
            checks: per-dependency detail

        "unhealthy" means a dependency is missing or broken and the bot cannot
        work. "degraded" is reserved for a working stack with an empty
        knowledge base - real and worth reporting, but a known open question
        rather than a fault.

        Cheap by design: this is called on every /health poll, so it reads
        state the service already established and makes at most one
        single-row lookup against the vector store. The one live retry it can
        trigger is rate limited inside KnowledgeService.revalidate.

        Never raises. A health endpoint that 500s is worse than one that lies.
        """
        checks: dict[str, str] = {}
        try:
            knowledge = self.knowledge_service

            # Give a probe that failed during construction a chance to recover
            # rather than reporting a transient outage as a permanent fault.
            knowledge.revalidate()

            checks["gemini"] = "ok" if knowledge.gemini_client else "unconfigured"
            checks["embeddings"] = (
                knowledge.embedding_model if knowledge.embedding_model else "unverified"
            )
            checks["vector_store"] = "ok" if knowledge.supabase else "unconfigured"

            if not knowledge.gemini_client:
                checks["documents"] = "not checked"
                return self._verdict(
                    "unhealthy",
                    "Gemini client unavailable - check GEMINI_API_KEY",
                    checks,
                )
            if not knowledge.embedding_model:
                checks["documents"] = "not checked"
                return self._verdict(
                    "unhealthy",
                    "Gemini embedding model did not verify - check the model name",
                    checks,
                )
            if not knowledge.supabase:
                checks["documents"] = "not checked"
                return self._verdict(
                    "unhealthy", "Supabase not configured - no vector store", checks
                )

            has_documents = knowledge.has_indexed_documents()
            checks["documents"] = "ok" if has_documents else "empty"
            if not has_documents:
                return self._verdict(
                    "degraded",
                    "Knowledge base has no indexed documents - answers would be ungrounded",
                    checks,
                )

            return self._verdict("healthy", None, checks)

        except Exception as e:
            logger.error(f"Bot health check failed: {e}", exc_info=True)
            return self._verdict("unhealthy", str(e), checks)

    @staticmethod
    def _verdict(
        status: str, error: str | None, checks: dict[str, str]
    ) -> dict[str, Any]:
        return {"status": status, "error": error, "checks": checks}

    async def sync_documents(
        self, doc_id: str, metadata: dict | None = None
    ) -> dict[str, Any]:
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
                    "doc_id": doc_id,
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
                    "doc_id": doc_id,
                }

            # Split into chunks
            try:
                chunks = self.document_service.split_into_chunks(
                    content, chunk_size=1000, overlap=100
                )
                logger.info(f"Split document into {len(chunks)} chunks")
            except Exception as e:
                logger.error(f"Failed to split document into chunks: {e}")
                return {
                    "status": "error",
                    "message": f"Failed to split document: {str(e)}",
                    "doc_id": doc_id,
                }

            if not chunks:
                return {
                    "status": "error",
                    "message": "No content chunks generated",
                    "doc_id": doc_id,
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
                    "doc_id": doc_id,
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
                    "doc_id": doc_id,
                }

            return {
                "status": "success",
                "message": f"Document {doc_id} synced successfully",
                "doc_id": doc_id,
                "chunks": len(chunks),
                "embeddings": len(embeddings),
                "store_result": store_result,
            }

        except Exception as e:
            logger.error(f"Document sync failed: {e}")
            return {
                "status": "error",
                "message": f"Document sync failed: {str(e)}",
                "doc_id": doc_id,
            }

    async def chat(
        self, message: str, user_context: dict | None = None
    ) -> dict[str, Any]:
        """Answer a question from the knowledge base, or raise.

        Raises EmbeddingsUnavailable when retrieval is broken,
        NoRelevantContext when it returned nothing usable, and
        GenerationUnavailable when grounding was found but the model could
        not turn it into an answer. Callers must not turn any of them into a
        guess: an ungrounded answer to a prospective volunteer is the harm
        this whole path is built to avoid.
        """
        logger.info(f"Processing chat message: {message[:100]}...")

        relevant_chunks = await self.knowledge_service.similarity_search(
            message, limit=3
        )
        if not relevant_chunks:
            raise NoRelevantContext(
                "No relevant knowledge base content for this question"
            )

        context = self._build_context(relevant_chunks)
        logger.info(f"Found {len(relevant_chunks)} relevant chunks")

        response = await self._generate_contextual_response(
            message, context, user_context
        )

        top_similarity = max(
            (chunk.get("similarity") or 0.0) for chunk in relevant_chunks
        )
        return {
            "response": response,
            "context_used": len(relevant_chunks),
            "confidence": top_similarity,
            "sources": [chunk.get("source_document_id") for chunk in relevant_chunks],
        }

    def _build_context(self, chunks: list[dict[str, Any]]) -> str:
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
                content = chunk.get("content", "").strip()
                if content:
                    context_parts.append(f"Context {i+1}:\n{content}\n")

            return "\n".join(context_parts)

        except Exception as e:
            logger.error(f"Error building context: {e}")
            return ""

    async def _generate_contextual_response(
        self, message: str, context: str, user_context: dict | None = None
    ) -> str:
        """Generate a grounded response, or raise if generation is unavailable."""
        if not self.knowledge_service.gemini_client:
            raise GenerationUnavailable(
                "Gemini client unavailable; refusing to answer ungrounded"
            )

        prompt = self._build_prompt(message, context, user_context)
        try:
            response = self.knowledge_service.gemini_client.models.generate_content(
                model=CHAT_MODEL, contents=prompt
            )
        except Exception as exc:
            raise GenerationUnavailable(f"Gemini generation failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise GenerationUnavailable("Gemini returned an empty response")

        logger.info(f"Generated Gemini response: {text[:100]}...")
        return text

    def _build_prompt(
        self, message: str, context: str, user_context: dict | None = None
    ) -> str:
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
            "Answer:",
        ]

        if user_context:
            prompt_parts.insert(2, f"User context: {user_context}")

        return "\n".join(prompt_parts)

    async def get_knowledge_status(self) -> dict[str, Any]:
        """
        Get status of knowledge base and services

        Returns:
            Status information
        """
        try:
            documents = await self.knowledge_service.list_documents()

            return {
                "knowledge_service_available": self.knowledge_service.is_available(),
                "embeddings_available": self.knowledge_service.embedding_model
                is not None,
                "gemini_available": self.knowledge_service.gemini_client is not None,
                "supabase_available": self.supabase is not None,
                "document_service_available": self.document_service.docs_service
                is not None,
                "documents_count": len(documents),
                "documents": documents,
            }

        except Exception as e:
            logger.error(f"Error getting knowledge status: {e}")
            return {"status": "error", "message": str(e)}

    async def list_available_docs(
        self, folder_id: str | None = None
    ) -> list[dict[str, str]]:
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
