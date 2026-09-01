from typing import List, Dict, Any, Optional
import asyncio
import json
import logging
import uuid
from datetime import datetime
from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from app.core.config import settings
from app.core.qdrant_client import QdrantVectorDB
from app.services.embedding_service import EmbeddingService
from app.models.database import Requirement, RequirementVersion
from app.utils.dates import session_to_ymd

logger = logging.getLogger(__name__)

# --- Candidate retrieval for meaning-based matching (Step 3) ---
# Embeddings only NARROW the field to a few candidates; the LLM makes the final
# same/modified/new decision by meaning. We deliberately do NOT use a high score
# threshold as a gate — re-worded requirements often score low but still match.
CANDIDATE_TOP_K = 5          # how many nearest existing requirements to consider
MIN_CANDIDATE_SCORE = 0.35   # drop only clearly-unrelated candidates (keep borderline for the LLM)

# How many Qdrant-search + LLM-match calls to run at once. The per-requirement analysis
# (search + match-classify) is independent across requirements, so we run several in
# parallel to cut wall-clock time. Capped to stay well under OpenAI rate limits.
MATCH_CONCURRENCY = 8

# --- Legacy 3-tier thresholds (no longer used for gating; kept for reference) ---
TIER_1_HIGH_CONFIDENCE = 0.90
TIER_2_GRAY_AREA = 0.80

class RequirementComparisonService:
    """Compares new requirements against Store 2 to detect changes.
    
    Uses a 3-tier semantic similarity system:
    - Tier 1 (>= 0.90): High confidence same topic. LLM checks MODIFIED vs UNCHANGED.
    - Tier 2 (0.80 - 0.90): Gray area. LLM checks if they are truly the SAME requirement or DIFFERENT.
    - Tier 3 (< 0.80): Low confidence. Treated as a brand new 'added' requirement.
    
    Embeddings are generated from the normalized 'canonical_text' (not raw text)
    to reduce false positives caused by AI rephrasing the same thing differently.
    """
    
    def __init__(self):
        self.qdrant = QdrantVectorDB()
        self.embedding_service = EmbeddingService()
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=6, timeout=120.0)
        self.model = settings.OPENAI_COMPARISON_MODEL
        
    async def process_and_compare(
        self, 
        extracted_reqs: List[Dict[str, Any]], 
        db: AsyncSession,
        customer_id: uuid.UUID,
        session_name: str,
        call_date: datetime,
        transcript_id: uuid.UUID
    ) -> List[Dict[str, Any]]:
        """
        Takes extracted requirements, finds matches, classifies changes, 
        and saves to DB/Qdrant. Returns the full enriched list.
        """
        logger.info(f"Comparing {len(extracted_reqs)} extracted requirements")
        processed_results = []

        # Normalized filterable date (YYYY-MM-DD), same field/logic as the conversations store.
        date_ymd = session_to_ymd(session_name, call_date)

        # Collect this batch's vectors and write them to Qdrant only AFTER all comparisons
        # are done. Writing inside the loop would let a later requirement match an earlier
        # one from the SAME upload (false "modified"). We must compare only against
        # requirements that existed BEFORE this transcript.
        pending_vectors: List[List[float]] = []
        pending_payloads: List[Dict[str, Any]] = []
        pending_ids: List[str] = []

        # Claim-guard: an existing requirement may be matched by at most ONE new requirement
        # per upload. If a second new requirement matches an already-claimed existing one,
        # it is a distinct new requirement → treat it as "added" instead of overwriting.
        claimed_req_ids: set = set()

        # Supersede set: ids of requirements that already existed (matched as modified/unchanged).
        # Their OLD version vectors are removed from the search store before this batch is
        # written, so only ONE current vector per requirement remains (no stale duplicates).
        # The full version history is untouched — it lives in Postgres (RequirementVersion).
        superseded_ids: set = set()

        # ── PHASE 1: batch-embed every requirement in ONE call (was one call each). ──
        canonicals = [r.get('canonical_text', r['requirement_text']) for r in extracted_reqs]
        vectors = self.embedding_service.generate_embeddings(canonicals)

        # ── PHASE 2: search Qdrant + classify with the LLM, IN PARALLEL across requirements. ──
        # These are read-only, order-independent network calls, so running several at once
        # (bounded by MATCH_CONCURRENCY) collapses ~N sequential round-trips into ~N/8 waves.
        # No DB writes and no Qdrant writes happen here — every candidate set is compared only
        # against requirements that existed BEFORE this upload (siblings are written in Phase 4).
        sem = asyncio.Semaphore(MATCH_CONCURRENCY)

        async def analyze(index: int) -> Dict[str, Any]:
            async with sem:
                return await asyncio.to_thread(
                    self._search_and_match, canonicals[index], vectors[index], customer_id
                )

        analyses = await asyncio.gather(*[analyze(i) for i in range(len(extracted_reqs))])

        # ── PHASE 3: apply results sequentially (claim-guard + DB writes must stay in order). ──
        for idx, req in enumerate(extracted_reqs):
            req_text = req['requirement_text']
            canonical = canonicals[idx]
            vector = vectors[idx]
            candidates = analyses[idx]["candidates"]
            decision = analyses[idx]["decision"]

            change_type = "added"
            matched_db_req = None
            old_text = None
            old_date = None
            old_session = None
            match_score = 0.0

            if candidates:
                matched_idx = decision["match"]

                if matched_idx is not None:
                    best = candidates[matched_idx]
                    matched_req_id = best['payload']['requirement_id']

                    if str(matched_req_id) in claimed_req_ids:
                        # Another new requirement already matched this existing one this upload.
                        # This is a distinct new requirement → add it (don't overwrite again).
                        logger.info(
                            f"Existing requirement {matched_req_id} already claimed this upload → treating as added"
                        )
                        change_type = "added"
                    else:
                        match_score = best.get('score', 0.0)
                        best_payload = best['payload']
                        # Use the existing raw text for the email diff (more readable than canonical)
                        old_text = best_payload.get('requirement_text', best_payload.get('canonical_text', ''))
                        # Capture WHEN the previous version is from, so the email can show
                        # "Before (from <that call/date>)". Derive the date from the session NAME
                        # (single source of truth) rather than the stored timestamp, which may be
                        # day/month-swapped or timezone-shifted in older records.
                        old_session = best_payload.get('session', '')
                        old_date = session_to_ymd(
                            old_session, best_payload.get('date_ymd') or best_payload.get('discussed_date')
                        )

                        result = await db.execute(select(Requirement).filter(Requirement.id == matched_req_id))
                        matched_db_req = result.scalars().first()

                        if matched_db_req:
                            change_type = decision["status"]  # "modified" or "unchanged"
                            claimed_req_ids.add(str(matched_req_id))  # claim it for this upload
                            logger.info(
                                f"LLM matched existing requirement (vector score {match_score:.3f}) → {change_type}"
                            )
                        else:
                            # Matched a vector whose DB row no longer exists — treat as new
                            logger.info("Matched vector has no DB row; treating as added")
                            change_type = "added"
                else:
                    logger.info("LLM found no matching existing requirement → added")
                    change_type = "added"
                    
            # 1. Save to PostgreSQL
            new_req_id = uuid.uuid4()

            if change_type == "added" or matched_db_req is None:
                # Create NEW Requirement
                db_req = Requirement(
                    id=new_req_id,
                    customer_id=customer_id,
                    category=req['category'],
                    sub_category=req['sub_category'],
                    current_text=req_text,
                    canonical_text=canonical,
                    status="active"
                )
                db.add(db_req)
                await db.flush()
                matched_db_req = db_req
            elif change_type == "modified":
                # Update EXISTING Requirement
                matched_db_req.current_text = req_text
                matched_db_req.canonical_text = canonical
                matched_db_req.category = req['category']
                matched_db_req.sub_category = req['sub_category']
                
            # Create Version Audit Trail
            # Calculate next version number
            count_result = await db.execute(
                select(func.count()).select_from(RequirementVersion)
                .filter(RequirementVersion.requirement_id == matched_db_req.id)
            )
            prev_versions = count_result.scalar() or 0
            version_num = prev_versions + 1
            
            version_id = uuid.uuid4()
            req_version = RequirementVersion(
                id=version_id,
                requirement_id=matched_db_req.id,
                version_number=version_num,
                text=req_text,
                change_type=change_type,
                confirmed_by=req['confirmed_by'],
                proposed_by=req.get('proposed_by', ''),
                discussed_date=call_date,
                session=session_name,
                transcript_id=transcript_id,
                # Qdrant point id for this vector == the version id. Persisting it here is what
                # lets a later transcript-delete actually remove this vector from Qdrant.
                vector_id=str(version_id)
            )
            db.add(req_version)
            await db.commit()
            
            # 2. Save to Qdrant (Store 2) — use canonical_text for the vector
            payload = {
                "version_id": str(req_version.id),
                "requirement_id": str(matched_db_req.id),
                "transcript_id": str(transcript_id),
                "customer_id": str(customer_id),
                "category": req['category'],
                "sub_category": req['sub_category'],
                "requirement_text": req_text,
                "canonical_text": canonical,
                "confirmed_by": req['confirmed_by'],
                "proposed_by": req.get('proposed_by', ''),
                "change_type": change_type,
                "session": session_name,
                "discussed_date": call_date.isoformat(),
                "date_ymd": date_ymd
            }
            
            # Defer the Qdrant write — collect now, upsert the whole batch after the loop
            # so siblings from this same upload are NOT candidates for each other.
            pending_vectors.append(vector)
            pending_payloads.append(payload)
            pending_ids.append(str(req_version.id))

            # If this requirement already existed (modified/unchanged), mark its prior vectors
            # for removal so the new one REPLACES them instead of piling up in the search store.
            if change_type in ("modified", "unchanged"):
                superseded_ids.add(str(matched_db_req.id))

            # Attach details for the email service
            req['change_type'] = change_type
            req['old_text'] = old_text
            req['old_date'] = old_date        # date the previous version was discussed
            req['old_session'] = old_session  # call the previous version came from
            # The concrete, verified reason this counts as a change (empty unless modified) —
            # used as the email headline so recipients see WHAT changed, not two prose blobs.
            req['change_summary'] = decision.get("change_summary", "") if change_type == "modified" else ""
            processed_results.append(req)

            logger.info(f"Requirement [{req['category']}/{req['sub_category']}] → {change_type} (score: {match_score:.4f})")

        # ── PHASE 4: now that all comparisons are done, write this batch's vectors to Qdrant. ──
        # First supersede: remove the OLD version vectors of every requirement that already
        # existed, so the search store keeps only ONE current vector per requirement. This runs
        # BEFORE add_vectors, and the new vectors aren't in Qdrant yet, so it only clears stale
        # ones. History stays intact in Postgres; date-scoped questions read Postgres, not this.
        for rid in superseded_ids:
            self.qdrant.delete_by_filter(
                self.qdrant.requirements_collection, {"requirement_id": rid}
            )
        if superseded_ids:
            logger.info(f"Superseded old vectors for {len(superseded_ids)} existing requirement(s)")

        if pending_vectors:
            self.qdrant.add_vectors(
                vectors=pending_vectors,
                payloads=pending_payloads,
                ids=pending_ids,
                collection_name=self.qdrant.requirements_collection
            )
            logger.info(f"Upserted {len(pending_vectors)} requirement vectors to Qdrant (post-comparison)")

        return processed_results

    @staticmethod
    def _is_next_gen(model: str) -> bool:
        """GPT-5 / o-series use newer API conventions (max_completion_tokens, fixed temperature)."""
        return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))

    def _chat(self, model, messages, max_tokens=None, temperature=None, json_mode=False):
        """Call chat.completions with model-appropriate params; fall back to gpt-4o-mini on failure."""
        def build(m, include_max=True):
            kw = {"model": m, "messages": messages}
            if json_mode:
                kw["response_format"] = {"type": "json_object"}
            if self._is_next_gen(m):
                if include_max and max_tokens is not None:
                    kw["max_completion_tokens"] = max_tokens
            else:
                if max_tokens is not None:
                    kw["max_tokens"] = max_tokens
                if temperature is not None:
                    kw["temperature"] = temperature
            return kw

        try:
            return self.client.chat.completions.create(**build(model))
        except TypeError as exc:
            if "max_completion_tokens" in str(exc):
                logger.warning(f"SDK lacks max_completion_tokens; calling '{model}' without a token cap.")
                try:
                    return self.client.chat.completions.create(**build(model, include_max=False))
                except Exception as exc2:
                    logger.error(f"Model '{model}' failed without cap ({exc2}); falling back to gpt-4o-mini.")
                    return self.client.chat.completions.create(**build("gpt-4o-mini", include_max=False))
            logger.error(f"Model '{model}' TypeError ({exc}); falling back to gpt-4o-mini.")
            return self.client.chat.completions.create(**build("gpt-4o-mini", include_max=False))
        except Exception as exc:
            logger.error(f"Model '{model}' call failed ({exc}); falling back to gpt-4o-mini.")
            return self.client.chat.completions.create(**build("gpt-4o-mini", include_max=False))

    def _search_and_match(self, canonical: str, vector: List[float], customer_id: uuid.UUID) -> Dict[str, Any]:
        """
        The read-only, order-independent unit of Step 3 for ONE requirement — safe to run in
        parallel across requirements (see MATCH_CONCURRENCY). Does NOT touch the DB or write to
        Qdrant. Returns {"candidates": [...], "decision": {"match", "status"}}.

        Retrieve the top-K nearest existing requirements (embeddings narrow the field cheaply),
        then let the LLM decide if any is the SAME requirement and, if so, whether it changed.
        The score is NOT a gate — borderline matches still reach the LLM, so re-worded
        requirements are caught instead of piling up as new.
        """
        candidates = self.qdrant.search(
            query_vector=vector,
            limit=CANDIDATE_TOP_K,
            filters={"customer_id": str(customer_id)},
            collection_name=self.qdrant.requirements_collection
        )
        candidates = [c for c in candidates if c.get('score', 0.0) >= MIN_CANDIDATE_SCORE]

        if candidates:
            decision = self._match_and_classify(canonical, candidates)
        else:
            decision = {"match": None, "status": "unchanged"}

        return {"candidates": candidates, "decision": decision}

    def _match_and_classify(self, new_text: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Step 3 core — one LLM call that decides, by MEANING:
          - which existing candidate (if any) is the SAME requirement as new_text, and
          - whether it is "modified" (a detail changed) or "unchanged".
        Returns {"match": <index into candidates or None>, "status": "modified"|"unchanged"}.
        Safe fallback on error: no match (treated as a new requirement).
        """
        lines = []
        for i, c in enumerate(candidates):
            payload = c.get('payload', {})
            existing = payload.get('canonical_text') or payload.get('requirement_text', '')
            lines.append(f"{i}. {existing}")
        candidate_block = "\n".join(lines)

        prompt = f'''You are an expert AI requirement analyst. Follow this exact Chain of Thought to decide if a new requirement matches an existing one, and if it represents a functional change.

NEW REQUIREMENT:
"{new_text}"

EXISTING REQUIREMENTS:
{candidate_block}

=== STEP 1: MATCH (Extract Business Intent & Compare Actions) ===
Analyze the core business intent of the NEW requirement. Then check the existing requirements.
- If an existing requirement shares the exact same core business intent (e.g. both dictate "Rollbacks" or both dictate "AppSettings"), you MUST MATCH THEM, even if the phrasing is completely different (e.g. "task" vs "stage"). 
- Do NOT create duplicates of overlapping rules! Merge them by selecting the matching index.
- Only choose `null` if they govern entirely separate features (e.g. "deploy ordering" vs "deploy scope", or "UI design" vs "API structure").

=== STEP 2: CLASSIFY (Analyze Technical Details) ===
Only if you matched an existing requirement, ask yourself: Was a new behavior introduced?
- Compare actions, constraints, and business outcomes.
- If the NEW version fundamentally alters the business intent, technical implementation, or core behavior (e.g. changing from "Manual" to "Automated", or from "Git" to "Azure"):
  -> status: "modified". Name the alteration in "change_summary".
- If it is ONLY a wording difference, an implementation detail, an added example, a clarification, or states the rule with less detail without changing the core outcome:
  -> status: "unchanged". 

Respond with ONLY JSON in this exact shape:
{{"reasoning": "<step-by-step analysis>", "match": <index number, or null>, "status": "modified" or "unchanged", "change_summary": "<one concrete sentence naming what changed, or empty>", "confidence": "high" or "medium" or "low"}}'''

        try:
            response = self._chat(
                self.model,
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                json_mode=True,
            )
            data = json.loads(response.choices[0].message.content)
            match = data.get("match", None)
            status = (data.get("status") or "unchanged").strip().lower()
            change_summary = (data.get("change_summary") or "").strip()
            confidence = (data.get("confidence") or "medium").strip().lower()

            # Normalise / validate the match index
            if isinstance(match, str):
                match = int(match) if match.strip().isdigit() else None
            if match is not None and (not isinstance(match, int) or match < 0 or match >= len(candidates)):
                match = None
            if status not in ("modified", "unchanged"):
                status = "unchanged"  # raised bar: if the model is unsure, do NOT treat as a change
            if confidence not in ("high", "medium", "low"):
                confidence = "medium"

            # ── LAYER 3 (adversarial verify): only a matched "modified" proceeds. A 2nd,
            #    independent call tries to prove it is the SAME rule reworded; it survives as a
            #    change ONLY if that challenge fails.
            # ── LAYER 4 (confidence gate): a low-confidence change is NOT emailed — it is
            #    downgraded to "unchanged" and logged for human review.
            if status == "modified" and match is not None:
                old_text = (candidates[match].get('payload', {}).get('canonical_text')
                            or candidates[match].get('payload', {}).get('requirement_text', ''))
                # ── IDENTITY GATE (precision-first): before treating this as a modification,
                #    confirm OLD and NEW are the SAME specific requirement — not two DIFFERENT
                #    requirements that merely share a topic/area. If different, this is a NEW
                #    requirement → drop the match (becomes "added") and send NO email.
                if not self._verify_same_requirement(old_text, new_text):
                    logger.info(f"REVIEW: matched candidate is a DIFFERENT requirement (same area only) "
                                f"→ treating as ADDED, not modified. new='{new_text[:60]}'")
                    return {"match": None, "status": "unchanged", "change_summary": "", "confidence": confidence}
                if not self._verify_change(old_text, new_text, change_summary):
                    logger.info(f"REVIEW: verifier judged a REWORD, not a change → unchanged. "
                                f"summary='{change_summary}' | new='{new_text[:60]}'")
                    status, change_summary = "unchanged", ""
                elif confidence == "low":
                    logger.info(f"REVIEW: low-confidence change withheld from email (→ unchanged). "
                                f"summary='{change_summary}' | new='{new_text[:60]}'")
                    status, change_summary = "unchanged", ""

            logger.info(f"LLM match-and-classify: match={match}, status={status}, confidence={confidence}")
            return {"match": match, "status": status,
                    "change_summary": change_summary, "confidence": confidence}
        except Exception as e:
            logger.error(f"Error in match-and-classify: {e}; defaulting to no match (added).")
            return {"match": None, "status": "unchanged", "change_summary": "", "confidence": "low"}

    def _verify_same_requirement(self, old_text: str, new_text: str) -> bool:
        """IDENTITY GATE — is NEW the SAME specific requirement as OLD (an updated version of the
        same rule), or a DIFFERENT requirement that merely shares a topic/area? Returns True only
        when they are clearly the same specific requirement. Biased toward DIFFERENT when unsure,
        so unrelated requirements in the same area are NOT reported as modifications (no false
        email). Safe fallback on error: True (defer to the primary decision; change-verify guards).
        """
        prompt = f'''Two requirement statements were proposed as the SAME requirement — one being
an updated version of the other. Decide whether that is actually true.

OLD: "{old_text}"
NEW: "{new_text}"

They are the SAME requirement ONLY IF they govern the SAME specific subject AND the SAME aspect
of it (the same field/screen/rule/step), so that NEW is clearly an updated version of OLD.

They are DIFFERENT requirements if they cover different subjects, different aspects, or rules
that could BOTH independently be true at the same time — EVEN IF they sit in the same feature or
area. Being in the same general topic (e.g. "deployment") is NOT enough to be the same requirement.

Examples that are DIFFERENT (answer "DIFFERENT"):
- "allow selecting the target environment and branch" vs "use dropdown values for parameters"
  (WHAT you select vs HOW you select — two separate rules).
- "API and UI deploy separately, API first" vs "UI deploys only its configured folder"
  (deploy ordering vs deploy scope — different aspects).
- "put lesson-plan info in order comments" vs "hide Comments-for-Employees on non-normal orders"
  (different features that merely share the word "comments").

When you are unsure, answer "DIFFERENT".
Answer with ONLY ONE WORD: "SAME" or "DIFFERENT".'''
        try:
            resp = self._chat(self.model, [{"role": "user", "content": prompt}], temperature=0.0)
            ans = (resp.choices[0].message.content or "").strip().upper()
            return "DIFFERENT" not in ans  # SAME only when it did not say DIFFERENT
        except Exception as e:
            logger.error(f"Error in _verify_same_requirement: {e}; deferring to primary decision.")
            return True

    def _verify_change(self, old_text: str, new_text: str, proposed_change: str) -> bool:
        """LAYER 3 — adversarial verification. A requirement was flagged as CHANGED; this
        independent pass CHALLENGES that by arguing the two are the SAME rule, only reworded.
        Returns True only if a genuine, material change survives the challenge; False if it is
        really just a rewording (caller then downgrades to 'unchanged' → no false email).
        Safe fallback on error: True (keep the primary 'modified' decision — never silently drop).
        """
        prompt = f'''Two requirement statements for the SAME feature were flagged as a CHANGE.
Your job is to CHALLENGE that flag as strictly as possible.

OLD: "{old_text}"
NEW: "{new_text}"
Claimed change: "{proposed_change or '(none given)'}"

Decide by comparing WHAT each version requires:
- Answer "SAME" if the NEW only rephrases the OLD, states the rule with LESS detail, OR simply
  adds explanations, real-world EXAMPLES, or background context that do not alter the core
  business rule. Elaborations and examples are NOT a change.
- Answer "CHANGED" ONLY if the NEW genuinely alters the business intent, technical implementation,
  or core behavior (e.g. "Git" → "Azure", True → False, "Send Email" → "Create Ticket").
Respond with ONLY ONE WORD: "SAME" or "CHANGED".'''
        try:
            resp = self._chat(self.model, [{"role": "user", "content": prompt}], temperature=0.0)
            ans = (resp.choices[0].message.content or "").strip().upper()
            return "CHANGED" in ans
        except Exception as e:
            logger.error(f"Error in _verify_change: {e}; keeping primary 'modified' decision.")
            return True

    def _check_if_modified(self, old_text: str, new_text: str) -> bool:
        """
        TIER 1 & 2 — Uses LLM to decide if two requirement texts are functionally different.
        Both texts should ideally be canonical (normalized) for best results.
        """
        prompt = f'''Compare these two requirement statements.
OLD: "{old_text}"
NEW: "{new_text}"

Are they functionally the EXACT SAME requirement just reworded (Unchanged), or has the scope, meaning, or details changed (Modified)?
Focus on FUNCTIONAL differences — changes to people, numbers, dates, actions, or constraints.
Ignore minor grammar, phrasing, or word-order differences.
Respond with ONLY ONE WORD: "MODIFIED" or "UNCHANGED".'''

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )
            decision = response.choices[0].message.content.strip().upper()
            logger.info(f"LLM modification check: '{decision}' | OLD: '{old_text[:50]}' | NEW: '{new_text[:50]}'")
            return "MODIFIED" in decision
        except Exception as e:
            logger.error(f"Error diffing texts: {e}")
            return True # Safe fallback is to flag it as modified

    def _check_if_same_requirement(self, old_text: str, new_text: str) -> bool:
        """
        TIER 2 ONLY — Used in the gray area (0.80-0.90 similarity).
        Asks the LLM whether these are actually the SAME requirement or two DIFFERENT 
        requirements that happen to be topically related.
        """
        prompt = f'''You are a business analyst. Determine if these two statements refer to the EXACT SAME software requirement or feature, 
or if they are two DIFFERENT requirements that happen to be in a similar area.

Statement A: "{old_text}"
Statement B: "{new_text}"

Rules:
- "SAME" means they are about the exact same feature, action, or rule — even if worded differently.
- "DIFFERENT" means they are about two separate features, even if they are in the same module or category.

Example of SAME:
  A: "Restrict hospitality emails to Rob, Anne Marie, Cassandra."
  B: "Send hospitality order notifications only to Rob, Anne Marie, and Cassandra."

Example of DIFFERENT:
  A: "Restrict hospitality emails to Rob, Anne Marie, Cassandra."
  B: "Display hospitality order status on the dashboard."

Respond with ONLY ONE WORD: "SAME" or "DIFFERENT".'''

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )
            decision = response.choices[0].message.content.strip().upper()
            logger.info(f"LLM identity check (Tier 2): '{decision}' | A: '{old_text[:50]}' | B: '{new_text[:50]}'")
            return "SAME" in decision
        except Exception as e:
            logger.error(f"Error in Tier 2 identity check: {e}")
            return False # Safe fallback: treat as different → mark as added
