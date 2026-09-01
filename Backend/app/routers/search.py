from fastapi import APIRouter, Depends, HTTPException
from app.services.search_service import SearchService
from app.core.security import get_current_user
from app.models.schemas import SearchRequest
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(get_current_user)])

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