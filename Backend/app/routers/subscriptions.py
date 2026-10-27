from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional
import re
import uuid
from pydantic import BaseModel

from app.core.database import get_db
from app.models.database import TeamSubscription, Customer
from app.core.security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])   # entire router is admin-only

# Deliberately loose: catches typos (missing @, spaces, no domain dot) without pulling in the
# email-validator package. Deliverability is the mail server's problem, not ours.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_email(addr: str) -> str:
    addr = (addr or "").strip()
    if not _EMAIL_RE.match(addr):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    return addr


async def _email_taken(db: AsyncSession, customer_id: uuid.UUID, addr: str,
                       exclude_id: Optional[uuid.UUID] = None) -> bool:
    """Case-insensitive: "Name@x.com" and "name@x.com" are the same mailbox. (A case-sensitive
    check here is what let duplicates in before — see notification_service's de-dup comment.)"""
    query = select(TeamSubscription).filter(
        TeamSubscription.customer_id == customer_id,
        func.lower(TeamSubscription.email_address) == addr.lower(),
    )
    if exclude_id:
        query = query.filter(TeamSubscription.id != exclude_id)
    return (await db.execute(query)).scalars().first() is not None


class SubscriptionCreate(BaseModel):
    customer_id: uuid.UUID
    member_name: str
    email_address: str
    is_active: bool = True

class SubscriptionBulkCreate(BaseModel):
    """One person added to several projects at once."""
    customer_ids: List[uuid.UUID]
    member_name: str = ""
    email_address: str
    is_active: bool = True

class SubscriptionUpdate(BaseModel):
    """Only the fields sent change."""
    member_name: Optional[str] = None
    email_address: Optional[str] = None
    is_active: Optional[bool] = None
    # A person is identified by email across projects (one row per project). When true, a name or
    # email change is copied to their rows in every other project. is_active always stays per project.
    apply_to_all_projects: bool = False

class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    member_name: Optional[str]
    email_address: str
    is_active: bool

    class Config:
        from_attributes = True

class SubscriptionBulkResponse(BaseModel):
    created: List[SubscriptionResponse]
    skipped: List[uuid.UUID]   # projects where this email was already a member


async def _same_person(db: AsyncSession, addr: str) -> list:
    """Every project row for this email (case-insensitive) — i.e. the same person."""
    return (await db.execute(
        select(TeamSubscription).filter(func.lower(TeamSubscription.email_address) == addr.lower())
    )).scalars().all()

@router.post("/bulk", response_model=SubscriptionBulkResponse)
async def create_subscriptions_bulk(sub: SubscriptionBulkCreate, db: AsyncSession = Depends(get_db)):
    """Add one person to several projects in a single transaction. Projects where the email is
    already a member are skipped (and reported), not treated as errors."""
    ids = list(dict.fromkeys(sub.customer_ids))   # de-dup, keep order
    if not ids:
        raise HTTPException(status_code=422, detail="Pick at least one project")
    found = set((await db.execute(select(Customer.id).filter(Customer.id.in_(ids)))).scalars().all())
    if len(found) != len(ids):
        raise HTTPException(status_code=404, detail="Project not found")

    addr = _clean_email(sub.email_address)
    created, skipped = [], []
    for cid in ids:
        if await _email_taken(db, cid, addr):
            skipped.append(cid)
            continue
        row = TeamSubscription(
            id=uuid.uuid4(),
            customer_id=cid,
            member_name=(sub.member_name or "").strip(),
            email_address=addr,
            is_active=sub.is_active,
        )
        db.add(row)
        created.append(row)
    await db.commit()
    for row in created:
        await db.refresh(row)
    return {"created": created, "skipped": skipped}

@router.post("/", response_model=SubscriptionResponse)
async def create_subscription(sub: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    """Add a new email address to receive requirement change notifications."""
    # Verify customer exists
    cust_result = await db.execute(select(Customer).filter(Customer.id == sub.customer_id))
    customer = cust_result.scalars().first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    addr = _clean_email(sub.email_address)
    if await _email_taken(db, sub.customer_id, addr):
        raise HTTPException(status_code=400, detail="Email already subscribed for this project")

    db_sub = TeamSubscription(
        id=uuid.uuid4(),
        customer_id=sub.customer_id,
        member_name=(sub.member_name or "").strip(),
        email_address=addr,
        is_active=sub.is_active
    )
    db.add(db_sub)
    await db.commit()
    await db.refresh(db_sub)

    return db_sub

@router.get("/", response_model=List[SubscriptionResponse])
async def list_subscriptions(customer_id: uuid.UUID = None, db: AsyncSession = Depends(get_db)):
    """List all email subscriptions. Optionally filter by customer_id."""
    query = select(TeamSubscription)
    if customer_id:
        query = query.filter(TeamSubscription.customer_id == customer_id)
    query = query.order_by(TeamSubscription.member_name, TeamSubscription.email_address)

    result = await db.execute(query)
    return result.scalars().all()

@router.patch("/{subscription_id}", response_model=SubscriptionResponse)
async def update_subscription(subscription_id: uuid.UUID, changes: SubscriptionUpdate,
                              db: AsyncSession = Depends(get_db)):
    """Edit a member's name/email, or pause/resume them (is_active). Paused members keep their
    details but are skipped by every sender and by the task-assignee list."""
    sub = (await db.execute(
        select(TeamSubscription).filter(TeamSubscription.id == subscription_id)
    )).scalars().first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    # The rows to rename: this one, plus the same person's rows in other projects if asked.
    rows = [sub]
    if changes.apply_to_all_projects:
        rows = [r for r in await _same_person(db, sub.email_address) if r.id != sub.id] + [sub]

    if changes.email_address is not None:
        addr = _clean_email(changes.email_address)
        for r in rows:
            if await _email_taken(db, r.customer_id, addr, exclude_id=r.id):
                if r.id == sub.id:
                    raise HTTPException(status_code=400, detail="Email already subscribed for this project")
                name = (await db.execute(select(Customer.name).filter(Customer.id == r.customer_id))).scalar()
                raise HTTPException(status_code=400,
                                    detail=f"That email is already a different member of {name or 'another project'}")
        for r in rows:
            r.email_address = addr
    if changes.member_name is not None:
        for r in rows:
            r.member_name = changes.member_name.strip()
    if changes.is_active is not None:
        sub.is_active = changes.is_active

    await db.commit()
    await db.refresh(sub)
    return sub

@router.delete("/{subscription_id}")
async def delete_subscription(subscription_id: uuid.UUID, all_projects: bool = False,
                              db: AsyncSession = Depends(get_db)):
    """Delete an email subscription. With all_projects=true, removes the same email from every project."""
    result = await db.execute(select(TeamSubscription).filter(TeamSubscription.id == subscription_id))
    sub = result.scalars().first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    rows = await _same_person(db, sub.email_address) if all_projects else [sub]
    for r in rows:
        await db.delete(r)
    await db.commit()
    return {"message": "Subscription deleted successfully", "deleted": len(rows)}
