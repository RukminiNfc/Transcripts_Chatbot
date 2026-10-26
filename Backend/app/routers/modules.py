"""Module document endpoints — the living specification, one document per product area.

Additive. This router reads `module_documents` and `module_document_revisions`, plus a count
from `requirements`. Nothing in the ingest or chat pipeline calls it.

Permissions follow the Minutes router rather than the Requirements one:

    read   (list, view, history, download)   any logged-in user
    write  (edit, approve a proposed module) admin only

Reading is open because these documents are the answer to "what did we agree about Orders?",
which is exactly what a non-admin user asks the chatbot. Writing is locked because a document
edited here becomes what the chatbot answers from — the index is rebuilt from it.

WHY EDITING RE-INDEXES
----------------------
The document is the source of truth and `requirements` / Qdrant are derived from it. An edit
that did not re-index would leave chat answering from text that no longer exists in the
document, which is the one failure this architecture is designed to make impossible.
"""
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.models.database import (
    Customer,
    ModuleDocument,
    ModuleDocumentRevision,
    Requirement,
    Transcript,
)
from app.models.schemas import (
    ModuleListItem,
    ModuleResponse,
    ModuleRevisionListItem,
    ModuleRevisionResponse,
    ModuleUpdate,
    ModuleMergeRequest,
)
from app.services import module_document_store as store
from app.services import module_registry as registry
from app.services.document_render import render_markdown_to_docx, safe_filename

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/modules",
    tags=["Module Documents"],
    dependencies=[Depends(get_current_user)],
)


async def _load(db: AsyncSession, module_id: uuid.UUID) -> ModuleDocument:
    module = (await db.execute(
        select(ModuleDocument).filter(ModuleDocument.id == module_id)
    )).scalars().first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return module


async def _requirement_counts(db: AsyncSession, customer_id: uuid.UUID) -> dict[str, int]:
    """
    Indexed requirements per module, counted in one grouped query.

    Keyed by `category`, which is where the indexer writes the module name — so this is the
    number the Requirements dashboard and the chatbot actually see, not a count of bullets in
    the document. They should agree; if they ever do not, the index needs rebuilding, and
    showing the real number is what makes that visible.
    """
    rows = (await db.execute(
        select(Requirement.category, func.count(Requirement.id))
        .filter(Requirement.customer_id == customer_id)
        .group_by(Requirement.category)
    )).all()
    return {(category or ""): count for category, count in rows}


async def _call_dates(
    db: AsyncSession,
    revisions: list[ModuleDocumentRevision],
) -> dict[uuid.UUID, object]:
    """
    Meeting dates for a set of revisions, in one query.

    A revision stores the transcript it came from but not the call date, so this reads it from
    the transcript — the single place that owns it. Looked up rather than copied onto the
    revision: a date stored twice is a date that can disagree with itself.
    """
    ids = {r.transcript_id for r in revisions if r.transcript_id}
    if not ids:
        return {}
    rows = (await db.execute(
        select(Transcript.id, Transcript.call_date).filter(Transcript.id.in_(ids))
    )).all()
    return {row[0]: row[1] for row in rows}


@router.get("/", response_model=List[ModuleListItem])
async def list_modules(
    customer_id: Optional[uuid.UUID] = None,
    include_empty: bool = True,
    status: str = "active",
    db: AsyncSession = Depends(get_db),
):
    """
    Every module for a customer, alphabetically.

    `status=proposed` lists modules the classifier asked for and an admin has not approved —
    they hold no content and are invisible to classification until approved.

    Modules with no content yet are included by default: an empty module is a real statement
    ("nothing has been agreed about Job Offers"), and hiding it makes the taxonomy look smaller
    than it is.
    """
    query = select(ModuleDocument)
    if customer_id:
        query = query.filter(ModuleDocument.customer_id == customer_id)
    if status and status != "all":
        query = query.filter(ModuleDocument.status == status)

    modules = (await db.execute(
        query.order_by(ModuleDocument.module_name)
    )).scalars().all()

    if not modules:
        return []

    revision_counts = dict((await db.execute(
        select(ModuleDocumentRevision.module_document_id,
               func.count(ModuleDocumentRevision.id))
        .group_by(ModuleDocumentRevision.module_document_id)
    )).all())

    # Latest meeting per module, joined through the transcript each revision came from.
    last_calls = dict((await db.execute(
        select(ModuleDocumentRevision.module_document_id,
               func.max(Transcript.call_date))
        .join(Transcript, Transcript.id == ModuleDocumentRevision.transcript_id)
        .group_by(ModuleDocumentRevision.module_document_id)
    )).all())

    # All modules in one response belong to one customer in practice; counting per distinct
    # customer keeps that an assumption of the data rather than of this code.
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for module in modules:
        if module.customer_id not in counts:
            counts[module.customer_id] = await _requirement_counts(db, module.customer_id)

    items = [
        ModuleListItem(
            id=module.id,
            module_name=module.module_name,
            description=module.description,
            status=module.status,
            current_version=module.current_version,
            has_content=bool(module.content_markdown),
            requirement_count=counts[module.customer_id].get(module.module_name, 0),
            revision_count=revision_counts.get(module.id, 0),
            last_call_date=last_calls.get(module.id),
            last_synced_at=module.last_synced_at,
        )
        for module in modules
    ]

    if not include_empty:
        items = [i for i in items if i.has_content]
    return items


@router.get("/{module_id}", response_model=ModuleResponse)
async def get_module(module_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """One module including the full document."""
    module = await _load(db, module_id)

    revision_count = (await db.execute(
        select(func.count(ModuleDocumentRevision.id))
        .filter(ModuleDocumentRevision.module_document_id == module.id)
    )).scalar() or 0
    counts = await _requirement_counts(db, module.customer_id)

    return ModuleResponse(
        id=module.id,
        module_name=module.module_name,
        description=module.description,
        status=module.status,
        current_version=module.current_version,
        has_content=bool(module.content_markdown),
        requirement_count=counts.get(module.module_name, 0),
        revision_count=revision_count,
        last_synced_at=module.last_synced_at,
        content_markdown=module.content_markdown,
    )


@router.get("/{module_id}/revisions", response_model=List[ModuleRevisionListItem])
async def list_revisions(
    module_id: uuid.UUID,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """
    Version history, newest first — without the snapshots.

    Each revision stores a full copy of the document, so returning them here would send the
    whole history of a 14,000-character document to render a list of dates.
    """
    await _load(db, module_id)
    revisions = await store.list_revisions(db, module_id, limit=limit)
    dates = await _call_dates(db, revisions)
    return [
        ModuleRevisionListItem(
            id=r.id,
            version_label=r.version_label,
            session_name=(r.session_name or "").strip() or None,
            transcript_id=r.transcript_id,
            revision_summary=r.revision_summary,
            change_count=len(r.line_diffs or []),
            call_date=dates.get(r.transcript_id),
            created_at=r.created_at,
        )
        for r in revisions
    ]


@router.get("/{module_id}/revisions/{revision_id}", response_model=ModuleRevisionResponse)
async def get_revision(
    module_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    One past version, with the document as it stood and what changed to produce it.

    The red/green lines are computed here rather than stored: a diff is a view of two snapshots,
    and storing it would mean a second thing to keep in step with the snapshots themselves.
    """
    await _load(db, module_id)

    revision = (await db.execute(
        select(ModuleDocumentRevision).filter(
            ModuleDocumentRevision.id == revision_id,
            ModuleDocumentRevision.module_document_id == module_id,
        )
    )).scalars().first()
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

    previous = await store.previous_revision(db, revision)
    dates = await _call_dates(db, [revision])

    return ModuleRevisionResponse(
        id=revision.id,
        version_label=revision.version_label,
        session_name=(revision.session_name or "").strip() or None,
        transcript_id=revision.transcript_id,
        revision_summary=revision.revision_summary,
        change_count=len(revision.line_diffs or []),
        call_date=dates.get(revision.transcript_id),
        created_at=revision.created_at,
        content_snapshot=revision.content_snapshot,
        line_diffs=revision.line_diffs or [],
        diff_lines=store.diff_view(
            previous.content_snapshot if previous else "",
            revision.content_snapshot or "",
        ),
        previous_version=previous.version_label if previous else None,
    )


@router.get("/{module_id}/download")
async def download_module(
    module_id: uuid.UUID,
    version: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Download the document as Word.

    `version` (e.g. v1.2) downloads that past version instead of the current one — the reason
    revisions store a full snapshot rather than only a diff.
    """
    module = await _load(db, module_id)

    markdown = module.content_markdown or ""
    label = module.current_version or ""

    if version:
        revision = await store.get_revision(db, module.id, version)
        if not revision:
            raise HTTPException(status_code=404, detail=f"Version {version} not found")
        markdown = revision.content_snapshot or ""
        label = revision.version_label or version

    if not markdown:
        raise HTTPException(
            status_code=400,
            detail="This module has no content yet — no meeting has discussed it.",
        )

    customer = (await db.execute(
        select(Customer).filter(Customer.id == module.customer_id)
    )).scalars().first()

    subtitle = "  ·  ".join(p for p in (customer.name if customer else "", label) if p)

    try:
        content = render_markdown_to_docx(
            markdown=markdown,
            title=module.module_name,
            subtitle=subtitle,
            footer="Generated from grooming call minutes.",
        )
    except Exception as exc:
        logger.error(f"Failed to render module {module_id} to docx: {exc}")
        raise HTTPException(status_code=500, detail="Could not build the Word document.")

    filename = safe_filename(module.module_name, label or "current")
    return Response(
        content=content,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch(
    "/{module_id}",
    response_model=ModuleResponse,
    dependencies=[Depends(require_admin)],
)
async def update_module(
    module_id: uuid.UUID,
    payload: ModuleUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Correct a document by hand — a mis-transcribed term, a requirement worded wrongly.

    Recorded as a normal revision, so a human edit sits in the same history as a meeting's and
    is just as reversible. The index is rebuilt immediately afterwards: an edit that changed the
    document but not what the chatbot answers from would be worse than no edit at all.
    """
    module = await _load(db, module_id)

    revision = await store.save_revision(
        db, module,
        new_content=payload.content_markdown,
        revision_summary=payload.revision_summary or "Edited by hand.",
        session_name="",
        transcript_id=None,
        # No reconciler diffs for a human edit — save_revision computes a text diff instead.
        line_diffs=None,
    )

    # Imported here, not at module scope: it constructs an embedding client, and this router is
    # imported at API startup where that is neither needed nor wanted.
    from app.services.module_indexer import module_indexer
    indexed = await module_indexer.index_module(db, module)

    if indexed["error"]:
        # The document IS saved. Surfacing the failure rather than raising keeps the edit, and
        # re-indexing is recoverable; losing the edit would not be.
        logger.error(f"Module {module_id} edited but re-indexing failed: {indexed['error']}")

    await db.refresh(module)
    counts = await _requirement_counts(db, module.customer_id)
    revision_count = (await db.execute(
        select(func.count(ModuleDocumentRevision.id))
        .filter(ModuleDocumentRevision.module_document_id == module.id)
    )).scalar() or 0

    logger.info(
        f"Module '{module.module_name}' edited by hand -> {revision.version_label} "
        f"({indexed['indexed']} requirements re-indexed)."
    )

    return ModuleResponse(
        id=module.id,
        module_name=module.module_name,
        description=module.description,
        status=module.status,
        current_version=module.current_version,
        has_content=bool(module.content_markdown),
        requirement_count=counts.get(module.module_name, 0),
        revision_count=revision_count,
        last_synced_at=module.last_synced_at,
        content_markdown=module.content_markdown,
    )


@router.post(
    "/{module_id}/approve",
    response_model=ModuleListItem,
    dependencies=[Depends(require_admin)],
)
async def approve_module(module_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Approve a module the classifier proposed, make it active, and automatically
    backfill its requirements and action items from existing meeting minutes.
    """
    module = await _load(db, module_id)
    if module.status == "active":
        raise HTTPException(status_code=400, detail="This module is already active.")

    approved = await registry.approve_module(db, module_id)
    if not approved:
        raise HTTPException(status_code=400, detail="Could not approve this module.")

    logger.info(f"Module '{approved.module_name}' approved. Starting auto-backfill...")

    from app.services.module_pipeline import backfill_single_module
    try:
        backfill_result = await backfill_single_module(db, approved.customer_id, approved)
        logger.info(f"Auto-backfill result for '{approved.module_name}': {backfill_result}")
    except Exception as exc:
        logger.error(f"Auto-backfill failed for '{approved.module_name}': {exc}", exc_info=True)

    await db.refresh(approved)
    counts = await _requirement_counts(db, approved.customer_id)
    rev_count = (await db.execute(
        select(func.count(ModuleDocumentRevision.id))
        .filter(ModuleDocumentRevision.module_document_id == approved.id)
    )).scalar() or 0

    return ModuleListItem(
        id=approved.id,
        module_name=approved.module_name,
        description=approved.description,
        status=approved.status,
        current_version=approved.current_version,
        has_content=bool(approved.content_markdown),
        requirement_count=counts.get(approved.module_name, 0),
        revision_count=rev_count,
        last_synced_at=approved.last_synced_at,
    )


@router.post(
    "/{module_id}/merge",
    response_model=ModuleListItem,
    dependencies=[Depends(require_admin)],
)
async def merge_module(
    module_id: uuid.UUID,
    payload: ModuleMergeRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Merge an unapproved proposed module into an existing active module.
    Folds any relevant meeting sections into the target module document,
    updates revisions & vector index, and removes the proposed module.
    """
    proposed = await _load(db, module_id)
    if proposed.status == "active":
        raise HTTPException(status_code=400, detail="Cannot merge an active module. Only proposed modules can be merged.")

    target = await _load(db, payload.target_module_id)
    if target.status != "active":
        raise HTTPException(status_code=400, detail="Target module must be an active module.")

    logger.info(f"Merging proposed module '{proposed.module_name}' into target '{target.module_name}'...")
    from app.services.module_pipeline import merge_proposed_into_module
    await merge_proposed_into_module(db, proposed.customer_id, proposed, target)

    await db.refresh(target)
    counts = await _requirement_counts(db, target.customer_id)
    rev_count = (await db.execute(
        select(func.count(ModuleDocumentRevision.id))
        .filter(ModuleDocumentRevision.module_document_id == target.id)
    )).scalar() or 0

    return ModuleListItem(
        id=target.id,
        module_name=target.module_name,
        description=target.description,
        status=target.status,
        current_version=target.current_version,
        has_content=bool(target.content_markdown),
        requirement_count=counts.get(target.module_name, 0),
        revision_count=rev_count,
        last_synced_at=target.last_synced_at,
    )


@router.delete(
    "/{module_id}",
    dependencies=[Depends(require_admin)],
)
@router.post(
    "/{module_id}/dismiss",
    dependencies=[Depends(require_admin)],
)
async def dismiss_module(module_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Dismiss/delete an unapproved proposed module from the registry."""
    module = await _load(db, module_id)
    if module.status == "active":
        raise HTTPException(status_code=400, detail="Cannot dismiss an active module. Only proposed modules can be dismissed.")

    name = module.module_name
    await db.delete(module)
    await db.commit()
    logger.info(f"Proposed module '{name}' ({module_id}) dismissed and removed.")
    return {"status": "dismissed", "module_name": name}


