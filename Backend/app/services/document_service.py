from sqlalchemy.orm import Session
from app.models.database import Document, Chunk
from app.models.schemas import DocumentCreate
from app.utils.pdf_processor import PDFProcessor
from app.utils.chunker import TextChunker
from app.services.embedding_service import EmbeddingService
from app.core.qdrant_client import QdrantVectorDB
from pathlib import Path
import logging
import uuid
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

class DocumentService:
    """Service for document processing and management"""
    
    def __init__(self):
        self.pdf_processor = PDFProcessor()
        self.chunker = TextChunker()
        self.embedding_service = EmbeddingService()
        self.vector_db = QdrantVectorDB()
    
    def process_document(
        self,
        db: Session,
        file_path: str,
        guide_type: Optional[str] = None,
        jurisdiction: Optional[str] = None
    ) -> Optional[Document]:
        """
        Process PDF document: extract text, chunk, embed, and store
        
        Args:
            db: Database session
            file_path: Path to PDF file
            guide_type: Category (Traffic Rules, Legal, etc.)
            jurisdiction: Country (USA, India, etc.)
            
        Returns:
            Created Document object
        """
        try:
            logger.info(f"Processing document: {file_path}")
            
            # 1. Extract text from PDF
            pdf_result = self.pdf_processor.extract_text(file_path)
            if not pdf_result['success']:
                logger.error(f"Failed to extract PDF: {pdf_result.get('error')}")
                return None
            
            # 2. Create document record
            document = Document(
                filename=pdf_result['metadata']['filename'],
                title=pdf_result['metadata'].get('title') or Path(file_path).stem,
                guide_type=guide_type,
                jurisdiction=jurisdiction,
                file_path=file_path,
                total_pages=pdf_result['metadata']['total_pages'],
                status='processing'
            )
            db.add(document)
            db.commit()
            db.refresh(document)
            
            logger.info(f"Created document record: {document.id}")
            
            # 3. Chunk all pages with section detection
            all_chunks = []
            chunk_index = 0

            for page_data in pdf_result['pages']:
                page_chunks = self.chunker.chunk_text(
                    page_data['text'],
                    metadata={
                        'page_number': page_data['page_number'],
                        'document_id': str(document.id)
                    }
                )
                
                for chunk in page_chunks:
                    chunk['chunk_index'] = chunk_index
                    chunk_index += 1
                    all_chunks.append(chunk)
            
            # 4. Generate embeddings
            chunk_texts = [chunk['text'] for chunk in all_chunks]
            embeddings = self.embedding_service.generate_embeddings(chunk_texts)
            
            logger.info(f"Generated {len(embeddings)} embeddings")
            
            # 5. Prepare vector payloads (UPDATED)
            vector_ids = []
            payloads = []
            for chunk, embedding in zip(all_chunks, embeddings):
                vector_id = str(uuid.uuid4())
                vector_ids.append(vector_id)
                
                payload = {
                    'document_id': str(document.id),
                    'chunk_index': chunk['chunk_index'],
                    'page_number': chunk.get('page_number'),
                    'chunk_text': chunk['text'],
                    'document_title': document.title,
                    'jurisdiction': jurisdiction,
                    'guide_type': guide_type,
                    'filename': document.filename,
                    
                    # NEW: Section metadata
                    'section_title': chunk.get('section_title'),
                    'level1_section': chunk.get('level1_section'),
                    'level2_section': chunk.get('level2_section'),
                }
                payloads.append(payload)
                
                # Create chunk record in database (UPDATED)
                chunk_record = Chunk(
                    document_id=document.id,
                    chunk_index=chunk['chunk_index'],
                    page_number=chunk.get('page_number'),
                    section_title=chunk.get('section_title'),
                    level1_section=chunk.get('level1_section'),
                    level2_section=chunk.get('level2_section'),
                    chunk_text=chunk['text'],
                    token_count=chunk['token_count'],
                    vector_id=vector_id
                )
                db.add(chunk_record)
                        
            # 6. Store in vector database
            success = self.vector_db.add_vectors(
                vectors=embeddings,
                payloads=payloads,
                ids=vector_ids
            )
            
            if not success:
                document.status = 'failed'
                db.commit()
                return document
            
            # 7. Update document status
            document.total_chunks = len(all_chunks)
            document.status = 'published'
            db.commit()
            db.refresh(document)
            
            logger.info(f"Document processed successfully: {document.id}")
            return document
            
        except Exception as e:
            logger.error(f"Error processing document: {e}")
            if document:
                document.status = 'failed'
                db.commit()
            return None
    
    def get_document(self, db: Session, document_id: uuid.UUID) -> Optional[Document]:
        """Get document by ID"""
        return db.query(Document).filter(Document.id == document_id).first()
    
    def list_documents(
        self,
        db: Session,
        jurisdiction: Optional[str] = None,
        guide_type: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Document]:
        """List documents with optional filters"""
        query = db.query(Document)
        
        if jurisdiction:
            query = query.filter(Document.jurisdiction == jurisdiction)
        if guide_type:
            query = query.filter(Document.guide_type == guide_type)
        if status:
            query = query.filter(Document.status == status)
        
        return query.order_by(Document.upload_date.desc()).all()
    
    def delete_document(self, db: Session, document_id: uuid.UUID) -> bool:
        """Delete document and its vectors"""
        try:
            document = self.get_document(db, document_id)
            if not document:
                return False
            
            # Delete from vector database
            self.vector_db.delete_by_document_id(str(document_id))
            
            # Delete from PostgreSQL
            db.query(Chunk).filter(Chunk.document_id == document_id).delete()
            db.delete(document)
            db.commit()
            
            logger.info(f"Deleted document: {document_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting document: {e}")
            db.rollback()
            return False