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


# ─── Module documents ────────────────────────────────────────────────────────
# One living document per product area. The list view deliberately excludes the body: these
# documents run to 14,000+ characters, and the page that lists fourteen of them needs none of it.

class ModuleListItem(BaseModel):
    """Row in the Documents list."""
    id: UUID
    module_name: str
    description: Optional[str] = None
    status: Optional[str] = None
    current_version: Optional[str] = None
    has_content: bool = False
    # Counts, so the list can be useful without loading any document text.
    requirement_count: int = 0
    revision_count: int = 0
    # The most recent MEETING that changed this document, which is what "last updated" means to
    # a reader. `last_synced_at` is when the pipeline last wrote it — the same day for every
    # module after a backfill, and so useless for telling which areas are moving.
    last_call_date: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
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


class ModuleResponse(ModuleListItem):
    """One module including the full document."""
    content_markdown: Optional[str] = None


class ModuleRevisionListItem(BaseModel):
    """An entry in a document's version history."""
    id: UUID
    version_label: Optional[str] = None
    session_name: Optional[str] = None
    transcript_id: Optional[UUID] = None
    revision_summary: Optional[str] = None
    change_count: int = 0
    # When the MEETING happened, looked up from the transcript. This is what the history should
    # be read by: `created_at` is when the pipeline ran, which for a backfill is the same day for
    # every revision and puts the project's timeline in the wrong order.
    call_date: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ModuleRevisionResponse(ModuleRevisionListItem):
    """
    One past version, with everything needed to show what changed.

    `line_diffs` is the reconciler's own account — which bullet changed and what it said before.
    `diff_lines` is computed against the previous snapshot for the red/green view, because a
    reader wants to see the document move, not just read a list of edits.
    """
    content_snapshot: Optional[str] = None
    line_diffs: List[dict] = []
    diff_lines: List[dict] = []
    previous_version: Optional[str] = None


class ModuleUpdate(BaseModel):
    """Body for correcting a document by hand."""
    content_markdown: str = Field(min_length=1)
    revision_summary: Optional[str] = None


class ModuleMergeRequest(BaseModel):
    """Payload to merge an unapproved proposed module into an existing active module."""
    target_module_id: UUID

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
