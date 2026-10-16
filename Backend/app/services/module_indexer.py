"""Rebuild the searchable index from a module document.

The document is the source of truth. `requirements`, `requirement_versions` and the Qdrant
`requirements` collection are a DERIVED INDEX — they exist so the chatbot can count and search,
and they are regenerated from the document rather than maintained alongside it.

That distinction is what removes the sync problem. Nothing writes a requirement directly, so the
index cannot disagree with the document in a way anyone has to reconcile: if it ever looks wrong,
re-index every document and it is correct again.

WHAT WOULD BREAK WITHOUT THIS
-----------------------------
Thirteen files reference Requirement/RequirementVersion and the Qdrant requirements collection
is used in eight places. Without an index, these chat lanes return nothing:

    "how many requirements are there?"        counts rows
    "list all requirements for CI/CD"         reranks the requirements collection
    "explain the rollback requirement"        vector search
    "what changed in the last call?"          requirement_versions
    the Requirements Dashboard                rows

Reusing the existing tables rather than inventing new ones means the chat code is untouched —
only what populates them changes.

ONE MODULE AT A TIME, REPLACING WHOLESALE
-----------------------------------------
Re-indexing deletes the module's existing rows and vectors and writes the current document's
bullets. Trying to update in place would mean matching bullets to rows, which is the
requirement-matching problem this design exists to avoid — and the document already says what
is true, so there is nothing to merge.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.qdrant_client import QdrantVectorDB
from app.models.database import ModuleDocument, Requirement, RequirementVersion
from app.services.embedding_service import EmbeddingService
from app.utils.dates import session_to_ymd

logger = logging.getLogger(__name__)

# A bullet already marked as withdrawn. Kept in the document for history, excluded from the
# index: a deprecated rule answering "what are the requirements for X?" would be worse than it
# not appearing at all.
_DEPRECATED = re.compile(r"^~~DEPRECATED~~\s*", re.IGNORECASE)


def parse_document(markdown: str) -> list[dict[str, str]]:
    """
    A module document into indexable bullets.

    Returns [{"sub_heading": str, "text": str}]. Deprecated bullets are skipped. `#` title and
    `##` section headings are structure, not requirements, so only `###` sub-headings are
    carried — they become sub_category, which the dashboard groups by.
    """
    bullets: list[dict[str, str]] = []
    sub_heading = ""

    for raw in (markdown or "").split("\n"):
        line = raw.strip()

        if m := re.match(r"^###\s+(.+?)\s*$", line):
            sub_heading = m.group(1).strip()
            continue
        # A `##` section resets the sub-heading: bullets under it belong to the section, not to
        # whichever `###` happened to come last in the previous one.
        if re.match(r"^##\s+(?!#)", line):
            sub_heading = ""
            continue

        if m := re.match(r"^[-*+]\s+(.+?)\s*$", line):
            text = m.group(1).strip()
            if _DEPRECATED.match(text):
                continue
            if text:
                bullets.append({"sub_heading": sub_heading, "text": text})

    return bullets


class ModuleIndexer:
    """Regenerates the requirement index for one module document."""

    def __init__(self) -> None:
        self.qdrant = QdrantVectorDB()
        self.embedding_service = EmbeddingService()

    async def _clear_module(
        self,
        db: AsyncSession,
        customer_id: uuid.UUID,
        module_name: str,
    ) -> int:
        """
        Remove this module's existing rows and vectors.

        Versions are deleted before requirements, and vectors before either: a requirement row
        whose vector survives would be returned by search and then fail to load, which looks
        like data corruption rather than a stale index.
        """
        req_ids = list((await db.execute(
            select(Requirement.id).filter(
                Requirement.customer_id == customer_id,
                Requirement.category == module_name,
            )
        )).scalars().all())

        if not req_ids:
            return 0

        vector_ids = list((await db.execute(
            select(RequirementVersion.vector_id).filter(
                RequirementVersion.requirement_id.in_(req_ids),
                RequirementVersion.vector_id.isnot(None),
            )
        )).scalars().all())

        if vector_ids:
            try:
                self.qdrant.delete_by_ids(
                    collection_name=self.qdrant.requirements_collection,
                    point_ids=[v for v in vector_ids if v],
                )
            except Exception as exc:
                # Postgres is still cleaned: a leftover vector is findable by
                # purge_orphan_vectors.py, whereas a leftover row is duplicated on the next run.
                logger.warning(f"Could not delete {len(vector_ids)} stale vectors: {exc}")

        await db.execute(delete(RequirementVersion).where(
            RequirementVersion.requirement_id.in_(req_ids)))
        await db.execute(delete(Requirement).where(Requirement.id.in_(req_ids)))
        await db.commit()
        return len(req_ids)

    async def index_module(
        self,
        db: AsyncSession,
        module: ModuleDocument,
        session_name: str = "",
        call_date: Optional[datetime] = None,
        transcript_id: Optional[uuid.UUID] = None,
    ) -> dict[str, Any]:
        """
        Replace this module's index with the current document's bullets.

        Returns {"indexed": int, "removed": int, "error": str}.
        Never raises: the document is already saved, and a failed index is recoverable by
        re-running, whereas an exception here would roll back a successful reconciliation.
        """
        result: dict[str, Any] = {"indexed": 0, "removed": 0, "error": ""}

        bullets = parse_document(module.content_markdown or "")
        if not bullets:
            result["error"] = "Document has no indexable bullets."
            return result

        try:
            result["removed"] = await self._clear_module(db, module.customer_id, module.module_name)

            texts = [b["text"] for b in bullets]
            vectors = self.embedding_service.generate_embeddings(texts)

            date_ymd = session_to_ymd(session_name, call_date) if session_name else None
            points_vectors, points_payloads, points_ids = [], [], []

            for bullet, vector in zip(bullets, vectors):
                req_id = uuid.uuid4()
                version_id = uuid.uuid4()

                db.add(Requirement(
                    id=req_id,
                    customer_id=module.customer_id,
                    # category/sub_category carry the module structure, so the dashboard and the
                    # chat's category filters keep working with no changes.
                    category=module.module_name,
                    sub_category=bullet["sub_heading"] or None,
                    current_text=bullet["text"],
                    canonical_text=bullet["text"],
                    status="active",
                ))

                # One version row per requirement. The meaningful history now lives in
                # ModuleDocumentRevision; this exists so the chat lanes that read
                # requirement_versions still find a row, and carries the module's version so a
                # requirement can be traced back to the revision that produced it.
                db.add(RequirementVersion(
                    id=version_id,
                    requirement_id=req_id,
                    version_number=1,
                    text=bullet["text"],
                    change_type="indexed",
                    session=session_name or None,
                    discussed_date=call_date,
                    transcript_id=transcript_id,
                    vector_id=str(version_id),
                ))

                points_vectors.append(vector)
                points_ids.append(str(version_id))
                points_payloads.append({
                    "version_id": str(version_id),
                    "requirement_id": str(req_id),
                    "customer_id": str(module.customer_id),
                    "category": module.module_name,
                    "sub_category": bullet["sub_heading"] or "",
                    "requirement_text": bullet["text"],
                    "canonical_text": bullet["text"],
                    "module_document_id": str(module.id),
                    "module_version": module.current_version or "",
                    "session": session_name or "",
                    "date_ymd": date_ymd or "",
                    "transcript_id": str(transcript_id) if transcript_id else "",
                    "change_type": "indexed",
                    "confirmed_by": "",
                    "proposed_by": "",
                })

            await db.commit()

            self.qdrant.add_vectors(
                vectors=points_vectors,
                payloads=points_payloads,
                ids=points_ids,
                collection_name=self.qdrant.requirements_collection,
            )

            result["indexed"] = len(bullets)
            logger.info(
                f"Indexed {module.module_name}: {result['indexed']} requirements "
                f"(replaced {result['removed']})."
            )
        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"Indexing failed for '{module.module_name}': {exc}")

        return result


module_indexer = ModuleIndexer()
