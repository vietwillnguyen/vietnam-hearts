"""
Document service for Google Drive integration and document processing

Handles fetching documents from Google Drive and splitting them into chunks for the knowledge base.
"""

import re
import logging
from typing import List, Dict, Any, Optional
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from app.config import GOOGLE_APPLICATION_CREDENTIALS
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

class DocumentService:
    """Service for handling Google Drive documents and text processing"""
    
    def __init__(self):
        """Initialize Google Drive and Docs services"""
        try:
            self.drive_service = self._get_drive_service()
            self.docs_service = self._get_docs_service()
            logger.info("Document service initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize document service: {e}")
            self.drive_service = None
            self.docs_service = None
    
    def _get_drive_service(self):
        """Get Google Drive service"""
        try:
            credentials = Credentials.from_service_account_file(
                GOOGLE_APPLICATION_CREDENTIALS,
                scopes=['https://www.googleapis.com/auth/drive.readonly']
            )
            return build('drive', 'v3', credentials=credentials, cache_discovery=False)
        except Exception as e:
            logger.error(f"Failed to create Drive service: {e}")
            raise
    
    def _get_docs_service(self):
        """Get Google Docs service"""
        try:
            credentials = Credentials.from_service_account_file(
                GOOGLE_APPLICATION_CREDENTIALS,
                scopes=['https://www.googleapis.com/auth/documents.readonly']
            )
            return build('docs', 'v1', credentials=credentials, cache_discovery=False)
        except Exception as e:
            logger.error(f"Failed to create Docs service: {e}")
            raise
    
    async def fetch_google_doc(self, doc_id: str) -> str:
        """
        Fetch content from Google Doc
        
        Args:
            doc_id: Google Doc ID from URL
            
        Returns:
            Document content as plain text
        """
        try:
            if not self.docs_service:
                raise ValueError("Docs service not initialized")
            
            logger.info(f"Fetching Google Doc: {doc_id}")
            document = self.docs_service.documents().get(documentId=doc_id).execute()
            content = self._extract_text(document)
            logger.info(f"Successfully fetched document with {len(content)} characters")
            return content
            
        except HttpError as e:
            logger.error(f"HTTP error fetching document {doc_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching document {doc_id}: {e}")
            raise
    
    def _extract_text(self, document: Dict[str, Any]) -> str:
        """
        Extract plain text from Google Doc structure
        
        Args:
            document: Google Doc API response
            
        Returns:
            Plain text content
        """
        try:
            content = document.get('body', {}).get('content', [])
            text_parts = []
            
            for element in content:
                if 'paragraph' in element:
                    paragraph = element['paragraph']
                    for para_element in paragraph.get('elements', []):
                        if 'textRun' in para_element:
                            text = para_element['textRun'].get('content', '')
                            text_parts.append(text)
            
            # Join and clean up text
            full_text = ''.join(text_parts)
            
            # Clean up extra whitespace and formatting
            cleaned_text = re.sub(r'\n\s*\n', '\n\n', full_text)  # Remove extra blank lines
            cleaned_text = re.sub(r' +', ' ', cleaned_text)  # Remove extra spaces
            cleaned_text = cleaned_text.strip()
            
            return cleaned_text
            
        except Exception as e:
            logger.error(f"Error extracting text from document: {e}")
            raise
    
    def split_into_chunks(self, content: str, chunk_size: int = 1000, overlap: int = 100) -> List[str]:
        """
        Split document into overlapping chunks for embedding
        
        Args:
            content: Document text content
            chunk_size: Maximum size of each chunk
            overlap: Number of characters to overlap between chunks
            
        Returns:
            List of text chunks
        """
        try:
            if not content or len(content.strip()) == 0:
                logger.warning("Empty content provided for chunking")
                return []
            
            # Split by paragraphs first
            paragraphs = content.split('\n\n')
            chunks = []
            current_chunk = ""
            
            for paragraph in paragraphs:
                paragraph = paragraph.strip()
                if not paragraph:
                    continue
                
                # If adding this paragraph would exceed chunk size
                if len(current_chunk) + len(paragraph) > chunk_size and current_chunk:
                    # Save current chunk
                    chunks.append(current_chunk.strip())
                    
                    # Start new chunk with overlap from previous
                    if overlap > 0 and current_chunk:
                        overlap_text = current_chunk[-overlap:].strip()
                        current_chunk = overlap_text + "\n\n" + paragraph
                    else:
                        current_chunk = paragraph
                else:
                    if current_chunk:
                        current_chunk += "\n\n" + paragraph
                    else:
                        current_chunk = paragraph
            
            # Add the last chunk
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            
            # Filter out chunks that are too short
            filtered_chunks = [chunk for chunk in chunks if len(chunk.strip()) > 50]
            
            logger.info(f"Split content into {len(filtered_chunks)} chunks")
            return filtered_chunks
            
        except Exception as e:
            logger.error(f"Error splitting content into chunks: {e}")
            raise
    
    async def list_available_docs(self, folder_id: Optional[str] = None) -> List[Dict[str, str]]:
        """
        List available Google Docs
        
        Args:
            folder_id: Optional folder ID to search in
            
        Returns:
            List of document metadata
        """
        try:
            if not self.drive_service:
                raise ValueError("Drive service not initialized")
            
            query = "mimeType='application/vnd.google-apps.document'"
            if folder_id:
                query += f" and '{folder_id}' in parents"
            
            results = self.drive_service.files().list(
                q=query,
                fields="files(id,name,createdTime,modifiedTime)",
                orderBy="modifiedTime desc"
            ).execute()
            
            files = results.get('files', [])
            logger.info(f"Found {len(files)} Google Docs")
            
            return [
                {
                    'id': file['id'],
                    'name': file['name'],
                    'created_time': file.get('createdTime'),
                    'modified_time': file.get('modifiedTime')
                }
                for file in files
            ]
            
        except Exception as e:
            logger.error(f"Error listing documents: {e}")
            raise
    
    def validate_doc_id(self, doc_id: str) -> bool:
        """
        Validate if a Google Doc ID is accessible
        
        Args:
            doc_id: Google Doc ID to validate
            
        Returns:
            True if accessible, False otherwise
        """
        try:
            if not self.docs_service:
                return False
            
            # Try to get document metadata
            self.docs_service.documents().get(documentId=doc_id).execute()
            return True
            
        except HttpError as e:
            if e.resp.status == 404:
                logger.warning(f"Document {doc_id} not found")
                return False
            else:
                logger.warning(f"Document {doc_id} access error: {e}")
                return False
        except Exception as e:
            logger.warning(f"Document {doc_id} validation error: {e}")
            return False