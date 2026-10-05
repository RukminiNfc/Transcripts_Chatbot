from sqlalchemy import Column, String, Integer, DateTime, Date, Text, Boolean, ARRAY, JSON
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
    # Where this customer's approved MOM action items go: "ado" | "jira" | NULL (none). Approve is
    # refused until the chosen tracker's target is set — never guessed.
    tracker = Column(String(20))
    # Azure Boards target (tracker = "ado"): project + area path required.
    ado_project = Column(String(255))
    ado_area_path = Column(String(500))           # e.g. "IntelliStaff\Client-X"
    ado_iteration_path = Column(String(500))      # optional
    # Jira target (tracker = "jira"): the project key, e.g. "CAL".
    jira_project_key = Column(String(50))
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

    content_markdown = Column(Text)               # the generated document
    status = Column(String(50), default="draft")  # draft | sent | send_failed

    model_used = Column(String(100))              # may differ from configured model after fallback
    prompt_name = Column(String(100))             # which prompt file produced this version
    truncated = Column(Boolean, default=False)    # model hit its token cap: document INCOMPLETE
    generation_error = Column(Text)               # set instead of content when generation failed

    email_recipients = Column(JSON)               # who it was actually sent to
    email_sent_at = Column(DateTime(timezone=True))
    email_error = Column(Text)

    # Approval is tracked separately from `status` (which records the EMAIL outcome), so sending
    # an approved MOM never erases the fact that it was approved.
    approved_at = Column(DateTime(timezone=True))
    approved_by = Column(String(100))             # username of the approving admin
    # When action items were parsed into mom_action_items. Set once, so deleting every draft item
    # does not make the next review re-extract them.
    items_extracted_at = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MOMActionItem(Base):
    """One action item extracted from a MOM, reviewed by an admin, then pushed to a tracker.

    Rows are created as `draft` the first time the review screen opens (parsed from the MOM's
    "Consolidated Action Items" section). While draft they can be edited or deleted. Approving the
    MOM creates one Task per remaining row in the customer's tracker (Azure Boards or Jira); after
    that the row is locked.
    """
    __tablename__ = "mom_action_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mom_id = Column(UUID(as_uuid=True), index=True, nullable=False)

    area = Column(String(255))                    # the "### Area" heading it sat under
    title = Column(Text, nullable=False)
    description = Column(Text)
    owner_name = Column(String(255))              # owner exactly as the MOM wrote it
    assignee_email = Column(String(255))          # chosen by the admin; NULL = unassigned
    due_text = Column(String(255))                # due exactly as the MOM wrote it
    due_date = Column(Date)                       # parsed or admin-set; NULL = no due date

    # draft | pushing | created | failed. `pushing` is claimed atomically before the tracker call so
    # a double-clicked Approve/Retry can never create the same Task twice.
    push_status = Column(String(20), default="draft")
    push_error = Column(Text)
    # Set once created. Tracker-neutral: an ADO work item id ("1226") or a Jira key ("CAL-123").
    tracker = Column(String(20))                  # "ado" | "jira" — where it was created
    external_key = Column(String(50))
    external_url = Column(String(500))

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