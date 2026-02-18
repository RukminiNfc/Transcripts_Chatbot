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
    jurisdiction: Optional[str] = None,
    guide_type: Optional[str] = None,
    document_id: Optional[str] = None,
    section: Optional[str] = None,  # NEW: Search within specific section
    top_k: int = 5,
    min_score: float = 0.3  # NEW: Minimum relevance score threshold
) -> List[Dict]:
        """
        Search for relevant chunks with optional section filtering and relevance threshold
        """
        try:
            logger.info(f"Searching for: {query}")
            logger.info(f"Filters - jurisdiction: {jurisdiction}, guide_type: {guide_type}, document_id: {document_id}, section: {section}")
            
            query_embedding = self.embedding_service.generate_embedding(query)
            
            # Build filters
            filters = {}
            if jurisdiction:
                filters['jurisdiction'] = jurisdiction
            if guide_type:
                filters['guide_type'] = guide_type
            if document_id:
                filters['document_id'] = document_id
            if section:  # NEW
                filters['level1_section'] = section
            
            # Perform search with higher limit to allow filtering
            results = self.vector_db.search(
                query_vector=query_embedding,
                limit=top_k * 2,  # Get more results to filter by score
                filters=filters if filters else None
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
        
    def search_multi_country(
        self,
        query: str,
        guide_type: Optional[str] = None,
        top_k_per_country: int = 3
    ) -> Dict[str, List[Dict]]:
        """
        Search across all countries and group by jurisdiction
        
        Args:
            query: Search query
            guide_type: Filter by category
            top_k_per_country: Results per country
            
        Returns:
            Dictionary with country as key, results as value
        """
        try:
            # Get all results
            all_results = self.search(
                query=query,
                guide_type=guide_type,
                top_k=50  # Get many results to distribute
            )
            
            # Group by jurisdiction
            grouped_results = defaultdict(list)
            for result in all_results:
                jurisdiction = result['payload'].get('jurisdiction', 'Unknown')
                if len(grouped_results[jurisdiction]) < top_k_per_country:
                    grouped_results[jurisdiction].append(result)
            
            # Convert to regular dict and sort by country
            result_dict = dict(grouped_results)
            sorted_result = {
                k: result_dict[k]
                for k in sorted(result_dict.keys())
            }
            
            logger.info(f"Results from {len(sorted_result)} countries")
            return sorted_result
            
        except Exception as e:
            logger.error(f"Error in multi-country search: {e}")
            return {}