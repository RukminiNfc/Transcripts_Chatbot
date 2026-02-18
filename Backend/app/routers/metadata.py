"""
Metadata router for dynamic dropdown data
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import distinct
from app.core.database import get_db
from app.models.database import Document
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/metadata", tags=["metadata"])


@router.get("/guide-types", response_model=List[str])
def get_guide_types(db: Session = Depends(get_db)):
    """Get all unique guide types from documents"""
    results = db.query(distinct(Document.guide_type)).filter(
        Document.guide_type.isnot(None),
        Document.status == 'published'
    ).all()
    
    guide_types = [r[0] for r in results if r[0]]
    logger.info(f"Found {len(guide_types)} guide types")
    return sorted(guide_types)


@router.get("/jurisdictions", response_model=List[str])
def get_jurisdictions(
    guide_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all unique jurisdictions, optionally filtered by guide type"""
    query = db.query(distinct(Document.jurisdiction)).filter(
        Document.jurisdiction.isnot(None),
        Document.status == 'published'
    )
    
    if guide_type:
        query = query.filter(Document.guide_type == guide_type)
    
    results = query.all()
    jurisdictions = [r[0] for r in results if r[0]]
    logger.info(f"Found {len(jurisdictions)} jurisdictions")
    return sorted(jurisdictions)


@router.get("/documents")
def get_documents_for_filter(
    guide_type: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get documents for specific filter combination"""
    query = db.query(Document).filter(Document.status == 'published')
    
    if guide_type:
        query = query.filter(Document.guide_type == guide_type)
    if jurisdiction:
        query = query.filter(Document.jurisdiction == jurisdiction)
    
    documents = query.order_by(Document.title).all()
    
    return [
        {
            "id": str(doc.id),
            "title": doc.title,
            "filename": doc.filename,
            "guide_type": doc.guide_type,
            "jurisdiction": doc.jurisdiction
        }
        for doc in documents
    ]


@router.get("/hierarchy")
def get_metadata_hierarchy(db: Session = Depends(get_db)):
    """Get full hierarchy of guide types and their jurisdictions"""
    results = db.query(
        Document.guide_type,
        Document.jurisdiction
    ).filter(
        Document.status == 'published',
        Document.guide_type.isnot(None),
        Document.jurisdiction.isnot(None)
    ).distinct().all()
    
    # Group by guide type
    hierarchy = {}
    for guide_type, jurisdiction in results:
        if guide_type not in hierarchy:
            hierarchy[guide_type] = []
        hierarchy[guide_type].append(jurisdiction)
        
    # Format as list of objects for frontend
    response = []
    for guide_type, jurisdictions in hierarchy.items():
        response.append({
            "guide_type": guide_type,
            "countries": sorted(jurisdictions)
        })
        
    return sorted(response, key=lambda x: x['guide_type'])
