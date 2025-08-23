from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import re
from typing import List

class DocumentService:
    def __init__(self):
        # Reuse your existing Google auth setup
        self.drive_service = self._get_drive_service()
        self.docs_service = self._get_docs_service()
    
    async def fetch_google_doc(self, doc_id: str) -> str:
        """Fetch content from Google Doc"""
        document = self.docs_service.documents().get(documentId=doc_id).execute()
        content = self._extract_text(document)
        return content
    
    def split_into_chunks(self, content: str, chunk_size: int = 1000) -> List[str]:
        """Split document into chunks for embedding"""
        # Simple split by paragraphs, then by size if needed
        paragraphs = content.split('\n\n')
        chunks = []
        
        for para in paragraphs:
            if len(para) <= chunk_size:
                chunks.append(para.strip())
            else:
                # Split large paragraphs
                words = para.split(' ')
                current_chunk = ""
                for word in words:
                    if len(current_chunk + word) <= chunk_size:
                        current_chunk += " " + word
                    else:
                        chunks.append(current_chunk.strip())
                        current_chunk = word
                if current_chunk:
                    chunks.append(current_chunk.strip())
        
        return [chunk for chunk in chunks if len(chunk.strip()) > 50]