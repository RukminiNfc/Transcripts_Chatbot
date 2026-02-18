from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.chat_service import ChatService
from app.models.schemas import ChatRequest, ChatResponse
import logging
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

@router.post("/", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db)
):
    """
    Process chat query
    
    Args:
        request: Chat request with query and optional session_id
        
    Returns:
        Chat response with answer and sources
    """
    try:
        chat_service = ChatService()
        
        # Get or create session
        session_id = request.session_id or uuid.uuid4()
        
        # Process query
        response = chat_service.process_query(
            db=db,
            session_id=session_id,
            query=request.query,
            filters=request.filters
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/lock-document/{session_id}/{document_id}")
def lock_document(
    session_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    """Lock conversation to specific document"""
    chat_service = ChatService()
    session = chat_service.get_session(db, session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    chat_service.lock_document(db, session, document_id)
    return {"message": "Document locked to conversation"}

@router.post("/unlock-document/{session_id}")
def unlock_document(
    session_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    """Unlock document from conversation"""
    chat_service = ChatService()
    session = chat_service.get_session(db, session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    chat_service.unlock_document(db, session)
    return {"message": "Document unlocked from conversation"}