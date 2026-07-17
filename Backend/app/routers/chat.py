from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.chat_service import ChatService
from app.models.schemas import ChatRequest, ChatResponse
import json
import logging
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Process chat query
    """
    try:
        chat_service = ChatService()
        session_id = request.session_id or uuid.uuid4()
        
        response = await chat_service.process_query(
            db=db,
            session_id=session_id,
            query=request.query
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Process chat query with token-by-token streaming (Server-Sent Events).
    Emits `data: {"type":"delta","text":...}` events as the answer generates, then a final
    `data: {"type":"done", ...}` with session id, sources, and metadata.
    """
    chat_service = ChatService()
    session_id = request.session_id or uuid.uuid4()

    async def event_generator():
        try:
            async for event in chat_service.process_query_stream(
                db=db, session_id=session_id, query=request.query
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.error(f"Chat stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Failed to generate a response.'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )