from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

# Chat Schemas
class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    timestamp: Optional[datetime] = None

class ChatRequest(BaseModel):
    query: str
    session_id: Optional[UUID] = None
    filters: Optional[Dict[str, Any]] = None

# Source returned by chat - flexible to handle both conversation and requirement results
class SourceCitation(BaseModel):
    type: str  # "conversation" or "requirement"
    # Conversation fields
    speaker: Optional[str] = None
    timestamp: Optional[str] = None
    session: Optional[str] = None
    # Requirement fields
    category: Optional[str] = None
    sub_category: Optional[str] = None
    change_type: Optional[str] = None
    confirmed_by: Optional[str] = None
    # Shared text field
    text: Optional[str] = None

class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceCitation]
    session_id: UUID
    response_time_ms: int
    context_metadata: Optional[Dict[str, Any]] = None

# Search Schemas
class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)

# --- Requirement Tracking Schemas ---

class CustomerCreate(BaseModel):
    name: str
    client_speaker_name: str

class TranscriptUploadRequest(BaseModel):
    customer_id: UUID
    session_name: str
    call_date: datetime

class RequirementResponse(BaseModel):
    id: UUID
    category: Optional[str]
    sub_category: Optional[str]
    current_text: str
    status: str
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

class RequirementVersionResponse(BaseModel):
    id: UUID
    version_number: int
    text: str
    change_type: str
    confirmed_by: Optional[str]
    proposed_by: Optional[str]
    discussed_date: Optional[datetime]
    session: Optional[str]

    class Config:
        from_attributes = True


# ── Minutes of Meeting ───────────────────────────────────────────────────────

class MOMListItem(BaseModel):
    """Row in the Minutes list — everything except the document body, which can be very long."""
    id: UUID
    transcript_id: Optional[UUID]
    customer_id: Optional[UUID]
    session_name: Optional[str]
    call_date: Optional[datetime]
    version: Optional[int]
    status: Optional[str]
    truncated: Optional[bool]
    model_used: Optional[str]
    generation_error: Optional[str]
    email_sent_at: Optional[datetime]
    email_error: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class MOMResponse(MOMListItem):
    """A single MOM including the full markdown body."""
    content_markdown: Optional[str]
    prompt_name: Optional[str]
    email_recipients: Optional[List[str]]
    # Set once a human has corrected the draft. The UI shows "Edited on ..." so a reader can
    # tell model output from a document someone has been through.
    edited_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MOMUpdate(BaseModel):
    """Body for correcting a draft before it is sent."""
    content_markdown: str = Field(min_length=1)


class MOMSendResult(BaseModel):
    id: UUID
    status: str
    recipients: List[str] = []
    error: Optional[str] = None