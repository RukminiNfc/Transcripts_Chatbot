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
from app.services.social_replies import match_social_reply, greeting_prefix
from app.core.config import settings

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

        # Preserve every non-message key (title, last_sources, ...) while replacing the messages list.
        session.conversation_history = {**existing, "messages": messages}
        flag_modified(session, "conversation_history")         # force SQLAlchemy to persist it
        await db.commit()
    
    def get_conversation_history(self, session: ChatSession) -> List[Dict]:
        """Get conversation messages"""
        if not session.conversation_history:
            return []
        return session.conversation_history.get("messages", [])

    async def list_sessions(self, db: AsyncSession, user_id: str) -> List[Dict]:
        """List the user's chat sessions (newest first) for the sidebar. Each session gets a short,
        STORED title (generated ONCE, lazily — greetings -> 'Greeting', else a small LLM call). This
        runs on the sidebar refresh (after the answer), so it never affects answer response time."""
        result = await db.execute(
            select(ChatSession)
            .filter(ChatSession.user_id == user_id)
            .order_by(ChatSession.last_activity.desc().nullslast())
        )
        out, dirty = [], False
        for s in result.scalars().all():
            history = s.conversation_history or {}
            msgs = history.get("messages", [])
            title = history.get("title")
            if not title:
                first_user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
                if first_user:
                    title = self._make_title(first_user)
                    s.conversation_history = {**history, "title": title}
                    flag_modified(s, "conversation_history")
                    dirty = True
                else:
                    title = "New chat"
            when = s.last_activity or s.started_at
            out.append({
                "session_id": str(s.id),
                "title": title,
                "last_activity": when.isoformat() if when else None,
                "message_count": len(msgs),
            })
        if dirty:
            await db.commit()
        return out

    def _make_title(self, first_user_msg: str) -> str:
        """Short sidebar title: greetings/small-talk -> 'Greeting' (no LLM); otherwise a small
        LLM-generated 3-5 word title."""
        if match_social_reply(first_user_msg) is not None:
            return "Greeting"
        return self.llm_service.generate_title(first_user_msg)

    async def rename_session(self, db: AsyncSession, session_id: uuid.UUID, user_id: str, new_title: str) -> bool:
        """Rename a chat (title stored in the conversation_history JSON). Only the owner can rename."""
        session = await self.get_session(db, session_id)
        if not session or session.user_id != user_id:
            return False
        title = (new_title or "").strip()[:80]
        if not title:
            return False
        history = session.conversation_history or {"messages": []}
        session.conversation_history = {**history, "title": title}
        flag_modified(session, "conversation_history")
        await db.commit()
        return True

    async def get_session_messages(self, db: AsyncSession, session_id: uuid.UUID, user_id: str) -> Optional[List[Dict]]:
        """Return a session's messages IF it belongs to user_id; else None (not found/forbidden)."""
        session = await self.get_session(db, session_id)
        if not session or session.user_id != user_id:
            return None
        return self.get_conversation_history(session)

    async def delete_session(self, db: AsyncSession, session_id: uuid.UUID, user_id: str) -> bool:
        """Delete a session IF it belongs to user_id. Returns True if deleted."""
        session = await self.get_session(db, session_id)
        if not session or session.user_id != user_id:
            return False
        await db.delete(session)
        await db.commit()
        return True

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

    async def _changes_for_scope(self, db: AsyncSession, changes: str, date_ymd: str = None) -> List[Dict]:
        """Read the requirement VERSION audit log for a change/diff question. Returns one row per
        change (added / modified / removed) with the NEW text and — for a modification — the PREVIOUS
        version's text (the 'before'). Scoped to a specific call date when given (matched on the
        session name, timezone-safe). Exact + complete — no semantic search."""
        type_map = {
            "modified": ["modified"],
            "added": ["added"],
            "removed": ["removed"],
            "all": ["added", "modified", "removed"],
        }
        wanted = type_map.get(changes, ["added", "modified", "removed"])

        # Restrict to the sessions that map to the requested call date (session-name based).
        session_filter = None
        if date_ymd:
            transcripts = (await db.execute(select(Transcript))).scalars().all()
            sessions = [t.session_name for t in transcripts
                        if session_to_ymd(t.session_name, t.call_date) == date_ymd]
            if not sessions:
                return []
            session_filter = sessions

        q = select(RequirementVersion).where(RequirementVersion.change_type.in_(wanted))
        if session_filter is not None:
            q = q.where(RequirementVersion.session.in_(session_filter))
        versions = (await db.execute(q)).scalars().all()
        if not versions:
            return []

        # Category / sub-category live on the Requirement (not the version) — preload them once.
        req_ids = list({v.requirement_id for v in versions})
        reqs = (await db.execute(select(Requirement).where(Requirement.id.in_(req_ids)))).scalars().all()
        req_map = {r.id: r for r in reqs}

        rows: List[Dict] = []
        for v in versions:
            old_text = ""
            if v.change_type == "modified" and (v.version_number or 0) > 1:
                old_text = (await db.execute(
                    select(RequirementVersion.text)
                    .where(RequirementVersion.requirement_id == v.requirement_id)
                    .where(RequirementVersion.version_number == v.version_number - 1)
                    .limit(1)
                )).scalars().first() or ""
            r = req_map.get(v.requirement_id)
            rows.append({
                "category": (getattr(r, "category", "") or "Uncategorized"),
                "sub_category": (getattr(r, "sub_category", "") or ""),
                "change_type": v.change_type,
                "new_text": v.text or "",
                "old_text": old_text,
                "session": v.session or "",
                # When it was discussed (audit log already records this) — shown per item and used
                # for the chronological "initial -> later changes" ordering of the answer.
                "discussed_date": v.discussed_date or v.created_at,
            })
        return rows

    def _filter_changes_by_topic(self, topic: str, rows: List[Dict]) -> List[Dict]:
        """Scope change/diff rows to the NAMED topic in two stages. Stage 1: Cohere-rerank every row
        against the topic and keep rows above RERANK_CHANGES_THRESHOLD (membership by relevance, not
        by the LLM-generated category). Stage 2: one LLM pass confirms each survivor's CONTENT belongs
        to the topic (kills same-meeting vocabulary bleed). On a stage-2 failure the fallback is the
        stage-1 (reranked) result — NEVER the unfiltered dump."""
        if not topic or not rows:
            return rows
        candidates = [{
            "payload": {
                "requirement_text": (r["new_text"] or r["old_text"]),
                "category": r["category"],
                "sub_category": r["sub_category"],
                "row_index": i,
            },
            "score": 1.0,
        } for i, r in enumerate(rows)]
        kept = self.reranker.rerank(
            topic, candidates,
            top_n=max(1, len(candidates)),
            score_threshold=settings.RERANK_CHANGES_THRESHOLD,
        )
        stage1 = [rows[c["payload"]["row_index"]] for c in kept]
        if not stage1:
            return []
        # Category goes along as a bracketed HINT (the prompt tells the LLM it may be wrong and the
        # text wins). Debugged on real data: with no category context, items whose text doesn't name
        # the feature ("Push source table...") were wrongly excluded — 7/25 recall on 'Jobs mobile app'.
        texts = [f"[{r['category']}] {r['new_text'] or r['old_text']}" for r in stage1]
        ids = self.llm_service.filter_items_by_topic(topic, texts)
        if ids is None:
            return stage1
        return [stage1[i] for i in ids]

    @staticmethod
    def _change_sort_key(r: Dict):
        """Chronological sort key for change rows (undated rows last)."""
        d = r.get("discussed_date")
        try:
            return (0, d.timestamp())
        except Exception:
            return (1, 0.0)

    @staticmethod
    def _change_when(r: Dict) -> str:
        """Human-readable 'when was this discussed' label: DATE + session. Date only — the stored
        time-of-day is a per-call ingestion placeholder (identical on every row), not a real
        discussion time, so showing it would mislead."""
        d = r.get("discussed_date")
        s = ""
        if d is not None:
            try:
                s = d.strftime("%Y-%m-%d")
            except Exception:
                s = str(d)[:10]
        sess = (r.get("session") or "").strip()
        if s and sess:
            return f"{s} ({sess})"
        return s or sess

    @staticmethod
    def _build_changes_overview(rows: List[Dict], changes: str) -> str:
        """Unscoped change question (no topic AND no date) -> NEVER dump the whole change history.
        Answer with per-call counts and ask the user to narrow — same guard pattern as the big
        requirements list."""
        from collections import defaultdict
        per_call = defaultdict(lambda: {"modified": 0, "added": 0, "removed": 0, "_sess": ""})
        totals = {"modified": 0, "added": 0, "removed": 0}
        for r in rows:
            ct = r.get("change_type")
            if ct not in totals:
                continue
            d = r.get("discussed_date")
            try:
                day = d.strftime("%Y-%m-%d")
            except Exception:
                day = (r.get("session") or "unknown call")
            per_call[day][ct] += 1
            if not per_call[day]["_sess"]:
                per_call[day]["_sess"] = (r.get("session") or "")
            totals[ct] += 1
        what = "changes" if changes == "all" else f"{changes} requirements"
        head = ", ".join(f"**{totals[t]} {t}**" for t in ("modified", "added", "removed") if totals[t])
        lines = [f"Across all recorded calls there are {head} — too many to list at once. Per call:", ""]
        for day in sorted(per_call.keys()):
            c = per_call[day]
            parts = ", ".join(f"{c[t]} {t}" for t in ("modified", "added", "removed") if c[t])
            sess = f" ({c['_sess']})" if c.get("_sess") and c["_sess"] != day else ""
            lines.append(f"- **{day}**{sess}: {parts}")
        lines.append("")
        lines.append(f"Tell me a **topic** (a feature/project name) or a **call date** and I'll list those "
                     f"{what} in full, with before → after details.")
        return "\n".join(lines)

    @staticmethod
    def _build_changes_answer(rows: List[Dict], summaries: List[str], date_ymd: str, changes: str,
                              topic: str = "") -> str:
        """Format the change/diff answer: a count header, then per-item before->after (A), the
        one-line summary (B) for modifications, plus added / removed lists — each item stamped with
        WHEN it was discussed (date/time + session). Rows are expected pre-sorted chronologically."""
        from collections import defaultdict
        topic_part = f'for "{topic}" ' if topic else ""
        scope = f"the {date_ymd} call" if date_ymd else "all recorded calls"
        if not rows:
            what = "changes" if changes == "all" else f"{changes} requirements"
            return f"I don't find any {what} {topic_part}for {scope}."

        when = ChatService._change_when

        by_type = defaultdict(list)
        for r in rows:
            by_type[r["change_type"]].append(r)
        mod, add, rem = by_type.get("modified", []), by_type.get("added", []), by_type.get("removed", [])

        counts = []
        if mod: counts.append(f"**{len(mod)} modified**")
        if add: counts.append(f"**{len(add)} added**")
        if rem: counts.append(f"**{len(rem)} removed**")
        lines = [f"Here's what changed {topic_part}in {scope}: " + ", ".join(counts) + ".", ""]

        if mod:
            lines.append("### Modified")
            for i, r in enumerate(mod):
                label = r["sub_category"] or r["category"] or "Requirement"
                lines.append(f"**{label}**")
                lines.append(f"- **Before:** {r['old_text'] or '_(previous version not recorded)_'}")
                lines.append(f"- **After:** {r['new_text']}")
                summ = summaries[i] if i < len(summaries) else ""
                if summ:
                    lines.append(f"- **Summary:** {summ}")
                w = when(r)
                if w:
                    lines.append(f"- **Discussed:** {w}")
                lines.append("")
        if add:
            lines.append("### Added")
            for r in add:
                label = r["sub_category"] or r["category"] or "Requirement"
                w = when(r)
                suffix = f" — _discussed {w}_" if w else ""
                lines.append(f"- **{label}:** {r['new_text']}{suffix}")
            lines.append("")
        if rem:
            lines.append("### Removed")
            for r in rem:
                label = r["sub_category"] or r["category"] or "Requirement"
                w = when(r)
                suffix = f" — _discussed {w}_" if w else ""
                lines.append(f"- **{label}:** {r['new_text']}{suffix}")
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _build_topic_requirements_answer(reqs: List[Dict]) -> str:
        """Deterministic COMPLETE list of the requirements retrieved for a topic (relevance-filtered
        from the WHOLE store). Grouped by category for readability only (display, not filtering — the
        cutoff already decided membership), every item shown in FULL — so the answer can't silently
        drop items the way an LLM write-up can. The count makes a partial result visible."""
        from collections import defaultdict
        if not reqs:
            return ("I couldn't find any requirements about that in the recorded transcripts. "
                    "Try naming the feature or topic more specifically.")
        by_cat = defaultdict(list)
        for r in reqs:
            by_cat[(r.get("category") or "Other")].append(r)
        n = len(reqs)
        lines = [f"**{n} requirement(s) found:**", ""]
        for cat in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c)):
            lines.append(f"### {cat}")
            for r in by_cat[cat]:
                sub = (r.get("sub_category") or "").strip()
                text = (r.get("text") or "").strip()
                lines.append(f"- **{sub}:** {text}" if sub else f"- {text}")
            lines.append("")
        return "\n".join(lines).strip()

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
    
    @staticmethod
    def _build_clarification(topic: str, options: List[str]) -> str:
        """Ask a short clarifying question for an ambiguous broad term.

        We intentionally do NOT list the detected subtypes: the labels are LLM-generated and can be
        slightly off, which reads as inaccurate. Instead we ask the user to name the specific one so
        their OWN wording drives an accurate follow-up. `options` is still used upstream to DECIDE
        whether to clarify (2+ distinct subtypes exist) — it just isn't displayed here.
        """
        term = (topic or "that").strip()
        return (
            f"Your question about **{term}** could mean a few different things — which one do you mean? "
            f"Just tell me the specific one (or the area/feature it relates to) and I'll explain it."
        )

    async def process_query(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        query: str,
        user_id: Optional[str] = None,
    ) -> Dict:
        """
        Process user query with dynamic intent routing
        """
        start_time = datetime.utcnow()
        try:
            session = await self.get_session(db, session_id)
            # New session, or a session that isn't this user's -> start a fresh owned session.
            if not session or (user_id is not None and session.user_id != user_id):
                session = await self.create_session(db, user_id=user_id)

            # Social/small-talk (hi, bye, thanks) -> fixed in-character reply, no retrieval/LLM,
            # so the bot never drifts off-domain and stays 100% consistent.
            social_reply = match_social_reply(query)
            if social_reply is not None:
                await self.add_message(db, session, "user", query)
                await self.add_message(db, session, "assistant", social_reply)
                await self.update_session_activity(db, session)
                response_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                return {
                    "answer": social_reply,
                    "sources": [],
                    "session_id": str(session.id),
                    "response_time_ms": response_time_ms,
                    "context_metadata": {"intent": "social"},
                }

            prep = await self._prepare_answer(db, session, query)

            if prep.get("transform_previous"):
                answer = self.llm_service.transform_previous_answer(
                    prep["transform_instruction"], prep["previous_answer"]
                )
            elif prep.get("structured_answer"):
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

            # Greeting + question: prepend a short greeting the understanding model detected, but ONLY
            # if the answer doesn't already greet itself (so on-topic answers and small-talk replies are
            # never double-greeted). No-op when the user didn't greet.
            answer = self._greeting_prefixed(prep, answer)

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

        # Fix 1: "summarize / shorten / simplify the PREVIOUS answer" -> transform the last answer
        # DIRECTLY (no retrieval), so it condenses exactly what the user saw — no wrong-topic guess.
        if intent_data.get("mode") == "transform" and intent_data.get("transform_scope") == "previous_answer":
            last_answer = next(
                (m.get("content", "") for m in reversed(prior_history) if m.get("role") == "assistant"),
                "",
            )
            if last_answer.strip():
                logger.info("Transform on PREVIOUS answer -> reusing last answer (no retrieval).")
                return {
                    "transform_previous": True,
                    "transform_instruction": query,
                    "previous_answer": last_answer,
                    "structured_answer": None,
                    "search_results": [],
                    "conversation_history": [],
                    "customer_metadata": None,
                    "has_context": True,
                    "transcript_map": "",
                    "dialogue_context": "",
                    "sources": [],
                    "target_collection": target_collection,
                }
            # No previous answer available -> fall through to normal retrieval/transform.

        if intent_data.get("mode") == "transform":
            logger.info(f"Transformation request -> retrieving on topic: '{search_query}'")

        # CHANGES / DIFF ROUTE: "what changed / was modified / added / removed in <call>" -> read the
        # requirement VERSION history directly (exact + complete), showing before->after (A) plus a
        # one-line auto summary (B). Its OWN isolated lane; only runs when understanding flags it, so
        # every other question path is untouched.
        changes = intent_data.get("changes", "none")
        if target_collection == "requirements" and changes != "none":
            want_date = extracted_filters.get("call_date")
            topic = (intent_data.get("topic") or "").strip()
            change_rows = await self._changes_for_scope(db, changes, want_date)

            # TOPIC scoping: "what changed for <X>" -> keep only rows that BELONG to X
            # (rerank cut + LLM confirm; fallback = the rerank cut, never the unfiltered dump).
            if topic and change_rows:
                before_n = len(change_rows)
                change_rows = self._filter_changes_by_topic(topic, change_rows)
                logger.info(f"CHANGES ({changes}) topic='{topic}': {before_n} -> {len(change_rows)} kept")

            if topic and not change_rows:
                # Named topic with nothing matching -> explicit no-match, never a dump. Names the
                # asked-for change type ("modified requirements") so "no modifications" is not read
                # as "no changes at all".
                what = "changes" if changes == "all" else f"{changes} requirements"
                hint = ", ".join(available_dates)
                structured = (f'No {what} recorded for "{topic}".'
                              + (f" Recorded calls: {hint} — tell me another topic or one of these "
                                 f"call dates." if hint else ""))
            elif not topic and not want_date and change_rows:
                # NEVER dump-all: an unscoped "what changed" gets a per-call counts overview
                # plus how to narrow — not the entire change history.
                structured = self._build_changes_overview(change_rows, changes)
            else:
                # Chronological (initial -> later changes); ALSO keeps the modified-pair summaries
                # aligned with the builder's row order.
                change_rows.sort(key=self._change_sort_key)
                modified_pairs = [(r["old_text"], r["new_text"]) for r in change_rows
                                  if r["change_type"] == "modified"]
                summaries = self.llm_service.summarize_changes(modified_pairs)  # B (fresh, at query time)
                structured = self._build_changes_answer(change_rows, summaries, want_date, changes, topic)
            logger.info(f"Structured CHANGES ({changes}) topic='{topic or '-'}' date={want_date}: "
                        f"{len(change_rows)} items")
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
                "greeting": intent_data.get("greeting"),
            }

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
                "greeting": intent_data.get("greeting"),
            }

        # COMPLETE-REQUIREMENTS ROUTE: "give me the (full/all) requirements for <topic>" -> rank the
        # ENTIRE requirements store against the topic and keep EVERY item above the relevance cutoff,
        # so nothing is trimmed by a fixed cap. Relevance (Cohere) decides membership, NOT the
        # LLM-generated category. Output is a deterministic list -> the answer can't silently drop items.
        if (target_collection == "requirements"
                and intent_data.get("complete_requirements")
                and not intent_data.get("verify")):
            all_reqs = (await db.execute(
                select(Requirement).where(Requirement.status == "active")
            )).scalars().all()
            candidates = [{
                "payload": {
                    "requirement_text": r.current_text,
                    "category": r.category or "",
                    "sub_category": r.sub_category or "",
                    "requirement_id": str(r.id),
                },
                "score": 1.0,
            } for r in all_reqs if (r.current_text or "").strip()]
            # TWO-STAGE selection (same design as the changes lane).
            # Stage 1 — GATHER generously at a LOW cutoff: the similarity score only COLLECTS
            # candidates, it is not trusted to decide membership (requirement texts rarely name
            # their feature, and the LLM-generated category inside the doc can be wrong both ways).
            topic = (intent_data.get("topic") or "").strip() or search_query
            kept = self.reranker.rerank(
                search_query, candidates,
                top_n=max(1, len(candidates)),
                score_threshold=settings.RERANK_COMPLETE_GATHER_THRESHOLD,
            ) if candidates else []
            gathered_n = len(kept)
            # Stage 2 — an LLM READS each gathered item and keeps only true topic members
            # (category passed as a bracketed hint the prompt may overrule; unsure -> include).
            if kept:
                texts = [f"[{c['payload'].get('category') or ''}] "
                         f"{c['payload'].get('requirement_text') or ''}" for c in kept]
                ids = self.llm_service.filter_items_by_topic(topic, texts)
                if ids is not None:
                    kept = [kept[i] for i in ids]
                else:
                    # Confirm stage failed -> fall back to TODAY'S exact behavior (strict cutoff),
                    # never to the over-generous gather set.
                    kept = self.reranker.rerank(
                        search_query, candidates,
                        top_n=max(1, len(candidates)),
                        score_threshold=settings.RERANK_COMPLETE_THRESHOLD,
                    ) if candidates else []
            rows = [{
                "category": c["payload"].get("category") or "Other",
                "sub_category": c["payload"].get("sub_category") or "",
                "text": c["payload"].get("requirement_text") or "",
            } for c in kept]
            structured = self._build_topic_requirements_answer(rows)
            # Remember the kept requirements so a later "are you sure?" re-checks the SAME set.
            try:
                session.conversation_history = {
                    **(session.conversation_history or {}),
                    "last_sources": [c.get("payload", {}) for c in kept[:25]],
                }
                flag_modified(session, "conversation_history")
            except Exception as e:
                logger.error(f"Could not store last_sources (complete): {e}")
            logger.info(f"COMPLETE requirements topic='{topic}': {len(candidates)} store -> "
                        f"{gathered_n} gathered -> {len(kept)} kept")
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
                "greeting": intent_data.get("greeting"),
            }

        # VERIFY/challenge turn ("are you sure?") -> re-check against the SAME sources that produced
        # the PREVIOUS answer, so the grounding is NOT lost on a fresh thin search (that loss is what
        # made the bot wrongly retract correct facts). Otherwise retrieve + re-rank normally and
        # remember the sources for a future "are you sure?".
        stored_sources = (session.conversation_history or {}).get("last_sources")
        if intent_data.get("verify") and stored_sources:
            search_results = [{"payload": p or {}, "score": 1.0} for p in stored_sources]
            has_context = True
            logger.info(f"Verify turn -> reusing {len(search_results)} sources from the previous answer.")
        else:
            # Lower min_score with explicit filters (metadata-heavy queries have low semantic overlap).
            dynamic_min_score = 0.1 if extracted_filters else 0.3
            # Wide candidate pool (config) -> better recall; the re-ranker + score-threshold trim it.
            candidate_pool = self.search_service.search_multi(
                query=search_query,
                collections=["requirements", "conversations"],
                top_k=settings.RETRIEVAL_CANDIDATE_POOL,
                min_score=dynamic_min_score,
                filters=extracted_filters if extracted_filters else None,
            )
            # ADAPTIVE context size: broad topics keep MORE chunks; narrow questions keep FEWER.
            top_n = (settings.RETRIEVAL_TOP_N_BROAD if intent_data.get("scope") == "broad"
                     else settings.RETRIEVAL_TOP_N_NARROW)
            search_results = self.reranker.rerank(search_query, candidate_pool, top_n=top_n)
            has_context = len(search_results) > 0
            # Remember these sources so a later "are you sure?" verifies against the SAME grounding.
            try:
                session.conversation_history = {
                    **(session.conversation_history or {}),
                    "last_sources": [r.get("payload", {}) or {} for r in search_results[:25]],
                }
                flag_modified(session, "conversation_history")
            except Exception as e:
                logger.error(f"Could not store last_sources: {e}")

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
                    "call_date": payload.get("call_date") or payload.get("date_ymd"),
                    "text": payload.get("text"),
                })
            elif "requirement_text" in payload:
                sources.append({
                    "type": "requirement",
                    "category": payload.get("category"),
                    "sub_category": payload.get("sub_category"),
                    "change_type": payload.get("change_type"),
                    "confirmed_by": payload.get("confirmed_by"),
                    # Provenance: which meeting this requirement came from (verifiability).
                    "session": payload.get("session"),
                    "call_date": payload.get("call_date") or payload.get("date_ymd"),
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
            "greeting": intent_data.get("greeting"),
        }

    # Words an answer may OPEN with that already count as a greeting (so we don't add a second one).
    _GREETING_WORDS = frozenset({
        "hi", "hii", "hiya", "hey", "heya", "hello", "helo", "hallo", "yo",
        "greetings", "good", "morning", "afternoon", "evening",
    })

    @classmethod
    def _answer_greets(cls, answer: str) -> bool:
        """Does the answer ALREADY open with a greeting word? Checks only the FIRST word (so 'That…'
        or '## Deployment…' count as NO greeting), to avoid double-greeting."""
        tokens = (answer or "").strip().split()
        if not tokens:
            return False
        first = "".join(c for c in tokens[0].lower() if c.isalpha())
        return first in cls._GREETING_WORDS

    @classmethod
    def _greeting_prefixed(cls, prep: Dict, answer: str) -> str:
        """Prepend a short greeting (the understanding model detected one BY MEANING) ONLY when the
        answer does NOT already greet on its own. This adds a greeting to the fixed off-topic/not-found
        replies (which never greet) while leaving on-topic answers and case-(c) small-talk replies —
        which greet themselves — untouched, so nothing is ever double-greeted. No-op when no greeting."""
        prefix = greeting_prefix(prep.get("greeting"))
        if not prefix or cls._answer_greets(answer):
            return answer
        return f"{prefix}\n\n{answer}"

    async def process_query_stream(self, db: AsyncSession, session_id: uuid.UUID, query: str, user_id: Optional[str] = None):
        """Async generator yielding SSE event dicts.

        'delta' events carry answer text as it streams; a final 'done' event carries the
        session id, sources, and intent metadata. The assistant message is saved to history
        after the stream finishes.
        """
        session = await self.get_session(db, session_id)
        if not session or (user_id is not None and session.user_id != user_id):
            session = await self.create_session(db, user_id=user_id)

        # Social/small-talk (hi, bye, thanks) -> fixed in-character reply, no retrieval/LLM.
        social_reply = match_social_reply(query)
        if social_reply is not None:
            await self.add_message(db, session, "user", query)
            yield {"type": "delta", "text": social_reply}
            await self.add_message(db, session, "assistant", social_reply)
            await self.update_session_activity(db, session)
            yield {
                "type": "done",
                "session_id": str(session.id),
                "sources": [],
                "context_metadata": {"intent": "social"},
            }
            return

        prep = await self._prepare_answer(db, session, query)

        # Transform the PREVIOUS answer directly (summarize/shorten/simplify) — no retrieval.
        if prep.get("transform_previous"):
            collected = []
            async for delta in self.llm_service.transform_previous_answer_stream(
                prep["transform_instruction"], prep["previous_answer"]
            ):
                collected.append(delta)
                yield {"type": "delta", "text": delta}
            answer = "".join(collected)
            await self.add_message(db, session, "assistant", answer)
            await self.update_session_activity(db, session)
            yield {
                "type": "done",
                "session_id": str(session.id),
                "sources": [],
                "context_metadata": {"intent": "transform_previous"},
            }
            return

        # Structured count/list answers come straight from the store — send as one chunk.
        if prep.get("structured_answer"):
            answer = self._greeting_prefixed(prep, prep["structured_answer"])
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

        stream = self.llm_service.generate_answer_stream(
            query=query,
            search_results=prep["search_results"],
            conversation_history=prep["conversation_history"],
            customer_metadata=prep["customer_metadata"],
            has_context=prep["has_context"],
            transcript_map=prep["transcript_map"],
            dialogue_context=prep["dialogue_context"],
        )
        if not prep["has_context"]:
            # Refusal / small-talk replies are SHORT and may greet themselves (case c). Collect the
            # whole reply, then prepend a greeting ONLY if it doesn't already greet — this fixes the
            # "hi, how are you?" double-greeting while still greeting on off-topic/not-found.
            parts = []
            async for delta in stream:
                parts.append(delta)
            answer = self._greeting_prefixed(prep, "".join(parts))
            yield {"type": "delta", "text": answer}
        else:
            # On-topic answers greet themselves (via the prompt) and can be long — stream normally.
            collected = []
            async for delta in stream:
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