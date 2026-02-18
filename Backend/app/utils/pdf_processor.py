import fitz  # PyMuPDF
import logging
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class PDFProcessor:
    """Extract text and metadata from PDF files"""
    
    def __init__(self):
        pass
    
    def extract_text(self, pdf_path: str) -> Dict[str, any]:
        """
        Extract text from PDF file
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Dictionary with text and metadata
        """
        try:
            doc = fitz.open(pdf_path)
            
            # Extract text page by page
            pages_text = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                pages_text.append({
                    'page_number': page_num + 1,
                    'text': text.strip()
                })
            
            # Get metadata
            metadata = {
                'title': doc.metadata.get('title', ''),
                'author': doc.metadata.get('author', ''),
                'subject': doc.metadata.get('subject', ''),
                'total_pages': len(doc),
                'filename': Path(pdf_path).name
            }
            
            doc.close()
            
            return {
                'pages': pages_text,
                'metadata': metadata,
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error extracting PDF text: {e}")
            return {
                'pages': [],
                'metadata': {},
                'success': False,
                'error': str(e)
            }
    
    def get_page_text(self, pdf_path: str, page_number: int) -> Optional[str]:
        """Get text from specific page"""
        try:
            doc = fitz.open(pdf_path)
            if page_number < 1 or page_number > len(doc):
                return None
            
            page = doc[page_number - 1]
            text = page.get_text()
            doc.close()
            
            return text.strip()
            
        except Exception as e:
            logger.error(f"Error getting page text: {e}")
            return None