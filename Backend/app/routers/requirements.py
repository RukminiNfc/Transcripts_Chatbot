from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Dict, Any
import logging
import uuid
import tempfile
import os
import hashlib
from datetime import datetime
from app.core.database import get_db
from app.models.database import Customer, Requirement, Transcript, ConversationLog, RequirementVersion
from app.core.qdrant_client import QdrantVectorDB
from app.services.transcript_parser import TranscriptParserService
from app.services.requirement_extraction import RequirementExtractionService
from app.services.requirement_comparison import RequirementComparisonService
from app.services.notification_service import NotificationService
from app.services.embedding_service import EmbeddingService
from app.utils.dates import session_to_ymd, session_to_datetime
from app.core.security import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/requirements",
    tags=["Requirements Tracking"],
    dependencies=[Depends(require_admin)],   # entire router is admin-only
)

# Initialize Services
parser_service = TranscriptParserService()
extraction_service = RequirementExtractionService()
comparison_service = RequirementComparisonService()
notification_service = NotificationService()

@router.post("/transcript")
async def upload_transcript(
    customer_id: uuid.UUID = Form(...),
    session_name: str = Form(...),
    call_date_str: str = Form(..., alias="call_date"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a grooming call transcript (.docx).
    Runs the full pipeline: Parse -> Extract -> Compare -> Notify
    """
    if not file.filename.endswith('.docx'):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")
        
    try:
        provided_date = datetime.fromisoformat(call_date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="call_date must be a valid ISO format string")

    # Authoritative call date comes from the SESSION NAME (MM-DD-YY) — the single source of
    # truth. The frontend value can be day/month-swapped or timezone-shifted, so fall back to
    # it only when the session name has no recognizable date. Noon-UTC keeps the calendar date
    # stable across timezones (midnight would slip to the previous day in IST).
    call_date = session_to_datetime(session_name, provided_date)
        
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalars().first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
        
    # Calculate file hash to prevent duplicates
    file_bytes = await file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    
    # Check if duplicate exists
    duplicate_result = await db.execute(select(Transcript).filter(Transcript.file_hash == file_hash))
    duplicate = duplicate_result.scalars().first()
    if duplicate:
        upload_date_str = duplicate.upload_date.strftime('%Y-%m-%d %H:%M:%S') if duplicate.upload_date else "an earlier date"
        raise HTTPException(
            status_code=409, 
            detail=f"Duplicate detected: This exact transcript was already uploaded and processed on {upload_date_str}."
        )
        
    # Ensure uploads directory exists
    uploads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    
    transcript_id = uuid.uuid4()
    temp_path = os.path.join(uploads_dir, f"{transcript_id}.docx")
    
    try:
        # Save file to disk so Celery can read it
        with open(temp_path, 'wb') as f:
            f.write(file_bytes)
            
        # Save Transcript Metadata with status="processing"
        db_transcript = Transcript(
            id=transcript_id,
            customer_id=customer_id,
            filename=file.filename,
            session_name=session_name,
            call_date=call_date,
            status="processing",
            file_hash=file_hash,
            total_blocks=0
        )
        db.add(db_transcript)
        await db.commit()
        
        # Dispatch Celery Task
        from app.worker.tasks import process_transcript_task
        task = process_transcript_task.delay(
            str(transcript_id),
            temp_path,
            str(customer_id),
            session_name,
            call_date_str
        )
        
        # Save the Celery task ID to the transcript for tracking
        db_transcript.celery_task_id = task.id
        await db.commit()
        
        return {
            "message": "Transcript uploaded and processing started in background",
            "transcript_id": str(transcript_id),
            "task_id": task.id
        }
        
    except Exception as e:
        # If something fails before Celery is dispatched, clean up the file
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"Failed to start processing: {str(e)}")


@router.get("/task/{transcript_id}")
async def get_task_status(transcript_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Check the status of a transcript being processed in the background.
    """
    result = await db.execute(select(Transcript).filter(Transcript.id == transcript_id))
    transcript = result.scalars().first()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
        
    return {
        "transcript_id": str(transcript.id),
        "status": transcript.status,
        "celery_task_id": transcript.celery_task_id,
        "summary": transcript.processing_summary
    }


@router.get("/customer/{customer_id}")
async def get_customer_requirements(customer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get active requirements for a specific customer"""
    result = await db.execute(
        select(Requirement).filter(
            Requirement.customer_id == customer_id,
            Requirement.status == "active"
        )
    )
    reqs = result.scalars().all()
    
    # Get the latest discussed_date for each requirement from RequirementVersion
    req_ids = [r.id for r in reqs]
    latest_dates = {}
    if req_ids:
        versions_result = await db.execute(
            select(RequirementVersion)
            .filter(RequirementVersion.requirement_id.in_(req_ids))
            .order_by(RequirementVersion.version_number.desc())
        )
        versions = versions_result.scalars().all()
        for v in versions:
            if v.requirement_id not in latest_dates:
                latest_dates[v.requirement_id] = v.discussed_date
    
    return [
        {
            "id": r.id,
            "category": r.category,
            "sub_category": r.sub_category,
            "current_text": r.current_text,
            "updated_at": r.updated_at or r.created_at,
            "transcript_date": latest_dates.get(r.id)
        } for r in reqs
    ]


@router.get("/customer/{customer_id}/export")
async def export_requirements(customer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Export consolidated requirements"""
    req_result = await db.execute(
        select(Requirement).filter(
            Requirement.customer_id == customer_id,
            Requirement.status == "active"
        )
    )
    reqs = req_result.scalars().all()
    
    cust_result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = cust_result.scalars().first()
    
    return {
        "title": f"Consolidated Requirements - {customer.name if customer else 'Unknown'}",
        "export_date": datetime.utcnow().isoformat(),
        "requirements": [
            {
                "category": r.category,
                "sub_category": r.sub_category,
                "requirement": r.current_text
            } for r in reqs
        ]
    }

@router.get("/transcripts")
async def get_transcripts(db: AsyncSession = Depends(get_db)):
    """Get all uploaded transcripts (metadata)"""
    result = await db.execute(select(Transcript).order_by(Transcript.upload_date.desc()))
    transcripts = result.scalars().all()
    
    return [
        {
            "id": str(t.id),
            "filename": t.filename,
            "session_name": t.session_name,
            "call_date": t.call_date.isoformat(),
            "upload_date": t.upload_date.isoformat(),
            "status": t.status,
            "total_blocks": t.total_blocks
        } for t in transcripts
    ]


@router.delete("/transcript/{transcript_id}")
async def delete_transcript(transcript_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Full cascade delete of a grooming call transcript.
    Removes from:
      - Qdrant 'conversations' collection (vectors for this transcript)
      - Qdrant 'requirements' collection (vectors for this transcript's requirement versions)
      - PostgreSQL: conversation_logs, requirement_versions, orphaned requirements, transcript record
    """
    # 1. Find the transcript
    result = await db.execute(select(Transcript).filter(Transcript.id == transcript_id))
    transcript = result.scalars().first()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    transcript_id_str = str(transcript_id)
    qdrant = QdrantVectorDB()

    # 2. Get all requirement_versions for this transcript to collect their Qdrant vector_ids
    versions_result = await db.execute(
        select(RequirementVersion).filter(RequirementVersion.transcript_id == transcript_id)
    )
    versions = versions_result.scalars().all()
    req_vector_ids = [v.vector_id for v in versions if v.vector_id]
    affected_req_ids = list({v.requirement_id for v in versions})

    logger.info(
        f"Cascade deleting transcript {transcript_id_str}: "
        f"{len(versions)} versions, {len(req_vector_ids)} Qdrant requirement vectors"
    )

    # 3. Delete from Qdrant — conversations vectors for this transcript
    qdrant.delete_by_filter(
        collection_name=qdrant.conversations_collection,
        filters={"transcript_id": transcript_id_str}
    )

    # 4. Delete from Qdrant — requirement vectors for this transcript.
    #    Primary path: a transcript_id FILTER — robust, works even if a vector_id was never
    #    saved (this is what prevents orphaned requirement vectors).
    #    Belt-and-suspenders: also delete any known point IDs, covering very old vectors that
    #    predate the transcript_id being stored in the payload.
    qdrant.delete_by_filter(
        collection_name=qdrant.requirements_collection,
        filters={"transcript_id": transcript_id_str}
    )
    if req_vector_ids:
        qdrant.delete_by_ids(
            collection_name=qdrant.requirements_collection,
            point_ids=req_vector_ids
        )

    # 5. Delete PostgreSQL: conversation_logs for this transcript
    conv_logs_result = await db.execute(
        select(ConversationLog).filter(ConversationLog.transcript_id == transcript_id)
    )
    conv_logs = conv_logs_result.scalars().all()
    for log in conv_logs:
        await db.delete(log)
    logger.info(f"Deleted {len(conv_logs)} conversation_logs")

    # 6. Delete PostgreSQL: requirement_versions for this transcript
    for version in versions:
        await db.delete(version)
    logger.info(f"Deleted {len(versions)} requirement_versions")

    await db.flush()  # flush so version deletions are visible for the orphan check

    # 7. Delete orphaned requirements — requirements that now have NO versions left
    for req_id in affected_req_ids:
        remaining_result = await db.execute(
            select(func.count()).select_from(RequirementVersion)
            .filter(RequirementVersion.requirement_id == req_id)
        )
        remaining_count = remaining_result.scalar() or 0
        if remaining_count == 0:
            req_result = await db.execute(
                select(Requirement).filter(Requirement.id == req_id)
            )
            orphan_req = req_result.scalars().first()
            if orphan_req:
                await db.delete(orphan_req)
                logger.info(f"Deleted orphaned requirement {req_id}")
        else:
            # HEALING: This requirement rolled back to an older version.
            # Its original vector was destroyed during overwrite, and the new vector was destroyed above.
            # We must re-embed the current surviving version and insert it back into Qdrant.
            req_result = await db.execute(select(Requirement).filter(Requirement.id == req_id))
            surviving_req = req_result.scalars().first()
            if surviving_req:
                # Re-embed
                embed_svc = EmbeddingService()
                text_to_embed = surviving_req.canonical_text or surviving_req.original_text
                if text_to_embed:
                    vector = embed_svc.generate_embedding(text_to_embed)
                    # Get the most recent version to link transcript_id if possible
                    # (Fallback to just the requirement id)
                    version_result = await db.execute(
                        select(RequirementVersion)
                        .filter(RequirementVersion.requirement_id == req_id)
                        .order_by(RequirementVersion.created_at.desc())
                    )
                    latest_version = version_result.scalars().first()
                    transcript_id_for_payload = str(latest_version.transcript_id) if latest_version else str(surviving_req.customer_id)
                    
                    qdrant.upsert_vectors(
                        collection_name=qdrant.requirements_collection,
                        points=[{
                            "id": str(surviving_req.id),
                            "vector": vector,
                            "payload": {
                                "type": "requirement",
                                "customer_id": str(surviving_req.customer_id),
                                "transcript_id": transcript_id_for_payload,
                                "requirement_text": surviving_req.original_text,
                                "canonical_text": surviving_req.canonical_text,
                                "status": surviving_req.status
                            }
                        }]
                    )
                    logger.info(f"Healed Qdrant vector for rolled-back requirement {req_id}")

    # 8. Delete the transcript record itself
    await db.delete(transcript)
    await db.commit()

    logger.info(f"Transcript {transcript_id_str} fully deleted.")
    return {
        "message": "Transcript and all associated data deleted successfully.",
        "deleted": {
            "transcript_id": transcript_id_str,
            "conversation_logs": len(conv_logs),
            "requirement_versions": len(versions),
            "qdrant_conversation_vectors": transcript.total_blocks,
            "qdrant_requirement_vectors": len(req_vector_ids),
        }
    }


# ─── Customer / Project Settings Endpoints ───────────────────────────────────

@router.get("/customers")
async def get_customers(db: AsyncSession = Depends(get_db)):
    """List all configured customers/projects"""
    result = await db.execute(select(Customer))
    customers = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "client_speaker_name": c.client_speaker_name,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in customers
    ]


@router.post("/customer")
async def create_customer(
    name: str = Form(...),
    client_speaker_name: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """Create a new customer/project with a client speaker name"""
    customer = Customer(
        id=uuid.uuid4(),
        name=name,
        client_speaker_name=client_speaker_name,
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    logger.info(f"Created customer: {customer.name} (speaker: {customer.client_speaker_name})")
    return {
        "id": str(customer.id),
        "name": customer.name,
        "client_speaker_name": customer.client_speaker_name,
    }


@router.put("/customer/{customer_id}")
async def update_customer(
    customer_id: uuid.UUID,
    name: str = Form(...),
    client_speaker_name: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """Update an existing customer/project settings"""
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalars().first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    customer.name = name
    customer.client_speaker_name = client_speaker_name
    await db.commit()
    logger.info(f"Updated customer: {customer.name} (speaker: {customer.client_speaker_name})")
    return {
        "id": str(customer.id),
        "name": customer.name,
        "client_speaker_name": customer.client_speaker_name,
    }


@router.delete("/customer/{customer_id}")
async def delete_customer(
    customer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """Delete a customer/project — blocked if any data exists"""
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalars().first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Count linked transcripts
    transcript_count_result = await db.execute(
        select(func.count()).select_from(Transcript).filter(Transcript.customer_id == customer_id)
    )
    transcript_count = transcript_count_result.scalar() or 0

    # Count linked requirements
    requirement_count_result = await db.execute(
        select(func.count()).select_from(Requirement).filter(Requirement.customer_id == customer_id)
    )
    requirement_count = requirement_count_result.scalar() or 0

    if transcript_count > 0 or requirement_count > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot delete project '{customer.name}'. "
                f"It has {transcript_count} transcript(s) and {requirement_count} requirement(s) linked to it. "
                f"Please remove all associated data before deleting the project."
            )
        )

    await db.delete(customer)
    await db.commit()
    logger.info(f"Deleted customer: {customer_id}")
    return {"message": "Customer deleted successfully"}
