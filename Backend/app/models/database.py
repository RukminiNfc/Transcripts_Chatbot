from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, ARRAY, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()

class Document(Base):
    """Document metadata table"""
    __tablename__ = "documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(255), nullable=False)
    title = Column(String(500))
    guide_type = Column(String(100))  # "Traffic Rules", "Legal", etc.
    jurisdiction = Column(String(100))  # "USA", "India", etc.
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(50), default="pending")  # pending, processing, published
    file_path = Column(Text)
    total_pages = Column(Integer)
    total_chunks = Column(Integer)
    metadata_json = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class Chunk(Base):
    """Chunk reference table (actual vectors in Qdrant)"""
    __tablename__ = "chunks"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    page_number = Column(Integer)
    section_title = Column(String(500))
    level1_section = Column(String(255))  # Top-level section (e.g., "I. General")
    level2_section = Column(String(500))  
    chunk_text = Column(Text, nullable=False)
    token_count = Column(Integer)
    vector_id = Column(String(100))  # Qdrant point ID
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ChatSession(Base):
    """Chat conversation sessions"""
    __tablename__ = "chat_sessions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(100))  # For future use
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    last_activity = Column(DateTime(timezone=True), server_default=func.now())
    locked_document_id = Column(UUID(as_uuid=True))
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