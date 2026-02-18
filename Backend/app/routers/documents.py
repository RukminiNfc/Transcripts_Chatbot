from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.document_service import DocumentService
from app.models.schemas import DocumentResponse
from typing import List, Optional
import shutil
from pathlib import Path
import logging
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

# Storage directory
UPLOAD_DIR = Path("../data/pdfs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    guide_type: Optional[str] = Form(None),
    jurisdiction: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Upload and process a PDF document
    
    Args:
        file: PDF file
        guide_type: Category (Traffic Rules, Legal, etc.)
        jurisdiction: Country (USA, India, etc.)
    """
    # Validate file type
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        # Save uploaded file
        file_path = UPLOAD_DIR / f"{uuid.uuid4()}_{file.filename}"
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        logger.info(f"Saved file: {file_path}")
        
        # Process document
        doc_service = DocumentService()
        document = doc_service.process_document(
            db=db,
            file_path=str(file_path),
            guide_type=guide_type,
            jurisdiction=jurisdiction
        )
        
        if not document:
            raise HTTPException(status_code=500, detail="Failed to process document")
        
        return document
        
    except Exception as e:
        logger.error(f"Error uploading document: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=List[DocumentResponse])
def list_documents(
    jurisdiction: Optional[str] = None,
    guide_type: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List all documents with optional filters"""
    doc_service = DocumentService()
    documents = doc_service.list_documents(
        db=db,
        jurisdiction=jurisdiction,
        guide_type=guide_type,
        status=status
    )
    return documents

@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    """Get document by ID"""
    doc_service = DocumentService()
    document = doc_service.get_document(db, document_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    return document

@router.delete("/{document_id}")
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    """Delete document"""
    doc_service = DocumentService()
    success = doc_service.delete_document(db, document_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    
    return {"message": "Document deleted successfully"}


@router.get("/{document_id}/download")
def download_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    """Download PDF document"""
    from fastapi.responses import FileResponse
    
    doc_service = DocumentService()
    document = doc_service.get_document(db, document_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    file_path = Path(document.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server")
    
    return FileResponse(
        path=str(file_path),
        filename=document.filename,
        media_type='application/pdf'
    )