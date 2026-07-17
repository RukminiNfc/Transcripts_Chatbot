from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, ARRAY, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()

class Transcript(Base):
    """Uploaded Transcript metadata"""
    __tablename__ = "transcripts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), nullable=False)
    filename = Column(String(255), nullable=False)
    session_name = Column(String(255), nullable=False)
    call_date = Column(DateTime(timezone=True), nullable=False)
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(50), default="processed") # processed, failed, processing
    celery_task_id = Column(String(255))
    processing_summary = Column(JSON) # To hold {added: X, modified: Y}
    file_hash = Column(String(64), unique=True, index=True) # SHA-256 duplicate check
    total_blocks = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ChatSession(Base):
    """Chat conversation sessions"""
    __tablename__ = "chat_sessions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(100))  # For future use
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    last_activity = Column(DateTime(timezone=True), server_default=func.now())
    conversation_history = Column(JSON)

class QueryLog(Base):
    """Analytics and query logging"""
    __tablename__ = "query_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True))
    query = Column(Text, nullable=False)
    intent = Column(JSON)
    results_count = Column(Integer)
    response_time_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# --- Requirement Tracking Models ---

class Customer(Base):
    """Client/Project tracking"""
    __tablename__ = "customers"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    client_speaker_name = Column(String(255), nullable=False) # e.g., "Prasad Kadrikar"
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class TeamSubscription(Base):
    """Who gets notified when requirements change"""
    __tablename__ = "team_subscriptions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), nullable=False)
    member_name = Column(String(255))
    email_address = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)

class Requirement(Base):
    """The current active state of a requirement"""
    __tablename__ = "requirements"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), nullable=False)
    category = Column(String(255))
    sub_category = Column(String(255))
    current_text = Column(Text, nullable=False)
    canonical_text = Column(Text)          # Normalized version for semantic comparison
    status = Column(String(50), default="active")  # active | removed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class RequirementVersion(Base):
    """Audit log of every requirement change"""
    __tablename__ = "requirement_versions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requirement_id = Column(UUID(as_uuid=True), nullable=False)
    version_number = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    change_type = Column(String(50))       # added | modified | unchanged | removed
    confirmed_by = Column(String(255))
    proposed_by = Column(String(255))
    discussed_date = Column(DateTime(timezone=True))
    session = Column(String(255))
    transcript_id = Column(UUID(as_uuid=True))  # Link to Document
    vector_id = Column(String(100))             # Qdrant point ID
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ConversationLog(Base):
    """Store 1: Full conversation log in PostgreSQL (for redundancy/relational queries)"""
    __tablename__ = "conversation_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcript_id = Column(UUID(as_uuid=True))
    customer_id = Column(UUID(as_uuid=True))
    speaker = Column(String(255))
    role = Column(String(50)) # client, team_member
    text = Column(Text)
    call_timestamp = Column(String(50)) # e.g. "4:49"
    created_at = Column(DateTime(timezone=True), server_default=func.now())