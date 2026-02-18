from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

# Document Schemas
class DocumentCreate(BaseModel):
    filename: str
    title: Optional[str] = None
    guide_type: Optional[str] = None
    jurisdiction: Optional[str] = None
    file_path: str

class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    guide_type: Optional[str] = None
    jurisdiction: Optional[str] = None
    status: Optional[str] = None

class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    title: Optional[str]
    guide_type: Optional[str]
    jurisdiction: Optional[str]
    status: str
    total_pages: Optional[int]
    total_chunks: Optional[int]
    upload_date: datetime
    
    class Config:
        from_attributes = True

# Chat Schemas
class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    timestamp: Optional[datetime] = None

class ChatRequest(BaseModel):
    query: str
    session_id: Optional[UUID] = None
    filters: Optional[Dict[str, Any]] = None

class SourceCitation(BaseModel):
    document_id: Optional[UUID] = None
    document_title: str
    page_number: int
    chunk_text: str
    jurisdiction: Optional[str] = None
    guide_type: Optional[str] = None

# Context Metadata - inferred from search results (no extra LLM call)
class GuideSuggestion(BaseModel):
    label: str
    icon: str
    action: str

class TopicSuggestion(BaseModel):
    label: str
    question: str

class ContextMetadata(BaseModel):
    query_type: str  # "country_specific" or "general"
    detected_country: Optional[str] = None
    suggested_topics: List[TopicSuggestion] = []
    guide_suggestions: Optional[List[GuideSuggestion]] = None
    detected_guide_type: Optional[str] = None

class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceCitation]
    session_id: UUID
    response_time_ms: int
    context_metadata: Optional[ContextMetadata] = None

# Search Schemas
class SearchRequest(BaseModel):
    query: str
    jurisdiction: Optional[str] = None
    guide_type: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=20)

class SearchResult(BaseModel):
    chunk_text: str
    document_title: str
    page_number: int
    jurisdiction: Optional[str]
    guide_type: Optional[str]
    score: float