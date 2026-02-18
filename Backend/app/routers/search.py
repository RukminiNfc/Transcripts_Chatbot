from fastapi import APIRouter, HTTPException
from app.services.search_service import SearchService
from app.models.schemas import SearchRequest
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"])

@router.post("/")
def search(request: SearchRequest):
    """
    Search documents
    
    Args:
        request: Search parameters
        
    Returns:
        Search results
    """
    try:
        search_service = SearchService()
        
        results = search_service.search(
            query=request.query,
            jurisdiction=request.jurisdiction,
            guide_type=request.guide_type,
            top_k=request.top_k
        )
        
        return {
            "query": request.query,
            "results": results,
            "count": len(results)
        }
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/multi-country")
def search_multi_country(request: SearchRequest):
    """
    Search across all countries with grouped results
    
    Args:
        request: Search parameters
        
    Returns:
        Results grouped by country
    """
    try:
        search_service = SearchService()
        
        results = search_service.search_multi_country(
            query=request.query,
            guide_type=request.guide_type,
            top_k_per_country=request.top_k
        )
        
        return {
            "query": request.query,
            "results_by_country": results,
            "countries_count": len(results)
        }
        
    except Exception as e:
        logger.error(f"Multi-country search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))