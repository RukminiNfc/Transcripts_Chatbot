import cohere
from app.core.config import settings
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)


class Reranker:
    """
    Cohere-based re-ranker. Vector search returns a WIDE candidate pool ranked by raw embedding
    similarity — which lets a loud generic word (e.g. "documentation") pull in off-topic chunks.
    Cohere's purpose-built rerank model re-reads the pool against the user's actual question and
    returns only the genuinely relevant candidates, best-first — better relevance than an LLM
    rerank, and faster/cheaper.

    Safe by design: with NO API key, or on ANY error / empty result, it falls back to the original
    top-N by embedding score, so retrieval is never worse than plain vector search.
    """

    def __init__(self):
        self.model = settings.COHERE_RERANK_MODEL
        self.client = cohere.Client(api_key=settings.COHERE_API_KEY) if settings.COHERE_API_KEY else None

    @staticmethod
    def _candidate_text(payload: Dict) -> str:
        """Compact one-line description of a candidate for reranking."""
        if 'requirement_text' in payload:
            cat = payload.get('category', '')
            sub = payload.get('sub_category', '')
            return f"[Requirement | {cat} > {sub}] {payload.get('requirement_text', '')}"
        if 'speaker' in payload:
            return f"[Transcript | {payload.get('speaker', '')}] {payload.get('text', '')}"
        return str(payload)[:300]

    def rerank(self, query: str, candidates: List[Dict], top_n: int = 12,
               score_threshold: float = None) -> List[Dict]:
        """
        Return the most relevant candidates to `query`, best-first, at most `top_n`.
        `candidates` are search results ({'payload','score',...}). Falls back to candidates[:top_n]
        on any problem (missing key, API error, or no usable result).

        `score_threshold` overrides the default relevance cutoff. COMPLETE-requirements mode passes a
        higher cutoff and `top_n = len(candidates)` to keep EVERY genuinely-relevant item (driven by
        relevance, not a fixed cap) — so a whole-topic list is never trimmed.
        """
        if not candidates:
            return []
        custom = score_threshold is not None
        threshold = settings.RERANK_SCORE_THRESHOLD if not custom else score_threshold
        # Default path: nothing to trim by count -> skip the call (preserves prior behavior). In
        # custom-threshold (complete) mode we ALWAYS rerank so the cutoff is actually applied.
        if not custom and len(candidates) <= top_n:
            return candidates
        # No key configured -> behave like plain vector search (never worse).
        if not self.client:
            logger.warning("No COHERE_API_KEY set; skipping rerank (top-N by score).")
            return candidates[:top_n]

        try:
            docs = [self._candidate_text(c.get('payload', {}) or {})[:1500] for c in candidates]
            # Rank ALL docs (same API cost) so the fair-share cut below can see the full threshold-
            # passing order, not just the first top_n of one dominant store.
            resp = self.client.rerank(model=self.model, query=query, documents=docs, top_n=len(docs))

            # Drop chunks Cohere scores as clearly-weak: cuts noise (fewer hallucinations) and, when
            # NOTHING is relevant, leaves an empty result so the caller can answer "not found".
            picked: List[Dict] = []
            for r in resp.results:
                idx = r.index
                score = getattr(r, "relevance_score", 1.0)
                if isinstance(idx, int) and 0 <= idx < len(candidates) and score >= threshold:
                    picked.append(candidates[idx])

            # FAIR SEATS: when seats are contested, guarantee each store its share so dense
            # requirement sentences can't fully crowd out transcript passages (and vice versa).
            picked = self._fair_share(picked, top_n)

            logger.info(f"Cohere reranked {len(candidates)} -> kept {len(picked)} (score >= {threshold})")
            return picked

        except Exception as e:
            logger.error(f"Cohere rerank failed ({e}); falling back to top-N by score.")
            return candidates[:top_n]

    @staticmethod
    def _fair_share(ranked: List[Dict], limit: int) -> List[Dict]:
        """Cut a best-first, threshold-passing MIXED list down to `limit` while guaranteeing each
        source store (requirements / conversations) a fair share of the seats. Re-allocates seats
        only — never adds items that didn't pass the threshold; a store's unused share flows to the
        other store; final order stays best-first. No-op when everything fits (limit >= len), when
        only one store is present, or in complete/changes modes (they pass limit = len)."""
        if len(ranked) <= limit:
            return ranked

        def _store(c: Dict) -> str:
            return 'requirements' if 'requirement_text' in (c.get('payload') or {}) else 'conversations'

        by_store: Dict[str, List[Dict]] = {}
        for c in ranked:
            by_store.setdefault(_store(c), []).append(c)
        if len(by_store) <= 1:
            return ranked[:limit]

        share = max(1, limit // len(by_store))
        keep: List[Dict] = []
        seen = set()
        # Pass 1: each store gets up to its share, best-first within the store.
        for lst in by_store.values():
            for c in lst[:share]:
                keep.append(c)
                seen.add(id(c))
        # Pass 2: fill any remaining seats globally, best-first.
        for c in ranked:
            if len(keep) >= limit:
                break
            if id(c) not in seen:
                keep.append(c)
                seen.add(id(c))
        # Present in the original best-first order.
        order = {id(c): i for i, c in enumerate(ranked)}
        keep.sort(key=lambda c: order[id(c)])
        return keep[:limit]
