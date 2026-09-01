from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
import uuid
from pydantic import BaseModel

from app.core.database import get_db
from app.models.database import TeamSubscription, Customer
from app.core.security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])   # entire router is admin-only

class SubscriptionCreate(BaseModel):
    customer_id: uuid.UUID
    member_name: str
    email_address: str
    is_active: bool = True

class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    member_name: str
    email_address: str
    is_active: bool

    class Config:
        from_attributes = True

@router.post("/", response_model=SubscriptionResponse)
async def create_subscription(sub: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    """Add a new email address to receive requirement change notifications."""
    # Verify customer exists
    cust_result = await db.execute(select(Customer).filter(Customer.id == sub.customer_id))
    customer = cust_result.scalars().first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
        
    # Check if email already exists for this customer
    existing_result = await db.execute(
        select(TeamSubscription).filter(
            TeamSubscription.customer_id == sub.customer_id,
            TeamSubscription.email_address == sub.email_address
        )
    )
    if existing_result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already subscribed for this customer")

    db_sub = TeamSubscription(
        id=uuid.uuid4(),
        customer_id=sub.customer_id,
        member_name=sub.member_name,
        email_address=sub.email_address,
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
        
    result = await db.execute(query)
    return result.scalars().all()

@router.delete("/{subscription_id}")
async def delete_subscription(subscription_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete an email subscription."""
    result = await db.execute(select(TeamSubscription).filter(TeamSubscription.id == subscription_id))
    sub = result.scalars().first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
        
    await db.delete(sub)
    await db.commit()
    return {"message": "Subscription deleted successfully"}
