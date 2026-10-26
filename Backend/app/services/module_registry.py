"""The per-customer module registry.

Requirements are filed into a FIXED set of product areas rather than headings invented per
meeting. The reason is measured, not theoretical: parsing four real MOMs produced 117 section
headings, every one unique — "Mobile App" under four different names, Chat/AI under three. Filed
as written, that is 117 documents describing roughly 14 product areas.

So this module owns the list, and everything downstream reads from it:

    extraction   asks which of these modules a section belongs to
    Google Docs  keeps one document per row
    the UI       lists the documents

A genuinely new product area is allowed — proposed with status='proposed' and only usable once
an admin accepts it. Allowing the case without letting it happen silently is what keeps the list
from drifting back toward 117.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ModuleDocument

logger = logging.getLogger(__name__)


async def list_modules(
    db: AsyncSession,
    customer_id: uuid.UUID,
    include_proposed: bool = False,
) -> list[ModuleDocument]:
    """
    Every module for one customer, alphabetical.

    Proposed modules are excluded by default: a classifier shown an unapproved module would file
    requirements into it, which is the approval step defeating itself.
    """
    statuses = ["active", "proposed"] if include_proposed else ["active"]
    rows = (await db.execute(
        select(ModuleDocument)
        .filter(ModuleDocument.customer_id == customer_id,
                ModuleDocument.status.in_(statuses))
        .order_by(ModuleDocument.module_name)
    )).scalars().all()
    return list(rows)


async def get_module(
    db: AsyncSession,
    customer_id: uuid.UUID,
    module_name: str,
) -> Optional[ModuleDocument]:
    """One module by exact name. Case-insensitive: the classifier echoes names back and its
    capitalisation is not reliable enough to key on."""
    return (await db.execute(
        select(ModuleDocument).filter(
            ModuleDocument.customer_id == customer_id,
            ModuleDocument.module_name.ilike(module_name.strip()),
        )
    )).scalars().first()


async def propose_module(
    db: AsyncSession,
    customer_id: uuid.UUID,
    module_name: str,
    description: str = "",
) -> ModuleDocument:
    """
    Record a module the classifier asked for that does not exist yet.

    Created as 'proposed', never 'active' — it does not take part in filing until an admin
    approves it. Returns the existing row if the name is already known in any state, so a
    repeated proposal across several transcripts does not pile up duplicates.
    """
    existing = await get_module(db, customer_id, module_name)
    if existing:
        return existing

    module = ModuleDocument(
        customer_id=customer_id,
        module_name=module_name.strip(),
        description=(description or "").strip(),
        status="proposed",
    )
    db.add(module)
    await db.commit()
    await db.refresh(module)
    logger.info(f"Proposed new module '{module.module_name}' for customer {customer_id}.")
    return module


async def approve_module(db: AsyncSession, module_id: uuid.UUID) -> Optional[ModuleDocument]:
    """Promote a proposed module to active so requirements can be filed into it."""
    module = (await db.execute(
        select(ModuleDocument).filter(ModuleDocument.id == module_id)
    )).scalars().first()
    if not module:
        return None
    module.status = "active"
    await db.commit()
    await db.refresh(module)
    logger.info(f"Approved module '{module.module_name}'.")
    return module


def as_classifier_block(modules: list[ModuleDocument]) -> str:
    """
    The module list as it is shown to the classifier.

    Descriptions are included rather than names alone: adjacent modules ("Scheduling &
    Availability" vs "Job Offers & Assignments") are hard to separate from a bare label, and the
    one-line boundary is what makes the choice decidable.
    """
    if not modules:
        return "(no modules defined for this project yet)"
    return "\n".join(
        f"- {m.module_name}: {m.description or '(no description)'}" for m in modules
    )


async def seed_modules(
    db: AsyncSession,
    customer_id: uuid.UUID,
    modules: list[dict[str, Any]],
) -> tuple[int, int]:
    """
    Load a starting taxonomy for a customer. Idempotent — existing names are left untouched, so
    re-running after editing the source file only adds what is new.

    Returns (created, skipped).
    """
    created = skipped = 0
    for entry in modules:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        if await get_module(db, customer_id, name):
            skipped += 1
            continue
        db.add(ModuleDocument(
            customer_id=customer_id,
            module_name=name,
            description=(entry.get("description") or "").strip(),
            status="active",
        ))
        created += 1
    await db.commit()
    return created, skipped
