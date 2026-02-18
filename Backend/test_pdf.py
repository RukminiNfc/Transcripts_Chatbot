"""
Test the new section-based chunking
"""

from app.utils.pdf_processor import PDFProcessor
from app.utils.chunker import TextChunker
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_section_chunking():
    # Process the uploaded PDF
    pdf_path = r"C:\Users\nfc\Desktop\AI_Projects\INTA_RAG_POC\data\pdfs\Single Jurisdiction Report 1.pdf"
    
    # Extract text
    pdf_processor = PDFProcessor()
    result = pdf_processor.extract_text(pdf_path)
    
    if not result['success']:
        logger.error("Failed to extract PDF")
        return
    
    # Combine all pages
    full_text = "\n\n".join([page['text'] for page in result['pages']])
    
    # Chunk with section detection
    chunker = TextChunker(use_section_chunking=True)
    chunks = chunker.chunk_text(full_text)
    
    # Display results
    logger.info(f"\n{'='*80}")
    logger.info(f"SECTION-BASED CHUNKING RESULTS")
    logger.info(f"{'='*80}\n")
    logger.info(f"Total chunks created: {len(chunks)}\n")
    
    for i, chunk in enumerate(chunks[:10], 1):  # Show first 10
        logger.info(f"\n--- Chunk {i} ---")
        logger.info(f"Section Title: {chunk.get('section_title', 'N/A')}")
        logger.info(f"Level 1: {chunk.get('level1_section', 'N/A')}")
        logger.info(f"Level 2: {chunk.get('level2_section', 'N/A')}")
        logger.info(f"Tokens: {chunk['token_count']}")
        logger.info(f"Preview: {chunk['text'][:200]}...")
        logger.info(f"{'-'*80}")
    
    # Show statistics
    logger.info(f"\n{'='*80}")
    logger.info("STATISTICS")
    logger.info(f"{'='*80}")
    
    avg_tokens = sum(c['token_count'] for c in chunks) / len(chunks)
    max_tokens = max(c['token_count'] for c in chunks)
    min_tokens = min(c['token_count'] for c in chunks)
    
    logger.info(f"Average chunk size: {avg_tokens:.0f} tokens")
    logger.info(f"Largest chunk: {max_tokens} tokens")
    logger.info(f"Smallest chunk: {min_tokens} tokens")
    
    # Show all sections detected
    logger.info(f"\n{'='*80}")
    logger.info("ALL SECTIONS DETECTED")
    logger.info(f"{'='*80}")
    
    unique_sections = set()
    for chunk in chunks:
        if chunk.get('section_title'):
            unique_sections.add(chunk['section_title'])
    
    for section in sorted(unique_sections):
        logger.info(f"  • {section}")

if __name__ == "__main__":
    test_section_chunking()