from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.chat_service import ChatService
from app.models.schemas import ChatRequest, ChatResponse
from app.core.security import get_current_user
from app.models.database import User
from pydantic import BaseModel
import json
import logging
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(get_current_user)])


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Process a chat query (session is owned by the logged-in user)."""
    try:
        chat_service = ChatService()
        session_id = request.session_id or uuid.uuid4()

        response = await chat_service.process_query(
            db=db,
            session_id=session_id,
            query=request.query,
            user_id=current_user.username,
        )
        return response

    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Process a chat query with token-by-token streaming (SSE). Session owned by the user."""
    chat_service = ChatService()
    session_id = request.session_id or uuid.uuid4()
    username = current_user.username

    async def event_generator():
        try:
            async for event in chat_service.process_query_stream(
                db=db, session_id=session_id, query=request.query, user_id=username
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


@router.get("/sessions")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's chat sessions (newest first) for the history sidebar."""
    chat_service = ChatService()
    return await chat_service.list_sessions(db, user_id=current_user.username)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Open one of the current user's chats (returns its messages). 404 if not theirs."""
    chat_service = ChatService()
    messages = await chat_service.get_session_messages(db, session_id, user_id=current_user.username)
    if messages is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"session_id": str(session_id), "messages": messages}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete one of the current user's chats. 404 if not theirs."""
    chat_service = ChatService()
    ok = await chat_service.delete_session(db, session_id, user_id=current_user.username)
    if not ok:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"deleted": True}


class RenameRequest(BaseModel):
    title: str


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: uuid.UUID,
    body: RenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename one of the current user's chats. 404 if not theirs or the title is empty."""
    chat_service = ChatService()
    ok = await chat_service.rename_session(
        db, session_id, user_id=current_user.username, new_title=body.title
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"renamed": True, "title": (body.title or "").strip()[:80]}
