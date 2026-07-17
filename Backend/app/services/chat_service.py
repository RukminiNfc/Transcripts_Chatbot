from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm.attributes import flag_modified
from app.models.database import ChatSession, QueryLog, Transcript, Customer, ConversationLog, Requirement, RequirementVersion
from app.services.search_service import SearchService
from app.services.llm_service import LLMService
from app.services.query_processor import QueryProcessor
from app.services.reranker import Reranker
from typing import Optional, Dict, List
import logging
import uuid
from datetime import datetime
from app.utils.dates import session_to_ymd

logger = logging.getLogger(__name__)

class ChatService:
    """Manage chat conversations and context"""
    
    def __init__(self):
        self.search_service = SearchService()
        self.llm_service = LLMService()
        self.query_processor = QueryProcessor()
        self.reranker = Reranker()
    
    async def create_session(self, db: AsyncSession, user_id: Optional[str] = None) -> ChatSession:
        """Create new chat session"""
        session = ChatSession(
            user_id=user_id,
            conversation_history={"messages": []}
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        
        logger.info(f"Created chat session: {session.id}")
        return session
    
    async def get_session(self, db: AsyncSession, session_id: uuid.UUID) -> Optional[ChatSession]:
        """Get chat session by ID"""
        result = await db.execute(select(ChatSession).filter(ChatSession.id == session_id))
        return result.scalars().first()
    
    async def update_session_activity(self, db: AsyncSession, session: ChatSession):
        """Update last activity timestamp"""
        session.last_activity = datetime.utcnow()
        await db.commit()
    
    async def add_message(
        self,
        db: AsyncSession,
        session: ChatSession,
        role: str,
        content: str
    ):
        """Add message to conversation history.

        conversation_history is a plain JSON column (not mutation-tracked), so we must rebuild
        it with FRESH objects (new dict + new list) and flag it modified — otherwise SQLAlchemy
        does not detect an in-place change and never persists the messages.
        """
        existing = session.conversation_history or {"messages": []}
        messages = list(existing.get("messages", []))  # new list, not shared with the snapshot

        messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat()
        })

        session.conversation_history = {"messages": messages}  # brand-new dict + list
        flag_modified(session, "conversation_history")         # force SQLAlchemy to persist it
        await db.commit()
    
    def get_conversation_history(self, session: ChatSession) -> List[Dict]:
        """Get conversation messages"""
        if not session.conversation_history:
            return []
        return session.conversation_history.get("messages", [])

    @staticmethod
    def _ts_to_seconds(ts) -> int:
        """Parse a call timestamp ('M:SS', 'MM:SS', or 'H:MM:SS') into seconds for ordering."""
        try:
            parts = [int(x) for x in str(ts).split(":")]
            while len(parts) < 3:
                parts = [0] + parts
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        except Exception:
            return 0

    async def _expand_dialogue(self, db: AsyncSession, search_results: List[Dict],
                               window: int = 4, max_total_lines: int = 60) -> str:
        """Build contiguous, speaker+role-labeled conversation windows around the retrieved
        conversation hits, so the LLM can see the actual back-and-forth (who committed, who was
        asked, who decided). Owners come from the reliable `speaker`/`role` fields — not from
        parsing names out of sentence text. Returns "" when there are no conversation hits.
        """
        from collections import defaultdict
        groups: Dict[str, Dict] = defaultdict(lambda: {"hit_ids": set(), "session": "", "date": ""})
        for r in search_results:
            p = r.get("payload") or {}
            tid = p.get("transcript_id")
            if "speaker" in p and tid and p.get("log_id"):
                g = groups[str(tid)]
                g["hit_ids"].add(str(p["log_id"]))
                g["session"] = p.get("session") or g["session"]
                g["date"] = p.get("date_ymd") or p.get("call_date") or g["date"]

        if not groups:
            return ""

        sections: List[str] = []
        total = 0
        for tid, g in groups.items():
            if total >= max_total_lines:
                break
            try:
                rows = (await db.execute(
                    select(ConversationLog).where(ConversationLog.transcript_id == uuid.UUID(tid))
                )).scalars().all()
            except Exception as e:
                logger.error(f"Dialogue expand failed for transcript {tid}: {e}")
                continue
            if not rows:
                continue

            ordered = sorted(rows, key=lambda b: self._ts_to_seconds(b.call_timestamp))
            pos = {str(b.id): i for i, b in enumerate(ordered)}

            keep = set()
            for hid in g["hit_ids"]:
                i = pos.get(hid)
                if i is None:
                    continue
                for j in range(max(0, i - window), min(len(ordered), i + window + 1)):
                    keep.add(j)
            if not keep:
                continue

            lines = []
            for j in sorted(keep):
                if total >= max_total_lines:
                    break
                b = ordered[j]
                role = "client" if (b.role or "").lower() == "client" else "team"
                lines.append(f"[{role}] {b.speaker} ({b.call_timestamp}): {(b.text or '').strip()}")
                total += 1
            if lines:
                header = f"--- Conversation: {g['session'] or tid} ({g['date']}) ---"
                sections.append(header + "\n" + "\n".join(lines))

        return "\n\n".join(sections)

    async def _requirements_for_scope(self, db: AsyncSession, date_ymd: str = None) -> List[Dict]:
        """Fetch requirements from the STORE (not semantic search) for a specific call date, or all
        calls if date_ymd is None. Counting / listing questions must read the store directly so the
        count and list are EXACT and COMPLETE — not a retrieved subset."""
        transcripts = (await db.execute(select(Transcript))).scalars().all()
        if date_ymd:
            sessions = [t.session_name for t in transcripts
                        if session_to_ymd(t.session_name, t.call_date) == date_ymd]
            if not sessions:
                return []
            rid = (await db.execute(
                select(RequirementVersion.requirement_id)
                .where(RequirementVersion.session.in_(sessions)).distinct()
            )).scalars().all()
        else:
            rid = (await db.execute(select(RequirementVersion.requirement_id).distinct())).scalars().all()
        if not rid:
            return []
        reqs = (await db.execute(select(Requirement).where(Requirement.id.in_(rid)))).scalars().all()
        return [{"category": (r.category or "Uncategorized"),
                 "sub_category": r.sub_category, "text": r.current_text} for r in reqs]

    @staticmethod
    def _build_requirements_answer(reqs: List[Dict], date_ymd: str, aggregate: str, query: str) -> str:
        """Format an EXACT, deterministic count/list answer from stored requirements. For a long list,
        show the count + per-category breakdown + a few examples per category, and offer to drill into
        a category (so the reader isn't buried in 100+ lines)."""
        from collections import defaultdict
        n = len(reqs)
        scope = f"the {date_ymd} call" if date_ymd else "all recorded calls"
        if n == 0:
            return f"I don't find any requirements recorded for {scope}."

        by_cat = defaultdict(list)
        for r in reqs:
            by_cat[r["category"]].append(r)
        cats_sorted = sorted(by_cat, key=lambda c: (-len(by_cat[c]), c))

        # If the user named a specific category, list THAT category in full.
        q = (query or "").lower()
        named = [c for c in cats_sorted if c != "Uncategorized" and c.lower() in q]
        if aggregate == "list" and named:
            cat = named[0]
            items = by_cat[cat]
            lines = [f"**{cat}** — {len(items)} requirement(s) from {scope}:", ""]
            lines += [f"{i}. {r['text']}" for i, r in enumerate(items, 1)]
            return "\n".join(lines)

        if aggregate == "count":
            lines = [f"There are **{n}** requirements from {scope}, across **{len(by_cat)}** categories:", ""]
            lines += [f"- **{c}** — {len(by_cat[c])}" for c in cats_sorted]
            return "\n".join(lines)

        # aggregate == "list": grouped breakdown + samples + drill-down offer
        lines = [f"There are **{n}** requirements from {scope}, across **{len(by_cat)}** categories. "
                 f"Here's the breakdown with a few examples each:", ""]
        for c in cats_sorted:
            items = by_cat[c]
            lines.append(f"**{c}** ({len(items)})")
            for r in items[:3]:
                lines.append(f"- {r['text']}")
            if len(items) > 3:
                lines.append(f"- …and {len(items) - 3} more in this category")
            lines.append("")
        lines.append(f"That's **{n}** requirements in total — too many to list every one here. "
                     f"Tell me a **category** from the list above (or a specific topic) and I'll give you "
                     f"the complete list for just that one.")
        return "\n".join(lines)

    async def _load_full_call(self, db: AsyncSession, date_ymd: str, max_chars: int = 150000) -> str:
        """Load the ENTIRE transcript(s) for a specific call date, in chronological order, as
        speaker+role-labeled text. Used for "list all action items / all X from THIS call" questions,
        where top-K snippet retrieval would return only an incomplete and inconsistent subset. A single
        call fits comfortably in one LLM pass, so the model sees the whole call and returns the same
        complete list every time. Matches on the session-name date (not the timezone-shifted timestamp).
        """
        transcripts = (await db.execute(select(Transcript))).scalars().all()
        match = [t for t in transcripts if session_to_ymd(t.session_name, t.call_date) == date_ymd]
        if not match:
            return ""

        sections: List[str] = []
        total = 0
        for t in match:
            rows = (await db.execute(
                select(ConversationLog).where(ConversationLog.transcript_id == t.id)
            )).scalars().all()
            if not rows:
                continue
            ordered = sorted(rows, key=lambda b: self._ts_to_seconds(b.call_timestamp))
            lines = []
            for b in ordered:
                role = "client" if (b.role or "").lower() == "client" else "team"
                line = f"[{role}] {b.speaker} ({b.call_timestamp}): {(b.text or '').strip()}"
                if total + len(line) > max_chars:
                    lines.append("... (transcript truncated for length) ...")
                    total = max_chars
                    break
                lines.append(line)
                total += len(line)
            if lines:
                sections.append(f"--- FULL conversation: {t.session_name} ({date_ymd}) ---\n" + "\n".join(lines))
            if total >= max_chars:
                break
        return "\n\n".join(sections)

    async def _get_transcript_rows(self, db: AsyncSession) -> List:
        """Return all transcripts as sorted (date_ymd, session_name) tuples, oldest -> newest.

        Dates come from the session name (immune to the timezone-shifted timestamp). This one
        read backs BOTH the corpus map and the relative-date ("last call") resolution.
        """
        result = await db.execute(select(Transcript))
        transcripts = result.scalars().all()
        return sorted(
            ((session_to_ymd(t.session_name, t.call_date), t.session_name) for t in transcripts),
            key=lambda r: r[0],
        )

    @staticmethod
    def _build_transcript_map(rows: List) -> str:
        """Format the transcript rows into the authoritative 'corpus map' text.

        Metadata questions ("how many calls?", "latest date?", "list the sessions") are facts
        about the WHOLE collection — semantic search can't answer them because it only sees a
        few retrieved chunks. Injecting this live list lets the LLM answer from real data.
        """
        if not rows:
            return ""
        lines = "\n".join(f"- {ymd}  ({sess})" for ymd, sess in rows)
        earliest_ymd, earliest_sess = rows[0]
        latest_ymd, latest_sess = rows[-1]
        return (
            f"Total grooming-call transcripts: {len(rows)}\n"
            f"Earliest call: {earliest_sess} ({earliest_ymd})\n"
            f"Most recent (latest) call: {latest_sess} ({latest_ymd})\n"
            f"All transcripts (oldest to newest):\n{lines}"
        )
    
    async def process_query(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        query: str
    ) -> Dict:
        """
        Process user query with dynamic intent routing
        """
        start_time = datetime.utcnow()
        try:
            session = await self.get_session(db, session_id)
            if not session:
                session = await self.create_session(db)

            prep = await self._prepare_answer(db, session, query)

            if prep.get("structured_answer"):
                answer = prep["structured_answer"]  # exact count/list from the store, no LLM
            else:
                answer = self.llm_service.generate_answer(
                    query=query,
                    search_results=prep["search_results"],
                    conversation_history=prep["conversation_history"],
                    customer_metadata=prep["customer_metadata"],
                    has_context=prep["has_context"],
                    transcript_map=prep["transcript_map"],
                    dialogue_context=prep["dialogue_context"],
                )

            await self.add_message(db, session, "assistant", answer)
            await self.update_session_activity(db, session)

            response_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            return {
                "answer": answer,
                "sources": prep["sources"],
                "session_id": str(session.id),
                "response_time_ms": response_time_ms,
                "context_metadata": {"intent": prep["target_collection"]},
            }
        except Exception as e:
            logger.error(f"Error processing query: {e}")
            raise

    async def _prepare_answer(self, db: AsyncSession, session: ChatSession, query: str) -> Dict:
        """Shared pipeline for streaming and non-streaming answers.

        Adds the user message, routes intent (with relative-date "last call" resolution),
        retrieves + re-ranks context, and gathers everything the answer LLM needs. Does NOT
        call the answer LLM itself, so both paths stay identical up to generation.
        """
        # Prior turns captured BEFORE adding the current message (for follow-up resolution).
        prior_history = self.get_conversation_history(session)
        await self.add_message(db, session, "user", query)

        # Live transcript list — backs BOTH the corpus map and relative-date resolution.
        transcript_rows = await self._get_transcript_rows(db)
        available_dates = [ymd for ymd, _ in transcript_rows]

        intent_data = self.query_processor.analyze_intent(
            query, conversation_history=prior_history, available_dates=available_dates
        )
        target_collection = intent_data["intent"]
        extracted_filters = intent_data.get("filters", {})
        # Rewritten standalone query for retrieval; the ORIGINAL query still goes to the answer LLM.
        search_query = intent_data.get("search_query") or query
        if intent_data.get("mode") == "transform":
            logger.info(f"Transformation request -> retrieving on topic: '{search_query}'")

        # STRUCTURED ROUTING: "how many requirements" / "list all requirements" are COUNT/LIST
        # questions — answer them from the requirements STORE directly (exact + complete), not from
        # a handful of semantic snippets. Short-circuits the RAG path entirely.
        aggregate = intent_data.get("aggregate", "none")
        if target_collection == "requirements" and aggregate in ("count", "list"):
            want_date = extracted_filters.get("call_date")
            reqs = await self._requirements_for_scope(db, want_date)
            structured = self._build_requirements_answer(reqs, want_date, aggregate, query)
            logger.info(f"Structured requirements {aggregate} for date={want_date}: {len(reqs)} requirements")
            return {
                "structured_answer": structured,
                "search_results": [],
                "conversation_history": [],
                "customer_metadata": None,
                "has_context": True,
                "transcript_map": "",
                "dialogue_context": "",
                "sources": [],
                "target_collection": target_collection,
            }

        # Lower min_score with explicit filters (metadata-heavy queries have low semantic overlap).
        dynamic_min_score = 0.1 if extracted_filters else 0.3
        # Wide pool + re-rank beats a narrow top-k (stops a loud generic word from crowding results).
        candidate_pool = self.search_service.search_multi(
            query=search_query,
            collections=["requirements", "conversations"],
            top_k=40,
            min_score=dynamic_min_score,
            filters=extracted_filters if extracted_filters else None,
        )
        search_results = self.reranker.rerank(search_query, candidate_pool, top_n=12)
        has_context = len(search_results) > 0

        # When the question needs the conversation flow (who does what / why / what was decided),
        # expand the conversation hits into contiguous speaker+role-labeled windows. The intent
        # step decides this dynamically — no hardcoded question-type list.
        dialogue_context = ""
        if intent_data.get("needs_dialogue"):
            want_date = extracted_filters.get("call_date")
            if want_date:
                # "list all action items / all X from THIS call" -> feed the WHOLE call, so the
                # answer is complete and identical every time (not a varying 12-snippet subset).
                dialogue_context = await self._load_full_call(db, want_date)
                logger.info(f"needs_dialogue + date {want_date} -> FULL call ({len(dialogue_context)} chars)")
            if not dialogue_context:
                # No specific call resolved (or none matched) -> windowed snippets around the hits.
                dialogue_context = await self._expand_dialogue(db, search_results)
                logger.info(f"needs_dialogue -> windowed dialogue ({len(dialogue_context)} chars)")

        # First customer as project context (chat doesn't enforce a customer_id yet).
        customer_metadata = None
        cust_result = await db.execute(select(Customer))
        customer = cust_result.scalars().first()
        if customer:
            customer_metadata = {
                "name": customer.name,
                "client_speaker_name": customer.client_speaker_name,
            }

        transcript_map_text = self._build_transcript_map(transcript_rows)
        conversation_history = self.get_conversation_history(session)

        # Format sources per-result (results can be a MIX of both stores).
        sources = []
        for result in search_results:
            payload = result["payload"]
            if "speaker" in payload:
                sources.append({
                    "type": "conversation",
                    "speaker": payload.get("speaker"),
                    "timestamp": payload.get("call_timestamp"),
                    "session": payload.get("session"),
                    "text": payload.get("text"),
                })
            elif "requirement_text" in payload:
                sources.append({
                    "type": "requirement",
                    "category": payload.get("category"),
                    "sub_category": payload.get("sub_category"),
                    "change_type": payload.get("change_type"),
                    "confirmed_by": payload.get("confirmed_by"),
                    "text": payload.get("requirement_text"),
                })

        return {
            "search_results": search_results,
            "conversation_history": conversation_history,
            "customer_metadata": customer_metadata,
            "has_context": has_context,
            "transcript_map": transcript_map_text,
            "dialogue_context": dialogue_context,
            "sources": sources,
            "target_collection": target_collection,
        }

    async def process_query_stream(self, db: AsyncSession, session_id: uuid.UUID, query: str):
        """Async generator yielding SSE event dicts.

        'delta' events carry answer text as it streams; a final 'done' event carries the
        session id, sources, and intent metadata. The assistant message is saved to history
        after the stream finishes.
        """
        session = await self.get_session(db, session_id)
        if not session:
            session = await self.create_session(db)

        prep = await self._prepare_answer(db, session, query)

        # Structured count/list answers come straight from the store — send as one chunk.
        if prep.get("structured_answer"):
            answer = prep["structured_answer"]
            yield {"type": "delta", "text": answer}
            await self.add_message(db, session, "assistant", answer)
            await self.update_session_activity(db, session)
            yield {
                "type": "done",
                "session_id": str(session.id),
                "sources": prep["sources"],
                "context_metadata": {"intent": prep["target_collection"]},
            }
            return

        collected = []
        async for delta in self.llm_service.generate_answer_stream(
            query=query,
            search_results=prep["search_results"],
            conversation_history=prep["conversation_history"],
            customer_metadata=prep["customer_metadata"],
            has_context=prep["has_context"],
            transcript_map=prep["transcript_map"],
            dialogue_context=prep["dialogue_context"],
        ):
            collected.append(delta)
            yield {"type": "delta", "text": delta}

        answer = "".join(collected)
        await self.add_message(db, session, "assistant", answer)
        await self.update_session_activity(db, session)

        yield {
            "type": "done",
            "session_id": str(session.id),
            "sources": prep["sources"],
            "context_metadata": {"intent": prep["target_collection"]},
        }