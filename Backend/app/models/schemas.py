from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, date
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
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class MOMResponse(MOMListItem):
    """A single MOM including the full markdown body."""
    content_markdown: Optional[str]
    prompt_name: Optional[str]
    email_recipients: Optional[List[str]]

    class Config:
        from_attributes = True


class MOMSendResult(BaseModel):
    id: UUID
    status: str
    recipients: List[str] = []
    error: Optional[str] = None


# ── MOM action items → tracker (Azure Boards | Jira) ─────────────────────────

class ActionItemOut(BaseModel):
    id: UUID
    mom_id: UUID
    area: Optional[str]
    title: str
    description: Optional[str]
    owner_name: Optional[str]
    assignee_email: Optional[str]
    due_text: Optional[str]
    due_date: Optional[date]
    push_status: str
    push_error: Optional[str]
    tracker: Optional[str]                  # where it was created: "ado" | "jira"
    external_key: Optional[str]             # "1226" or "CAL-123"
    external_url: Optional[str]

    class Config:
        from_attributes = True


class ActionItemUpdate(BaseModel):
    """PATCH body — only the fields sent are changed. Send assignee_email/due_date as null to clear."""
    title: Optional[str] = None
    description: Optional[str] = None
    area: Optional[str] = None
    assignee_email: Optional[str] = None
    due_date: Optional[date] = None


class ActionItemsBulkDelete(BaseModel):
    ids: List[UUID] = Field(..., min_length=1, max_length=500)


class ActionItemsBulkDeleteResult(BaseModel):
    deleted: List[UUID]
    skipped: List[UUID]                     # not found, or no longer editable (already in the tracker)


class AssigneeOption(BaseModel):
    """An active team_subscriptions row offered in the assignee dropdown."""
    name: Optional[str]
    email: str


class ActionItemsReview(BaseModel):
    """Everything the review screen needs in one call."""
    mom_id: UUID
    approved_at: Optional[datetime]
    approved_by: Optional[str]
    tracker: Optional[str]                  # customer's tracker: "ado" | "jira" | None
    tracker_configured: bool                # that tracker is enabled AND its target is set
    items: List[ActionItemOut]
    assignees: List[AssigneeOption]


class ApproveResult(BaseModel):
    mom_id: UUID
    approved_at: Optional[datetime]
    approved_by: Optional[str]
    created: int
    failed: int
    items: List[ActionItemOut]