import asyncio
import logging
import uuid
import os
from datetime import datetime
from sqlalchemy.future import select
from app.core.celery_app import celery_app
from app.core.database import AsyncSessionLocal
from app.models.database import Transcript, Customer, Requirement
from app.services.transcript_parser import TranscriptParserService
from app.services.requirement_extraction import RequirementExtractionService
from app.services.requirement_comparison import RequirementComparisonService
from app.services.notification_service import NotificationService
from app.utils.dates import session_to_datetime

logger = logging.getLogger(__name__)

# Initialize services once per worker process
parser_service = TranscriptParserService()
extraction_service = RequirementExtractionService()
comparison_service = RequirementComparisonService()
notification_service = NotificationService()

async def _process_transcript_async(
    transcript_id_str: str,
    file_path: str,
    customer_id_str: str,
    session_name: str,
    call_date_str: str,
    send_email: bool = True,
):
    """
    The actual asynchronous pipeline that processes the transcript.

    send_email: when False (used by the bulk backfill), the full pipeline still runs
    (parse -> extract -> compare) but the change-notification email is skipped.
    """
    transcript_id = uuid.UUID(transcript_id_str)
    customer_id = uuid.UUID(customer_id_str)
    # Derive the date from the SESSION NAME (noon-UTC, timezone-stable) so requirement dates
    # match the transcript record and never slip a day in IST. Falls back to the passed value.
    call_date = session_to_datetime(session_name, datetime.fromisoformat(call_date_str))
    
    async with AsyncSessionLocal() as db:
        try:
            # 1. Fetch Customer
            result = await db.execute(select(Customer).filter(Customer.id == customer_id))
            customer = result.scalars().first()
            if not customer:
                raise ValueError(f"Customer {customer_id} not found")

            # 2. Parse & Store 1
            blocks = await parser_service.parse_and_store_transcript(
                file_path=file_path,
                db=db,
                customer_id=customer_id,
                transcript_id=transcript_id,
                session_name=session_name,
                call_date=call_date,
                client_speaker_name=customer.client_speaker_name
            )
            
            # Update total blocks
            result = await db.execute(select(Transcript).filter(Transcript.id == transcript_id))
            db_transcript = result.scalars().first()
            if db_transcript:
                db_transcript.total_blocks = len(blocks)
                await db.commit()
            
            # 3a. Fetch category names already used for this project, so extraction can
            # reuse them and keep categories consistent across uploads (Step 2).
            cat_result = await db.execute(
                select(Requirement.category)
                .filter(Requirement.customer_id == customer_id)
                .distinct()
            )
            existing_categories = [c for c in cat_result.scalars().all() if c]

            # 3b. LLM Extraction (No DB interaction, keeping sync)
            # Notes-based extraction (v4): reason into structured notes over the whole
            # transcript, then format to JSON. Two calls, intent-based dedup by the model.
            extracted_reqs = extraction_service.extract_requirements_notes_based(
                blocks=blocks,
                client_speaker_name=customer.client_speaker_name,
                existing_categories=existing_categories,
                run_review=False,  # 1 pass (notes only) — ~45% cheaper; gpt-5.5 + max_tokens make the single pass complete enough
            )
            
            processed_reqs = []
            if extracted_reqs:
                # 4. Compare & Store 2
                processed_reqs = await comparison_service.process_and_compare(
                    extracted_reqs=extracted_reqs,
                    db=db,
                    customer_id=customer_id,
                    session_name=session_name,
                    call_date=call_date,
                    transcript_id=transcript_id
                )
                
                # 5. Email Notifications — DISABLED (replaced by the chat "what changed" feature).
                #    Commented out (NOT removed) so it's fully reversible: uncomment the block below
                #    to restore emails. All email code stays intact in notification_service.py.
                # if send_email:
                #     await notification_service.send_change_notification(
                #         db=db,
                #         customer_id=customer_id,
                #         session_name=session_name,
                #         call_date=call_date,
                #         processed_reqs=processed_reqs
                #     )
                # else:
                #     logger.info("Backfill mode: skipping change-notification email")
            
            # 6. Update Transcript Status as Completed
            if db_transcript:
                added_count = len([r for r in processed_reqs if r['change_type'] == 'added'])
                modified_count = len([r for r in processed_reqs if r['change_type'] == 'modified'])
                
                db_transcript.status = "processed"
                db_transcript.processing_summary = {
                    "total_extracted": len(extracted_reqs),
                    "added": added_count,
                    "modified": modified_count,
                }
                await db.commit()
                
            logger.info(f"Successfully processed transcript {transcript_id}")
            
        except Exception as e:
            logger.error(f"Error processing transcript {transcript_id}: {e}")
            # Mark as failed
            result = await db.execute(select(Transcript).filter(Transcript.id == transcript_id))
            db_transcript = result.scalars().first()
            if db_transcript:
                db_transcript.status = "failed"
                await db.commit()
            raise e
            
        finally:
            # Clean up the temporary file uploaded by the API
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Failed to delete temporary file {file_path}: {e}")
            
            # CRITICAL WINDOWS FIX: 
            # Because each Celery task uses a fresh asyncio.run() loop, we MUST 
            # dispose of the database connection pool at the end of the task. 
            # Otherwise, the next task tries to reuse old connections on a dead event loop,
            # causing "AttributeError: 'NoneType' object has no attribute 'send'".
            from app.core.database import engine
            await engine.dispose()

@celery_app.task(name="process_transcript_task")
def process_transcript_task(
    transcript_id_str: str, 
    file_path: str, 
    customer_id_str: str, 
    session_name: str, 
    call_date_str: str
):
    """
    Celery task wrapper. Celery is synchronous, so we use asyncio.run 
    to trigger our async pipeline in the background.
    """
    logger.info(f"Starting Celery task for transcript: {transcript_id_str}")
    asyncio.run(_process_transcript_async(
        transcript_id_str, 
        file_path, 
        customer_id_str, 
        session_name, 
        call_date_str
    ))
    return f"Processed {transcript_id_str}"
