"""Saving and reading module documents, with their version history.

A module document is a living specification: the current text is overwritten on every update,
and each update is recorded as a revision. That is the opposite of the requirement_versions
model, where every mention appended a row and a rule discussed ten times produced ten rows to
read through.

Two stores, deliberately:

    ModuleDocument.content_markdown    the CURRENT text — one row, one read
    ModuleDocumentRevision             every past version, with diffs and a full snapshot

Nothing here calls an LLM. Deciding what changed is the reconciliation engine's job; this
records the outcome.
"""
from __future__ import annotations

import difflib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ModuleDocument, ModuleDocumentRevision

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)$")


def next_version(current: Optional[str]) -> str:
    """
    v1.0 -> v1.1 -> v1.2 ...

    Only the minor part moves. A major bump would have to mean something — a release boundary,
    a re-baseline — and nothing here knows about those, so inventing a rule would make the
    number look meaningful when it is not.
    """
    if not current:
        return "v1.0"
    m = _VERSION_RE.match(current.strip())
    if not m:
        logger.warning(f"Unrecognised version '{current}'; restarting at v1.0.")
        return "v1.0"
    major, minor = int(m.group(1)), int(m.group(2))
    return f"v{major}.{minor + 1}"


def compute_line_diff(old: str, new: str) -> list[dict[str, str]]:
    """
    Line-level added/removed, for the red/green view.

    A fallback, not the primary source: reconciliation returns its own diffs, which say WHY
    something changed. This only recovers WHAT changed, and is used when those are missing —
    a human edit, for instance, which has no model reasoning behind it.
    """
    diffs: list[dict[str, str]] = []
    for line in difflib.unified_diff(
        (old or "").split("\n"), (new or "").split("\n"), lineterm="", n=0
    ):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            diffs.append({"action": "ADDED", "new_text": line[1:].strip()})
        elif line.startswith("-"):
            diffs.append({"action": "REMOVED", "old_text": line[1:].strip()})
    return diffs


def diff_view(old: str, new: str) -> list[dict[str, str]]:
    """
    The whole document as lines tagged same / added / removed — the red/green view.

    Distinct from compute_line_diff above, which lists only what moved. A reader comparing two
    versions needs the unchanged lines around a change to see where it landed; a bare list of
    edits reads like a changelog, not a document.
    """
    old_lines = (old or "").split("\n")
    new_lines = (new or "").split("\n")
    view: list[dict[str, str]] = []

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines, autojunk=False
    ).get_opcodes():
        if tag in ("equal",):
            view.extend({"type": "same", "text": line} for line in new_lines[j1:j2])
        else:
            # "replace" is a removal and an insertion at the same place: showing both, old first,
            # is what makes a reworded requirement readable as one change rather than two.
            view.extend({"type": "removed", "text": line} for line in old_lines[i1:i2])
            view.extend({"type": "added", "text": line} for line in new_lines[j1:j2])

    return view


async def previous_revision(
    db: AsyncSession,
    revision: ModuleDocumentRevision,
) -> Optional[ModuleDocumentRevision]:
    """
    The revision immediately before this one for the same document.

    Ordered by created_at rather than by parsing version_label: the label is a display string,
    and ordering the history by a string is the kind of assumption that survives right up until
    a document reaches v1.10 and sorts before v1.2.
    """
    return (await db.execute(
        select(ModuleDocumentRevision)
        .filter(
            ModuleDocumentRevision.module_document_id == revision.module_document_id,
            ModuleDocumentRevision.created_at < revision.created_at,
        )
        .order_by(ModuleDocumentRevision.created_at.desc())
        .limit(1)
    )).scalars().first()


async def get_document(
    db: AsyncSession,
    customer_id: uuid.UUID,
    module_name: str,
) -> Optional[ModuleDocument]:
    """One module's current document. Case-insensitive on the name."""
    return (await db.execute(
        select(ModuleDocument).filter(
            ModuleDocument.customer_id == customer_id,
            ModuleDocument.module_name.ilike(module_name.strip()),
        )
    )).scalars().first()


async def save_revision(
    db: AsyncSession,
    module: ModuleDocument,
    new_content: str,
    revision_summary: str,
    session_name: str = "",
    transcript_id: Optional[uuid.UUID] = None,
    line_diffs: Optional[list[dict[str, Any]]] = None,
) -> ModuleDocumentRevision:
    """
    Overwrite the document and record the version that produced it.

    Both happen in one commit. Splitting them would allow a document whose text has moved on
    while its history says otherwise — and the history is the part nobody checks until it is
    already wrong.

    `line_diffs` should come from reconciliation, which knows why each change was made. When
    omitted, a text diff is computed so the revision is still viewable.
    """
    previous = module.content_markdown or ""

    # A module row carries current_version='v1.0' from the moment it is created, before any
    # content exists. Bumping that on the first save would make the document's first version
    # v1.1 and leave a v1.0 that was never written. An empty document means this IS v1.0.
    version = next_version(module.current_version) if previous else "v1.0"

    if line_diffs is None:
        line_diffs = compute_line_diff(previous, new_content)

    revision = ModuleDocumentRevision(
        module_document_id=module.id,
        transcript_id=transcript_id,
        session_name=session_name or None,
        version_label=version,
        revision_summary=revision_summary or "",
        line_diffs=line_diffs,
        content_snapshot=new_content,
    )
    db.add(revision)

    module.content_markdown = new_content
    module.current_version = version
    module.last_synced_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(revision)

    logger.info(
        f"{module.module_name}: {version} "
        f"({len(line_diffs)} change(s)) — {revision_summary[:60]}"
    )
    return revision


async def list_revisions(
    db: AsyncSession,
    module_document_id: uuid.UUID,
    limit: int = 50,
) -> list[ModuleDocumentRevision]:
    """Version history, newest first."""
    rows = (await db.execute(
        select(ModuleDocumentRevision)
        .filter(ModuleDocumentRevision.module_document_id == module_document_id)
        .order_by(ModuleDocumentRevision.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return list(rows)


async def get_revision(
    db: AsyncSession,
    module_document_id: uuid.UUID,
    version_label: str,
) -> Optional[ModuleDocumentRevision]:
    """One past version, for viewing or restoring. Reads the stored snapshot — no replay."""
    return (await db.execute(
        select(ModuleDocumentRevision).filter(
            ModuleDocumentRevision.module_document_id == module_document_id,
            ModuleDocumentRevision.version_label == version_label,
        )
    )).scalars().first()


async def document_summaries(
    db: AsyncSession,
    customer_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    One row per module for the Documents page: name, version, when, how many revisions.

    Counts come from a grouped query rather than from loading the revisions themselves — the
    list view never needs their text, and a module with fifty revisions would otherwise drag
    fifty snapshots across for a number.
    """
    counts = dict((await db.execute(
        select(ModuleDocumentRevision.module_document_id,
               func.count(ModuleDocumentRevision.id))
        .group_by(ModuleDocumentRevision.module_document_id)
    )).all())

    modules = (await db.execute(
        select(ModuleDocument)
        .filter(ModuleDocument.customer_id == customer_id,
                ModuleDocument.status == "active")
        .order_by(ModuleDocument.module_name)
    )).scalars().all()

    return [{
        "id": str(m.id),
        "module_name": m.module_name,
        "description": m.description,
        "current_version": m.current_version,
        "has_content": bool(m.content_markdown),
        "revision_count": counts.get(m.id, 0),
        "last_synced_at": m.last_synced_at.isoformat() if m.last_synced_at else None,
    } for m in modules]
