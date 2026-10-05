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
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.models.database import Customer, MeetingMinutes, MOMActionItem, TeamSubscription, Transcript, User
from app.models.schemas import (
    ActionItemOut, ActionItemsBulkDelete, ActionItemsBulkDeleteResult, ActionItemsReview,
    ActionItemUpdate, ApproveResult, AssigneeOption,
    MOMListItem, MOMResponse, MOMSendResult,
)
from app.services import trackers
from app.services.document_render import render_markdown_to_docx, safe_filename
from app.services.mom_action_items import TITLE_MAX, match_assignee, parse_action_items
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


# ─── Action items → tracker (Azure Boards | Jira) ────────────────────────────
#
# Flow: GET action-items (first call extracts drafts) → PATCH / DELETE drafts → POST approve
# (creates one Task per remaining item in the customer's tracker) → POST action-items/push to
# retry failures. All admin-only. Items are editable while draft or failed, locked once
# pushing/created. Tracker specifics live in services/trackers.py.

_EDITABLE = ("draft", "failed")


async def _get_mom(db: AsyncSession, mom_id: uuid.UUID, lock: bool = False) -> MeetingMinutes:
    query = select(MeetingMinutes).filter(MeetingMinutes.id == mom_id)
    if lock:
        query = query.with_for_update()   # serialises concurrent extract / approve on one MOM
    mom = (await db.execute(query)).scalars().first()
    if not mom:
        raise HTTPException(status_code=404, detail="Minutes not found")
    return mom


async def _assignees(db: AsyncSession, customer_id) -> List[AssigneeOption]:
    """Active subscribers for the customer, de-duplicated by email (case-insensitive)."""
    rows = (await db.execute(
        select(TeamSubscription)
        .filter(TeamSubscription.customer_id == customer_id, TeamSubscription.is_active.is_(True))
        .order_by(TeamSubscription.member_name)
    )).scalars().all()
    seen, out = set(), []
    for r in rows:
        key = (r.email_address or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(AssigneeOption(name=r.member_name, email=r.email_address.strip()))
    return out


async def _items(db: AsyncSession, mom_id: uuid.UUID) -> List[MOMActionItem]:
    return (await db.execute(
        select(MOMActionItem).filter(MOMActionItem.mom_id == mom_id).order_by(MOMActionItem.created_at, MOMActionItem.id)
    )).scalars().all()


async def _customer(db: AsyncSession, customer_id) -> Optional[Customer]:
    return (await db.execute(select(Customer).filter(Customer.id == customer_id))).scalars().first()


async def _configured_customer(db: AsyncSession, customer_id) -> Customer:
    customer = await _customer(db, customer_id)
    if not trackers.is_configured(customer):
        raise HTTPException(status_code=400, detail=trackers.not_configured_reason(customer))
    return customer


async def _prepare(customer: Customer):
    """Tracker preflight, run BEFORE approving/claiming anything so an unreachable tracker fails cleanly."""
    try:
        return await trackers.prepare(customer)
    except trackers.TrackerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


async def _push_items(db: AsyncSession, mom: MeetingMinutes, customer: Customer, ctx, from_status: str) -> None:
    """Create Tasks in the customer's tracker for this MOM's items currently in `from_status`.

    `ctx` is the preflight result from trackers.prepare (which optional fields the project accepts).

    Items are claimed with an atomic UPDATE … RETURNING (→ 'pushing') before any tracker call, so a
    second concurrent request finds nothing to claim. Each result is committed as it lands, so a
    crash part-way never forgets a Task that was already created.
    """
    claimed = (await db.execute(
        update(MOMActionItem)
        .where(MOMActionItem.mom_id == mom.id, MOMActionItem.push_status == from_status)
        .values(push_status="pushing", push_error=None)
        .returning(MOMActionItem.id)
    )).scalars().all()
    await db.commit()

    mom_url = f"{settings.APP_PUBLIC_URL.rstrip('/')}/minutes/{mom.id}"
    call_date = mom.call_date.date() if mom.call_date else None

    for item_id in claimed:
        item = (await db.execute(select(MOMActionItem).filter(MOMActionItem.id == item_id))).scalars().first()
        try:
            key, url, warning = await trackers.create(customer, ctx, item, mom.session_name, call_date, mom_url)
            item.push_status, item.tracker = "created", customer.tracker
            item.external_key, item.external_url = key, url
            item.push_error = warning   # created, but e.g. the follow-up assign failed (Jira)
        except trackers.TrackerError as exc:
            item.push_status, item.push_error = "failed", str(exc)
            logger.warning(f"{customer.tracker} Task creation failed for action item {item.id}: {exc}")
        except Exception as exc:   # never leave an item stuck in 'pushing'
            item.push_status, item.push_error = "failed", f"Unexpected error: {exc.__class__.__name__}"
            logger.exception(f"Unexpected error creating {customer.tracker} Task for action item {item.id}")
        await db.commit()


async def _approve_result(db: AsyncSession, mom: MeetingMinutes) -> ApproveResult:
    items = await _items(db, mom.id)
    return ApproveResult(
        mom_id=mom.id,
        approved_at=mom.approved_at,
        approved_by=mom.approved_by,
        created=sum(1 for i in items if i.push_status == "created"),
        failed=sum(1 for i in items if i.push_status == "failed"),
        items=items,
    )


@router.get(
    "/{mom_id}/action-items",
    response_model=ActionItemsReview,
    dependencies=[Depends(require_admin)],
)
async def review_action_items(mom_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    The review list. The FIRST call parses the MOM's Consolidated Action Items into draft rows,
    with assignees pre-matched from team_subscriptions. Later calls return the saved rows as edited.
    """
    mom = await _get_mom(db, mom_id, lock=True)
    assignees = await _assignees(db, mom.customer_id)

    if mom.items_extracted_at is None:
        subscribers = [{"name": a.name, "email": a.email} for a in assignees]
        call_date = mom.call_date.date() if mom.call_date else None
        for parsed in parse_action_items(mom.content_markdown, call_date):
            db.add(MOMActionItem(
                id=uuid.uuid4(),
                mom_id=mom.id,
                assignee_email=match_assignee(parsed["owner_name"], subscribers),
                push_status="draft",
                **parsed,
            ))
        mom.items_extracted_at = datetime.now(timezone.utc)
    await db.commit()   # also releases the row lock

    customer = await _customer(db, mom.customer_id)
    return ActionItemsReview(
        mom_id=mom.id,
        approved_at=mom.approved_at,
        approved_by=mom.approved_by,
        tracker=customer.tracker if customer else None,
        tracker_configured=trackers.is_configured(customer),
        items=await _items(db, mom.id),
        assignees=assignees,
    )


async def _editable_item(db: AsyncSession, mom_id: uuid.UUID, item_id: uuid.UUID) -> MOMActionItem:
    item = (await db.execute(
        select(MOMActionItem).filter(MOMActionItem.id == item_id, MOMActionItem.mom_id == mom_id)
    )).scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")
    if item.push_status not in _EDITABLE:
        raise HTTPException(
            status_code=409,
            detail="This task has already been created in the tracker — edit it there instead.",
        )
    return item


@router.patch(
    "/{mom_id}/action-items/{item_id}",
    response_model=ActionItemOut,
    dependencies=[Depends(require_admin)],
)
async def update_action_item(
    mom_id: uuid.UUID, item_id: uuid.UUID, body: ActionItemUpdate, db: AsyncSession = Depends(get_db)
):
    """Edit a draft (or failed) item. Only the fields sent change; send null to clear assignee/due date."""
    item = await _editable_item(db, mom_id, item_id)
    changes = body.model_dump(exclude_unset=True)

    if "title" in changes:
        title = (changes["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title cannot be empty.")
        if len(title) > TITLE_MAX:
            raise HTTPException(status_code=400, detail=f"Title must be {TITLE_MAX} characters or fewer.")
        changes["title"] = title
    if "assignee_email" in changes:
        email = (changes["assignee_email"] or "").strip()
        if email and "@" not in email:
            raise HTTPException(status_code=400, detail="Assignee must be an email address.")
        changes["assignee_email"] = email or None

    for field, value in changes.items():
        setattr(item, field, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete(
    "/{mom_id}/action-items/{item_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_action_item(mom_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Remove a draft (or failed) item so it is never sent to the tracker."""
    item = await _editable_item(db, mom_id, item_id)
    await db.delete(item)
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/{mom_id}/action-items/bulk-delete",
    response_model=ActionItemsBulkDeleteResult,
    dependencies=[Depends(require_admin)],
)
async def bulk_delete_action_items(
    mom_id: uuid.UUID, body: ActionItemsBulkDelete, db: AsyncSession = Depends(get_db)
):
    """
    Remove several draft (or failed) items in one transaction.

    POST rather than DELETE-with-body, which some proxies and clients drop. Only items of THIS MOM
    that are still editable are removed; anything else (unknown id, or created in the tracker since
    the screen loaded) is reported in `skipped` rather than failing the whole request.
    """
    wanted = set(body.ids)
    rows = (await db.execute(
        select(MOMActionItem)
        .filter(MOMActionItem.mom_id == mom_id, MOMActionItem.id.in_(wanted))
        .with_for_update()   # an Approve claiming these rows waits, and vice versa
    )).scalars().all()

    deleted = [r.id for r in rows if r.push_status in _EDITABLE]
    for r in rows:
        if r.push_status in _EDITABLE:
            await db.delete(r)
    await db.commit()
    return ActionItemsBulkDeleteResult(deleted=deleted, skipped=sorted(wanted - set(deleted), key=str))


@router.post("/{mom_id}/approve", response_model=ApproveResult)
async def approve_minutes(
    mom_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Approve these minutes and create one Task per remaining draft item in the customer's tracker.

    Independent of Send: approving does not email anyone, and sending does not approve.
    """
    mom = await _get_mom(db, mom_id, lock=True)
    if mom.approved_at:
        raise HTTPException(
            status_code=409,
            detail=f"Already approved by {mom.approved_by} — use Retry to resend failed tasks.",
        )
    if mom.items_extracted_at is None:
        raise HTTPException(status_code=400, detail="Review the tasks before approving.")

    customer = await _configured_customer(db, mom.customer_id)

    other = (await db.execute(
        select(MeetingMinutes).filter(
            MeetingMinutes.transcript_id == mom.transcript_id,
            MeetingMinutes.id != mom.id,
            MeetingMinutes.approved_at.isnot(None),
        )
    )).scalars().first()
    if other:
        raise HTTPException(
            status_code=409,
            detail=f"Version {other.version} of this call is already approved; approving another would duplicate its tasks.",
        )

    ctx = await _prepare(customer)   # before approving — fails cleanly

    mom.approved_at = datetime.now(timezone.utc)
    mom.approved_by = admin.username
    await db.commit()
    logger.info(f"MOM {mom.id} approved by {admin.username} ({customer.tracker})")

    await _push_items(db, mom, customer, ctx, from_status="draft")
    return await _approve_result(db, mom)


@router.post(
    "/{mom_id}/action-items/push",
    response_model=ApproveResult,
    dependencies=[Depends(require_admin)],
)
async def retry_action_items(mom_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Retry failed items against the customer's CURRENT tracker. Created items are never re-sent."""
    mom = await _get_mom(db, mom_id)
    if not mom.approved_at:
        raise HTTPException(status_code=400, detail="Approve these minutes first.")
    customer = await _configured_customer(db, mom.customer_id)

    await _push_items(db, mom, customer, await _prepare(customer), from_status="failed")
    return await _approve_result(db, mom)
