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

            # Exact-match pre-filters handled server-side by Qdrant (before ranking).
            qdrant_filters: Dict = {}
            if want_speaker:
                qdrant_filters['speaker'] = want_speaker
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

            merged.sort(key=lambda r: r.get('score', 0), reverse=True)
            merged = merged[:top_k]

            logger.info(f"Multi-search merged to {len(merged)} results (min_score={min_score})")
            if merged:
                logger.info(f"Top merged score: {merged[0].get('score', 0):.3f}")
            return merged
        except Exception as e:
            logger.error(f"Error in multi-search: {e}")
            return []