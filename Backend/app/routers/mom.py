"""Minutes of Meeting endpoints.

Entirely additive — this router touches only the `meeting_minutes` table plus read-only lookups,
and nothing in the requirement pipeline calls it.

Permissions are SPLIT rather than admin-only:

    read  (list, view, download)   any logged-in user
    write (generate, send)         admin only

Reading is open because the chatbot is available to every logged-in user, so a normal user asking
"show me last call's minutes" must be able to receive them. Writing stays locked because
generating costs money and sending reaches a client-facing mailbox.

Generation is automatic (via the Celery task fired after a transcript is processed), but SENDING
is deliberately manual: a wrong MOM costs more trust than one that arrives an hour later. Flip to
auto-send once the output is proven.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.models.database import Customer, MeetingMinutes, Transcript
from app.models.schemas import MOMListItem, MOMResponse, MOMSendResult, MOMUpdate
from app.services.document_render import render_markdown_to_docx, safe_filename
from app.services.mom_generation import generate_and_store_mom
from app.services.notification_service import NotificationService

# Word's MIME type. Getting this wrong makes browsers save a file Word refuses to open.
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/mom",
    tags=["Minutes of Meeting"],
    # Every endpoint needs a login. The two that WRITE add Depends(require_admin) individually.
    dependencies=[Depends(get_current_user)],
)

notification_service = NotificationService()


@router.get("/", response_model=List[MOMListItem])
async def list_minutes(
    customer_id: Optional[uuid.UUID] = None,
    all_versions: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    List minutes, newest call first.

    By default returns only the LATEST version per transcript, which is what the dashboard wants.
    Pass all_versions=true to see the full regeneration history.
    """
    query = select(MeetingMinutes)
    if customer_id:
        query = query.filter(MeetingMinutes.customer_id == customer_id)

    rows = (await db.execute(
        query.order_by(MeetingMinutes.call_date.desc(), MeetingMinutes.version.desc())
    )).scalars().all()

    if all_versions:
        return rows

    # Rows are already ordered version-desc, so the first one seen per transcript is the latest.
    latest, seen = [], set()
    for row in rows:
        if row.transcript_id not in seen:
            seen.add(row.transcript_id)
            latest.append(row)
    return latest


@router.get("/transcript/{transcript_id}", response_model=List[MOMResponse])
async def minutes_for_transcript(transcript_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Every generated version for one call, newest first."""
    rows = (await db.execute(
        select(MeetingMinutes)
        .filter(MeetingMinutes.transcript_id == transcript_id)
        .order_by(MeetingMinutes.version.desc())
    )).scalars().all()
    return rows


@router.get("/{mom_id}", response_model=MOMResponse)
async def get_minutes(mom_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """One MOM including the full markdown body."""
    mom = (await db.execute(
        select(MeetingMinutes).filter(MeetingMinutes.id == mom_id)
    )).scalars().first()
    if not mom:
        raise HTTPException(status_code=404, detail="Minutes not found")
    return mom


@router.patch(
    "/{mom_id}",
    response_model=MOMResponse,
    dependencies=[Depends(require_admin)],   # same gate as send: this is what recipients get
)
async def update_minutes(
    mom_id: uuid.UUID,
    payload: MOMUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Correct a draft before it is sent — typically a word the transcription got wrong.

    Overwrites `content_markdown` IN PLACE rather than creating a version: an edit is a
    correction, not a regeneration, and `version` already means "nth time the model produced
    this document". The first edit copies the model's output to `original_markdown` so it is
    never lost, which is what keeps MOM quality measurable once people start fixing wording
    by hand.

    Editing an already-sent MOM is allowed and does NOT change what recipients received; the
    UI warns about that. Status is left alone for the same reason — a sent document stays sent.
    """
    mom = (await db.execute(
        select(MeetingMinutes).filter(MeetingMinutes.id == mom_id)
    )).scalars().first()
    if not mom:
        raise HTTPException(status_code=404, detail="Minutes not found")

    if not mom.content_markdown:
        raise HTTPException(
            status_code=400,
            detail="These minutes have no content to edit. Generate them first.",
        )

    # First edit only — afterwards this holds the model's output permanently.
    if mom.original_markdown is None:
        mom.original_markdown = mom.content_markdown

    mom.content_markdown = payload.content_markdown
    mom.edited_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(mom)

    logger.info(f"Minutes {mom_id} edited ({len(payload.content_markdown)} chars).")
    return mom


@router.get("/{mom_id}/download")
async def download_minutes(mom_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Download these minutes as a Word document.

    Rendered on the fly from the stored markdown rather than saved at generation time, so
    improving the document styling never requires regenerating content.
    """
    mom = (await db.execute(
        select(MeetingMinutes).filter(MeetingMinutes.id == mom_id)
    )).scalars().first()
    if not mom:
        raise HTTPException(status_code=404, detail="Minutes not found")

    if not mom.content_markdown:
        raise HTTPException(
            status_code=400,
            detail=f"These minutes have no content to download. {mom.generation_error or ''}".strip(),
        )

    customer = (await db.execute(
        select(Customer).filter(Customer.id == mom.customer_id)
    )).scalars().first()
    customer_name = customer.name if customer else ""

    date_str = mom.call_date.strftime("%d %B %Y") if mom.call_date else ""
    subtitle = "  ·  ".join(p for p in (customer_name, mom.session_name, date_str) if p)

    try:
        content = render_markdown_to_docx(
            markdown=mom.content_markdown,
            title="Minutes of Meeting",
            subtitle=subtitle,
            footer="Generated automatically from the call transcript.",
            # Never let an incomplete document look complete to whoever opens it.
            warning=(
                "Note: these minutes reached the generation length limit and may be "
                "incomplete toward the end."
                if mom.truncated else ""
            ),
        )
    except Exception as exc:
        logger.error(f"Failed to render minutes {mom_id} to docx: {exc}")
        raise HTTPException(status_code=500, detail="Could not build the Word document.")

    filename = safe_filename("MOM", mom.session_name or str(mom_id))
    return Response(
        content=content,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/generate/{transcript_id}",
    response_model=MOMResponse,
    dependencies=[Depends(require_admin)],   # writing costs money — admin only
)
async def generate_minutes(transcript_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Generate minutes for a transcript, or regenerate them as a new version.

    Runs inline (not via Celery) so the caller gets the document back directly — this is the
    on-demand path for existing transcripts and for retrying after a prompt change. It can take
    a minute or two on a long call.
    """
    transcript = (await db.execute(
        select(Transcript).filter(Transcript.id == transcript_id)
    )).scalars().first()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    mom = await generate_and_store_mom(db=db, transcript_id=transcript_id)
    if not mom:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot generate minutes for '{transcript.session_name}': it has no stored "
                f"conversation (status={transcript.status})."
            ),
        )
    return mom


@router.post(
    "/{mom_id}/send",
    response_model=MOMSendResult,
    dependencies=[Depends(require_admin)],   # reaches a client mailbox — admin only
)
async def send_minutes(mom_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Email these minutes to the project's active subscribers.

    Recipients come from `team_subscriptions` — manage them via /api/subscriptions/.
    The send outcome is recorded on the row either way, so a failure is visible rather than lost.
    """
    mom = (await db.execute(
        select(MeetingMinutes).filter(MeetingMinutes.id == mom_id)
    )).scalars().first()
    if not mom:
        raise HTTPException(status_code=404, detail="Minutes not found")

    if not mom.content_markdown:
        raise HTTPException(
            status_code=400,
            detail=f"These minutes have no content and cannot be sent. {mom.generation_error or ''}".strip(),
        )

    result = await notification_service.send_mom_email(
        db=db,
        customer_id=mom.customer_id,
        session_name=mom.session_name,
        call_date=mom.call_date,
        content_markdown=mom.content_markdown,
        truncated=bool(mom.truncated),
    )

    mom.email_recipients = result["recipients"]
    if result["success"]:
        mom.status = "sent"
        mom.email_sent_at = datetime.now(timezone.utc)
        mom.email_error = None
    else:
        mom.status = "send_failed"
        mom.email_error = result["error"]
    await db.commit()

    return MOMSendResult(
        id=mom.id,
        status=mom.status,
        recipients=result["recipients"],
        error=result["error"] or None,
    )
