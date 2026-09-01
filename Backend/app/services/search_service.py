from app.services.embedding_service import EmbeddingService
from app.core.qdrant_client import QdrantVectorDB
from typing import List, Dict, Optional
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

class SearchService:
    """Vector search service with multi-country support"""

    def __init__(self):
        self.embedding_service = EmbeddingService()
        self.vector_db = QdrantVectorDB()

    @staticmethod
    def _fair_share_cut(ranked: List[Dict], limit: int) -> List[Dict]:
        """Cut a best-first MIXED result list down to `limit`, guaranteeing each source store
        (requirements / conversations) a fair share of the seats. Re-allocates seats only — never
        adds results that weren't retrieved; a store's unused share flows to the other store; final
        order stays best-first. No-op when everything fits or only one store is present."""
        if len(ranked) <= limit:
            return ranked

        def _store(r: Dict) -> str:
            return 'requirements' if 'requirement_text' in (r.get('payload') or {}) else 'conversations'

        by_store: Dict[str, List[Dict]] = {}
        for r in ranked:
            by_store.setdefault(_store(r), []).append(r)
        if len(by_store) <= 1:
            return ranked[:limit]

        share = max(1, limit // len(by_store))
        keep: List[Dict] = []
        seen = set()
        for lst in by_store.values():        # pass 1: per-store share, best-first within store
            for r in lst[:share]:
                keep.append(r)
                seen.add(id(r))
        for r in ranked:                      # pass 2: fill remaining seats globally, best-first
            if len(keep) >= limit:
                break
            if id(r) not in seen:
                keep.append(r)
                seen.add(id(r))
        keep.sort(key=lambda r: r.get('score', 0), reverse=True)
        return keep[:limit]
    
    def search(
        self,
        query: str,
        collection_name: str = "requirements",
        top_k: int = 5,
        min_score: float = 0.3,
        filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Search for relevant chunks with relevance threshold and target collection.
        If filters are provided, they are applied to the Qdrant search payload.
        """
        try:
            logger.info(f"Searching for: {query} in {collection_name} with filters: {filters}")
            
            query_embedding = self.embedding_service.generate_embedding(query)
            
            # Perform search
            results = self.vector_db.search(
                query_vector=query_embedding,
                limit=top_k * 2,  # Get more results to filter by score
                collection_name=collection_name,
                filters=filters
            )
            
            # Filter by minimum score
            filtered_results = [
                result for result in results 
                if result.get('score', 0) >= min_score
            ]
            
            # Limit to top_k after filtering
            filtered_results = filtered_results[:top_k]
            
            logger.info(f"Found {len(results)} total results, {len(filtered_results)} after score filtering (min_score={min_score})")
            
            # Log top results for debugging
            if filtered_results:
                logger.info(f"Top result score: {filtered_results[0].get('score', 0):.3f}")
                logger.info(f"Top result preview: {filtered_results[0]['payload'].get('chunk_text', '')[:100]}...")
            else:
                logger.warning(f"No results met minimum score threshold of {min_score}")
            
            return filtered_results
            
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return []

    def search_multi(
        self,
        query: str,
        collections: List[str],
        top_k: int = 15,
        min_score: float = 0.3,
        filters: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Embed the query ONCE and search several collections, then merge results by score and
        return the top_k overall. Used so discussion-type questions can pull from BOTH the
        requirements store and the conversation-transcript store in a single turn.

        DATE and SPEAKER are pushed INTO Qdrant as pre-filters (applied BEFORE ranking), so a
        date-scoped question retrieves only that date's chunks and cannot be crowded out by
        higher-scoring chunks from other dates (the old "cannot find for May 28" bug). 'date_ymd'
        is a normalized YYYY-MM-DD field present on BOTH stores (added at ingest + backfill), so a
        single filter works everywhere. SESSION stays a SOFT post-filter: session labels are
        free-form, so it narrows results when it matches but NEVER causes a false-empty.
        """
        try:
            filters = filters or {}
            want_date = filters.get('call_date')     # normalized YYYY-MM-DD by the query processor
            want_session = filters.get('session')    # free-form, e.g. "Grooming 28" -> soft filter
            want_speaker = filters.get('speaker')    # stored verbatim -> exact-match in Qdrant

            # DATE stays a hard Qdrant pre-filter (exact YYYY-MM-DD is safe). SPEAKER is now a SOFT
            # partial post-filter (below): an exact Qdrant match failed when the user gave a FIRST
            # NAME ("Prasad") but the data stores the full name ("Prasad Kadrikar") -> empty pool.
            qdrant_filters: Dict = {}
            if want_date:
                qdrant_filters['date_ymd'] = want_date
            qdrant_filters = qdrant_filters or None

            logger.info(
                f"Multi-search: '{query}' across {collections} | "
                f"qdrant_filters={qdrant_filters} soft_session={want_session}"
            )
            query_embedding = self.embedding_service.generate_embedding(query)  # embed once, reuse

            merged: List[Dict] = []
            for coll in collections:
                try:
                    res = self.vector_db.search(
                        query_vector=query_embedding,
                        limit=top_k * 2,
                        collection_name=coll,
                        filters=qdrant_filters,
                    )
                    merged.extend(res)
                except Exception as e:
                    logger.error(f"Multi-search failed for collection '{coll}': {e}")

            merged = [r for r in merged if r.get('score', 0) >= min_score]

            # Soft session narrowing — apply only if it leaves something (never a false-empty).
            if want_session:
                req = str(want_session).lower().replace('_', ' ')
                tokens = [t for t in req.split()
                          if t not in ('grooming', 'call', 'the', 'mom', 'meeting', 'a')]

                def _session_ok(r: Dict) -> bool:
                    stored = str((r.get('payload') or {}).get('session') or '').lower().replace('_', ' ')
                    return (req in stored) or (bool(tokens) and all(t in stored for t in tokens))

                subset = [r for r in merged if _session_ok(r)]
                if subset:
                    merged = subset

            # Soft SPEAKER narrowing — PARTIAL & case-insensitive (so "Prasad" matches "Prasad
            # Kadrikar"); applied only if it leaves something (never a false-empty).
            if want_speaker:
                sp = str(want_speaker).strip().lower()

                def _speaker_ok(r: Dict) -> bool:
                    stored = str((r.get('payload') or {}).get('speaker') or '').lower()
                    return bool(stored) and (sp in stored or stored in sp)

                subset = [r for r in merged if _speaker_ok(r)]
                if subset:
                    merged = subset

            merged.sort(key=lambda r: r.get('score', 0), reverse=True)
            # FAIR SEATS at the candidate-pool cut: guarantee each store a share of the pool so one
            # store's dense, keyword-rich texts can't eliminate the other store before reranking.
            merged = self._fair_share_cut(merged, top_k)

            logger.info(f"Multi-search merged to {len(merged)} results (min_score={min_score})")
            if merged:
                logger.info(f"Top merged score: {merged[0].get('score', 0):.3f}")
            return merged
        except Exception as e:
            logger.error(f"Error in multi-search: {e}")
            return []