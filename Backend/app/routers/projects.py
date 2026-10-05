"""Project list for the nav-bar project selector.

Separate from /requirements/customers (admin-only, includes tracker settings) because EVERY
logged-in user needs to pick a project to scope the Minutes page. Exposes id + name only.
"""
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from uuid import UUID

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.database import Customer

router = APIRouter(
    prefix="/api/projects",
    tags=["Projects"],
    dependencies=[Depends(get_current_user)],
)


class ProjectOption(BaseModel):
    id: UUID
    name: str

    class Config:
        from_attributes = True


@router.get("/", response_model=List[ProjectOption])
async def list_projects(db: AsyncSession = Depends(get_db)):
    """All projects, by name."""
    return (await db.execute(select(Customer).order_by(Customer.name))).scalars().all()
