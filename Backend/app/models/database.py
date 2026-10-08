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

    # Raw extraction output, saved BEFORE comparison runs.
    #
    # Comparison is the only thing that writes `requirements` rows, so without this the
    # extracted requirements exist only as a local variable inside the worker — if comparison
    # is skipped, disabled or crashes, that work is gone and re-running it costs another LLM
    # pass over the whole transcript.
    #
    # Storing it makes comparison REPLAYABLE: a better matching algorithm can be re-run over
    # the same stored extractions as often as needed, paying only for comparison. The wrapper
    # records which prompt and model produced the rows, because replaying is only valid while
    # those are unchanged.
    #
    #   {"extracted_at": iso8601, "prompt_file": str, "model": str,
    #    "count": int, "requirements": [ ... ]}
    extracted_requirements = Column(JSON)

    # Comparison tracked SEPARATELY from `status`, which stays the ingestion state the admin UI
    # polls on ("processing" -> "processed"). Overloading it would break that poll.
    #
    #   pending   extracted, comparison not yet run
    #   comparing in progress
    #   compared  done
    #   failed    comparison errored; the transcript itself is still fine
    comparison_status = Column(String(50), default="pending")

    # Which comparison algorithm produced this transcript's requirements (e.g. "v1-gates").
    # Replaying stored extractions through a new matcher rewrites history; without this there is
    # no way to tell which rows came from which algorithm, so a rebuild cannot be compared
    # against the run it replaced, or rolled back selectively.
    comparison_version = Column(String(50))

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


class MeetingMinutes(Base):
    """Generated Minutes of Meeting for one processed transcript.

    Purely additive — nothing else in the schema references this table, and nothing about the
    requirement pipeline depends on it. A row per GENERATION, not per transcript: regenerating
    after a prompt change inserts a new `version` rather than overwriting, so two prompts can be
    compared on the same call.

    `content_markdown` is the source of truth. HTML is rendered at send time, so improving the
    email template never requires regenerating content.
    """
    __tablename__ = "meeting_minutes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcript_id = Column(UUID(as_uuid=True), index=True)  # the call these minutes are for
    customer_id = Column(UUID(as_uuid=True), index=True)
    session_name = Column(String(255))
    call_date = Column(DateTime(timezone=True))
    version = Column(Integer, default=1)          # 1..n per transcript_id

    content_markdown = Column(Text)               # the generated document — what gets sent

    # What the model originally produced, copied here the FIRST time a human edits the document
    # and never touched again. Edits are corrections, not new versions, so content_markdown is
    # overwritten in place — but without this the model's own output is lost every time someone
    # fixes a mis-transcribed word. Keeping it is what makes "is MOM quality improving?"
    # answerable: compare original_markdown against what was actually sent.
    original_markdown = Column(Text)
    edited_at = Column(DateTime(timezone=True))   # NULL until a human edits it

    status = Column(String(50), default="draft")  # draft | sent | send_failed

    model_used = Column(String(100))              # may differ from configured model after fallback
    prompt_name = Column(String(100))             # which prompt file produced this version
    truncated = Column(Boolean, default=False)    # model hit its token cap: document INCOMPLETE
    generation_error = Column(Text)               # set instead of content when generation failed

    email_recipients = Column(JSON)               # who it was actually sent to
    email_sent_at = Column(DateTime(timezone=True))
    email_error = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """Application login account with role-based access.

    role = "admin"  -> may use Chat + Requirements + Admin, and create other users.
    role = "user"   -> may use Chat only.
    Passwords are stored HASHED (bcrypt) via app.core.security — never in plain text.
    """
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user")  # "admin" | "user"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())