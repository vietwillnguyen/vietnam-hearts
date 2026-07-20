"""
Re-embed the existing knowledge base with the current Gemini embedding model.

The google-generativeai -> google-genai migration (see app/services/knowledge_service.py)
switched the embedding model from the retired text-embedding-001 to
gemini-embedding-001. Both happen to produce 768-dimensional vectors (the new
model is truncated via output_dimensionality to match), but they are vectors
from two different, incompatible embedding spaces - similarity scores between
an old-model document vector and a new-model query vector are meaningless.

This script re-syncs every document currently stored in document_chunks so all
rows are re-embedded with the new model. It is NOT run automatically by any
deploy or CI step - it must be run manually, once, with real GEMINI_API_KEY
and Supabase credentials in the environment. The bot/knowledge-base feature is
currently disabled (public_bot_router/admin_router are commented out in
app/main.py), so this is also a prerequisite to run before that feature is
turned back on.

Usage:
    uv run scripts/reembed_knowledge_base.py [--dry-run]

--dry-run lists the documents that would be re-synced without calling any
embedding API or writing to the database.
"""

import argparse
import asyncio
import sys

from app.config import SUPABASE_SECRET_KEY, SUPABASE_URL
from app.services.bot_service import BotService
from app.utils.logging_config import get_api_logger

logger = get_api_logger()


def _build_bot_service() -> BotService:
    from supabase import create_client

    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must be set to re-embed the "
            "knowledge base"
        )

    supabase_client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return BotService(supabase_client)


async def reembed_all(dry_run: bool) -> int:
    bot_service = _build_bot_service()
    documents = await bot_service.knowledge_service.list_documents()

    if not documents:
        logger.info("No documents found in document_chunks - nothing to re-embed")
        return 0

    logger.info(f"Found {len(documents)} document(s) to re-embed")

    failures = 0
    for document in documents:
        doc_id = document["id"]
        metadata = document.get("metadata") or {}

        if dry_run:
            logger.info(f"[dry-run] Would re-sync document {doc_id}")
            continue

        logger.info(f"Re-syncing document {doc_id}")
        result = await bot_service.sync_documents(doc_id, metadata)
        if result["status"] != "success":
            failures += 1
            logger.error(f"Failed to re-sync document {doc_id}: {result['message']}")
        else:
            logger.info(
                f"Re-synced document {doc_id}: {result['chunks']} chunks, "
                f"{result['embeddings']} embeddings"
            )

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List documents that would be re-synced without calling any API",
    )
    args = parser.parse_args()

    failures = asyncio.run(reembed_all(args.dry_run))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
