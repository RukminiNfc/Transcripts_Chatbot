import tiktoken
import re
from typing import List, Dict, Tuple
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class TextChunker:
    """
    Intelligent chunker that detects sections and creates chunks based on document structure
    """
    
    def __init__(
        self,
        chunk_size: int = settings.CHUNK_SIZE,
        chunk_overlap: int = settings.CHUNK_OVERLAP,
        use_section_chunking: bool = True
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.use_section_chunking = use_section_chunking
        self.encoding = tiktoken.get_encoding("cl100k_base")
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text"""
        return len(self.encoding.encode(text))
    
    def detect_section_pattern(self, text: str) -> str:
        """
        Detect the section heading pattern used in the document
        
        Common patterns:
        - "I. General — A. Rights Afforded by Registration"
        - "1. Introduction"
        - "Section 1: Overview"
        - "Article 5.2.3"
        """
        patterns = [
            # Roman numerals with letters (INTA style)
            r'^([IVX]+)\.\s+([^—\n]+)\s*—\s*([A-Z])\.\s*(.+)$',
            
            # Roman numerals only
            r'^([IVX]+)\.\s+(.+)$',
            
            # Numbers with letters
            r'^(\d+)\.\s+([^—\n]+)\s*—\s*([A-Z])\.\s*(.+)$',
            
            # Simple numbered sections
            r'^(\d+)\.\s+(.+)$',
            
            # Article/Section format
            r'^(Article|Section)\s+(\d+[\.\d]*):?\s*(.+)$',
        ]
        
        for line in text.split('\n')[:50]:  # Check first 50 lines
            line = line.strip()
            for pattern in patterns:
                if re.match(pattern, line):
                    return pattern
        
        return None
    
    def extract_sections(self, text: str) -> List[Dict]:
        """
        Extract sections based on detected heading pattern
        
        Returns list of sections with hierarchy
        """
        pattern = self.detect_section_pattern(text)
        
        if not pattern:
            logger.info("No section pattern detected, using paragraph-based chunking")
            return self._fallback_chunking(text)
        
        logger.info(f"Detected section pattern: {pattern}")
        
        sections = []
        lines = text.split('\n')
        current_section = None
        current_content = []
        
        # Track section hierarchy
        current_level1 = None  # e.g., "I. General"
        current_level2 = None  # e.g., "A. Rights Afforded by Registration"
        
        for line in lines:
            line_stripped = line.strip()
            
            if not line_stripped:
                if current_content:
                    current_content.append(line)
                continue
            
            # Check if this line is a section heading
            match = re.match(pattern, line_stripped)
            
            if match:
                # Save previous section if exists
                if current_section and current_content:
                    section_text = '\n'.join(current_content).strip()
                    if section_text:
                        sections.append({
                            'section_title': current_section,
                            'level1_section': current_level1,
                            'level2_section': current_level2,
                            'text': section_text,
                            'token_count': self.count_tokens(section_text)
                        })
                
                # Start new section
                current_section = line_stripped
                current_content = []
                
                # Determine hierarchy level
                if '—' in line_stripped:
                    # Two-level hierarchy (e.g., "I. General — A. Rights")
                    parts = line_stripped.split('—')
                    current_level1 = parts[0].strip()
                    current_level2 = line_stripped
                else:
                    # Single level
                    current_level1 = line_stripped
                    current_level2 = None
                
            else:
                # Regular content line
                if current_section:
                    current_content.append(line)
                else:
                    # Content before first section (like headers)
                    if not current_content:
                        current_section = "Document Header"
                        current_level1 = "Document Header"
                    current_content.append(line)
        
        # Add final section
        if current_section and current_content:
            section_text = '\n'.join(current_content).strip()
            if section_text:
                sections.append({
                    'section_title': current_section,
                    'level1_section': current_level1,
                    'level2_section': current_level2,
                    'text': section_text,
                    'token_count': self.count_tokens(section_text)
                })
        
        logger.info(f"Extracted {len(sections)} sections")
        return sections
    
    def _fallback_chunking(self, text: str) -> List[Dict]:
        """
        Fallback to paragraph-based chunking if no sections detected
        """
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        
        chunks = []
        current_chunk = ""
        current_tokens = 0
        chunk_index = 0
        
        for para in paragraphs:
            para_tokens = self.count_tokens(para)
            
            if current_tokens + para_tokens <= self.chunk_size:
                current_chunk += para + "\n\n"
                current_tokens += para_tokens
            else:
                # Save current chunk
                if current_chunk.strip():
                    chunks.append({
                        'section_title': f"Chunk {chunk_index + 1}",
                        'level1_section': None,
                        'level2_section': None,
                        'text': current_chunk.strip(),
                        'token_count': current_tokens
                    })
                    chunk_index += 1
                
                # Start new chunk
                current_chunk = para + "\n\n"
                current_tokens = para_tokens
        
        # Add final chunk
        if current_chunk.strip():
            chunks.append({
                'section_title': f"Chunk {chunk_index + 1}",
                'level1_section': None,
                'level2_section': None,
                'text': current_chunk.strip(),
                'token_count': current_tokens
            })
        
        return chunks
    
    def split_large_section(self, section: Dict) -> List[Dict]:
        """
        Split sections that exceed max token size
        Uses paragraph boundaries to split naturally
        """
        if section['token_count'] <= self.chunk_size * 2:
            return [section]
        
        logger.info(f"Splitting large section: {section['section_title']} ({section['token_count']} tokens)")
        
        # Split by paragraphs
        paragraphs = section['text'].split('\n\n')
        
        sub_chunks = []
        current_chunk = ""
        current_tokens = 0
        sub_index = 1
        
        for para in paragraphs:
            para_tokens = self.count_tokens(para)
            
            if current_tokens + para_tokens <= self.chunk_size:
                current_chunk += para + "\n\n"
                current_tokens += para_tokens
            else:
                # Save current sub-chunk
                if current_chunk.strip():
                    sub_chunks.append({
                        'section_title': f"{section['section_title']} (Part {sub_index})",
                        'level1_section': section['level1_section'],
                        'level2_section': section['level2_section'],
                        'text': current_chunk.strip(),
                        'token_count': current_tokens
                    })
                    sub_index += 1
                
                # Start new sub-chunk with overlap
                overlap_text = self._get_overlap(current_chunk)
                current_chunk = overlap_text + para + "\n\n"
                current_tokens = self.count_tokens(current_chunk)
        
        # Add final sub-chunk
        if current_chunk.strip():
            sub_chunks.append({
                'section_title': f"{section['section_title']} (Part {sub_index})",
                'level1_section': section['level1_section'],
                'level2_section': section['level2_section'],
                'text': current_chunk.strip(),
                'token_count': current_tokens
            })
        
        logger.info(f"Split into {len(sub_chunks)} sub-chunks")
        return sub_chunks
    
    def chunk_text(
        self,
        text: str,
        metadata: Dict = None
    ) -> List[Dict]:
        """
        Main chunking method - uses section-based chunking when possible
        
        Args:
            text: Text to chunk
            metadata: Optional metadata to attach to each chunk
            
        Returns:
            List of chunk dictionaries
        """
        if not text or not text.strip():
            return []
        
        if self.use_section_chunking:
            # Extract sections
            sections = self.extract_sections(text)
            
            # Split large sections if needed
            final_chunks = []
            for section in sections:
                if section['token_count'] > self.chunk_size * 2:
                    sub_chunks = self.split_large_section(section)
                    final_chunks.extend(sub_chunks)
                else:
                    final_chunks.append(section)
            
            # Add metadata and indices
            for idx, chunk in enumerate(final_chunks):
                chunk['chunk_index'] = idx
                if metadata:
                    chunk.update(metadata)
            
            logger.info(f"Created {len(final_chunks)} section-based chunks")
            return final_chunks
        
        else:
            # Use original token-based chunking
            return self._token_based_chunking(text, metadata)
    
    def _token_based_chunking(self, text: str, metadata: Dict = None) -> List[Dict]:
        """
        Original token-based chunking (fallback method)
        """
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        
        chunks = []
        current_chunk = ""
        current_tokens = 0
        chunk_index = 0
        
        for paragraph in paragraphs:
            paragraph_tokens = self.count_tokens(paragraph)
            
            if current_tokens + paragraph_tokens <= self.chunk_size:
                current_chunk += paragraph + "\n\n"
                current_tokens += paragraph_tokens
            else:
                # Save current chunk
                if current_chunk.strip():
                    chunk = {
                        'chunk_index': chunk_index,
                        'text': current_chunk.strip(),
                        'token_count': current_tokens,
                        'section_title': f"Chunk {chunk_index + 1}"
                    }
                    if metadata:
                        chunk.update(metadata)
                    chunks.append(chunk)
                    chunk_index += 1
                
                # Start new chunk with overlap
                overlap_text = self._get_overlap(current_chunk)
                current_chunk = overlap_text + paragraph + "\n\n"
                current_tokens = self.count_tokens(current_chunk)
        
        # Add final chunk
        if current_chunk.strip():
            chunk = {
                'chunk_index': chunk_index,
                'text': current_chunk.strip(),
                'token_count': current_tokens,
                'section_title': f"Chunk {chunk_index + 1}"
            }
            if metadata:
                chunk.update(metadata)
            chunks.append(chunk)
        
        return chunks
    
    def _get_overlap(self, text: str) -> str:
        """Get overlap text from end of chunk"""
        if not text:
            return ""
        
        tokens = self.encoding.encode(text)
        if len(tokens) <= self.chunk_overlap:
            return text
        
        overlap_tokens = tokens[-self.chunk_overlap:]
        overlap_text = self.encoding.decode(overlap_tokens)
        
        return overlap_text