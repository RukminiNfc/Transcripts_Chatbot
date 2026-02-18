from sqlalchemy.orm import Session
from app.models.database import ChatSession, QueryLog
from app.services.search_service import SearchService
from app.services.llm_service import LLMService
from app.services.query_processor import QueryProcessor
from app.services.context_inference import ContextInference
from typing import Optional, Dict, List
import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

class ChatService:
    """Manage chat conversations and context"""
    
    def __init__(self):
        self.search_service = SearchService()
        self.llm_service = LLMService()
        self.query_processor = QueryProcessor()
        self.context_inference = ContextInference()
    
    def create_session(self, db: Session, user_id: Optional[str] = None) -> ChatSession:
        """Create new chat session"""
        session = ChatSession(
            user_id=user_id,
            conversation_history={"messages": []}
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        
        logger.info(f"Created chat session: {session.id}")
        return session
    
    def get_session(self, db: Session, session_id: uuid.UUID) -> Optional[ChatSession]:
        """Get chat session by ID"""
        return db.query(ChatSession).filter(ChatSession.id == session_id).first()
    
    def update_session_activity(self, db: Session, session: ChatSession):
        """Update last activity timestamp"""
        session.last_activity = datetime.utcnow()
        db.commit()
    
    def lock_document(
        self,
        db: Session,
        session: ChatSession,
        document_id: uuid.UUID
    ):
        """Lock conversation to specific document"""
        session.locked_document_id = document_id
        db.commit()
        logger.info(f"Locked session {session.id} to document {document_id}")
    
    def unlock_document(self, db: Session, session: ChatSession):
        """Unlock document from conversation"""
        session.locked_document_id = None
        db.commit()
        logger.info(f"Unlocked session {session.id}")
    
    def add_message(
        self,
        db: Session,
        session: ChatSession,
        role: str,
        content: str
    ):
        """Add message to conversation history"""
        if not session.conversation_history:
            session.conversation_history = {"messages": []}
        
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        session.conversation_history["messages"].append(message)
        db.commit()
    
    def get_conversation_history(self, session: ChatSession) -> List[Dict]:
        """Get conversation messages"""
        if not session.conversation_history:
            return []
        return session.conversation_history.get("messages", [])
    
    def process_query(
        self,
        db: Session,
        session_id: uuid.UUID,
        query: str,
        filters: Optional[Dict] = None
    ) -> Dict:
        """
        Process user query with context awareness
        
        Args:
            db: Database session
            session_id: Chat session ID
            query: User query
            filters: Optional search filters
            
        Returns:
            Response dictionary
        """
        start_time = datetime.utcnow()
        
        try:
            # Get or create session
            session = self.get_session(db, session_id)
            if not session:
                session = self.create_session(db)
            
            # Add user message to history
            self.add_message(db, session, "user", query)
            
            # Apply document lock if active
            search_filters = filters or {}
            if session.locked_document_id:
                search_filters['document_id'] = str(session.locked_document_id)
            
            # Preprocess query for better retrieval
            query_metadata = self.query_processor.preprocess_query(query)
            expanded_query = query_metadata['expanded_query']
            is_short_query = query_metadata['is_short']
            
            logger.info(f"Original query: '{query}'")
            logger.info(f"Expanded query: '{expanded_query}'")
            logger.info(f"Question types: {query_metadata['question_types']}")
            
            # Search for relevant chunks using expanded query
            # Use higher limit to get results from multiple countries for general queries
            search_results = self.search_service.search(
                query=expanded_query,  # Use expanded query for better matching
                **search_filters,
                top_k=15,  # Increased from 5 to get more country coverage
                min_score=0.3  # Filter low-quality matches
            )
            
            # Fallback: If no results with expanded query, try original query with lower threshold
            if not search_results and is_short_query:
                logger.info("No results with expanded query, trying original query with lower threshold")
                search_results = self.search_service.search(
                    query=query,  # Use original query
                    **search_filters,
                    top_k=15,
                    min_score=0.2  # Lower threshold for fallback
                )
            
            if not search_results:
                # Infer context even with no results (returns general + guide suggestions)
                context_metadata = self.context_inference.infer_context([], query)
                response_text = "I couldn't find any relevant information in the documents for your query."
                self.add_message(db, session, "assistant", response_text)
                return {
                    "answer": response_text,
                    "sources": [],
                    "session_id": str(session.id),
                    "response_time_ms": 0,
                    "context_metadata": context_metadata
                }
            
            # --- CONTEXT INFERENCE (no LLM call) ---
            # Infer country, topics, query type from search result metadata
            context_metadata = self.context_inference.infer_context(search_results, query)
            logger.info(f"Context: type={context_metadata['query_type']}, "
                        f"country={context_metadata['detected_country']}")
            
            # Generate answer using LLM
            conversation_history = self.get_conversation_history(session)
            answer = self.llm_service.generate_answer(
                query=query,
                search_results=search_results,
                conversation_history=conversation_history
            )
            
            # Add assistant message to history
            self.add_message(db, session, "assistant", answer)
            
            # Update session activity
            self.update_session_activity(db, session)
            
            # Calculate response time
            response_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            # Log query
            query_log = QueryLog(
                session_id=session.id,
                query=query,
                results_count=len(search_results),
                response_time_ms=response_time_ms
            )
            db.add(query_log)
            db.commit()
            
            # Check if the response is conversational/greeting or indicates lack of information
            # These types of responses should NOT show sources
            no_source_phrases = [
                # Greetings and conversational
                "hello",
                "hi there",
                "how can i assist",
                "how can i help",
                "feel free to ask",
                "what can i do for you",
                "nice to meet you",
                # No information phrases
                "don't have information",
                "don't have any information",
                "no information available",
                "cannot find information",
                "couldn't find",
                "not available in the documents",
                "no relevant information",
                "unable to find",
                "doesn't contain information"
            ]
            
            answer_lower = answer.lower()
            should_suppress_sources = any(phrase in answer_lower for phrase in no_source_phrases)
            
            # Also suppress sources for general (multi-country) queries — too many URLs looks cluttered
            # Only show sources for country-specific queries where a single document is relevant
            if context_metadata.get('query_type') == 'general':
                should_suppress_sources = True
            
            # Format sources - only for country-specific responses
            # Deduplicate by document_id, and filter to detected country only
            sources = []
            if not should_suppress_sources:
                detected_country = context_metadata.get('detected_country', '').lower() if context_metadata.get('detected_country') else None
                seen_docs = set()
                for result in search_results:
                    doc_id = result['payload'].get('document_id')
                    jurisdiction = result['payload'].get('jurisdiction', '')
                    
                    # For country-specific queries, only show sources from the detected country
                    if detected_country and jurisdiction.lower() != detected_country:
                        continue
                    
                    # Only add if we haven't seen this document yet
                    if doc_id and doc_id not in seen_docs:
                        seen_docs.add(doc_id)
                        sources.append({
                            "document_id": doc_id,
                            "document_title": result['payload'].get('document_title'),
                            "page_number": result['payload'].get('page_number'),
                            "chunk_text": result['payload'].get('chunk_text')[:200] + "...",
                            "jurisdiction": result['payload'].get('jurisdiction'),
                            "guide_type": result['payload'].get('guide_type'),
                            "score": result['score']
                        })
            
            return {
                "answer": answer,
                "sources": sources,
                "session_id": str(session.id),
                "response_time_ms": response_time_ms,
                "context_metadata": context_metadata
            }
            
        except Exception as e:
            logger.error(f"Error processing query: {e}")
            raise