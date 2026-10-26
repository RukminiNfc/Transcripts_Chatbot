"""Keep the searchable index in step with a module document — one row per requirement block.

The document is the source of truth. `requirements`, `requirement_versions` and the Qdrant
`requirements` collection are a DERIVED INDEX — they exist so the chatbot can count and search.
Nothing else writes a module requirement, so if the index ever looks wrong, rebuilding it from
the document's revisions (rebuild_module_history) makes it correct again.

WHAT A ROW IS
-------------
One requirement = one `###` block of the document = one MOM sub-section ("Topic: Sub-section"
plus its bullets; see requirement_blocks.py). The row is identified by (module_document_id,
heading_key), so the SAME block keeps the SAME row id from meeting to meeting:

    block unchanged                -> nothing written
    block's text changed           -> same row, next version number, change_type "modified"
    new block                      -> new row, version 1, change_type "added"
    block deprecated or deleted    -> row status deprecated/removed, version "removed",
                                      vector deleted so chat stops answering from it

That is what gives each requirement its own history. The chat "what changed" lane reads exactly
these change types and finds the previous text at version_number - 1, so version numbers stay a
contiguous 1..n chain per requirement.

Exactly ONE vector per live requirement: a new version's vector replaces the previous one, so
search never returns a requirement's old wording.

WHAT WOULD BREAK WITHOUT THIS
-----------------------------
    "how many requirements are there?"        counts rows
    "list all requirements for CI/CD"         reranks the requirements collection
    "explain the rollback requirement"        vector search
    "what changed in the last call?"          requirement_versions
    the Requirements Dashboard                rows
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.qdrant_client import QdrantVectorDB
from app.models.database import (
    ModuleDocument,
    ModuleDocumentRevision,
    Requirement,
    RequirementVersion,
    Transcript,
)
from app.services.embedding_service import EmbeddingService
from app.services.requirement_blocks import TITLE_SEPARATOR, parse_document_blocks
from app.utils.dates import session_to_datetime, session_to_ymd

logger = logging.getLogger(__name__)

# Legacy: bullet-level parse, kept for scripts that still read documents bullet by bullet.
_DEPRECATED = re.compile(r"^~~DEPRECATED~~\s*", re.IGNORECASE)


def parse_document(markdown: str) -> list[dict[str, str]]:
    """A module document into its bullets: [{"sub_heading", "text"}]. Deprecated blocks skipped."""
    out: list[dict[str, str]] = []
    for block in parse_document_blocks(markdown):
        if block["deprecated"]:
            continue
        for bullet in block["bullets"]:
            text = bullet.strip()
            if text and not _DEPRECATED.match(text):
                out.append({"sub_heading": block["title"], "text": text})
    return out


def block_text(block: dict[str, Any]) -> str:
    """What a requirement row stores and what gets embedded: the title, then its bullets."""
    lines = [block["title"]]
    lines.extend(f"- {b.strip()}" for b in block.get("bullets") or [])
    return "\n".join(lines)


def _topic(title: str) -> str:
    return title.split(TITLE_SEPARATOR, 1)[0].strip() if TITLE_SEPARATOR in title else ""


class ModuleIndexer:
    """Keeps one module's requirement rows, version history and vectors in step with its document."""

    def __init__(self) -> None:
        self.qdrant = QdrantVectorDB()
        self.embedding_service = EmbeddingService()

    async def _clear_module(
        self,
        db: AsyncSession,
        customer_id: uuid.UUID,
        module_name: str,
        module_document_id: Optional[uuid.UUID] = None,
    ) -> int:
        """
        Remove every row, version and vector belonging to this module.

        Matches both the block rows (module_document_id) and rows from the earlier bullet-level
        indexer, which carried only the module name in `category`. Versions are deleted before
        requirements and vectors before either: a row whose vector survives would be returned by
        search and then fail to load.
        """
        conditions = [Requirement.category == module_name]
        if module_document_id is not None:
            conditions.append(Requirement.module_document_id == module_document_id)
        req_ids = list((await db.execute(
            select(Requirement.id).filter(Requirement.customer_id == customer_id, or_(*conditions))
        )).scalars().all())
        if not req_ids:
            return 0

        vector_ids = [v for v in (await db.execute(
            select(RequirementVersion.vector_id).filter(
                RequirementVersion.requirement_id.in_(req_ids),
                RequirementVersion.vector_id.isnot(None),
            )
        )).scalars().all() if v]
        if vector_ids:
            try:
                self.qdrant.delete_by_ids(collection_name=self.qdrant.requirements_collection,
                                          point_ids=vector_ids)
            except Exception as exc:
                logger.warning(f"Could not delete {len(vector_ids)} stale vectors: {exc}")

        await db.execute(delete(RequirementVersion).where(RequirementVersion.requirement_id.in_(req_ids)))
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
        content: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Bring this module's rows, versions and vectors in step with its document.

        `content` defaults to the module's current document; rebuild_module_history passes each
        past revision's snapshot instead. Only requirements whose text changed are re-embedded.

        Returns {"indexed", "added", "modified", "removed", "unchanged", "error"}, where
        "indexed" is the number of live requirements after the update. Never raises: the document
        is already saved, and a failed index is recoverable by rebuild_module_history.
        """
        result: dict[str, Any] = {"indexed": 0, "added": 0, "modified": 0, "removed": 0,
                                  "unchanged": 0, "error": ""}
        markdown = module.content_markdown if content is None else content
        blocks = parse_document_blocks(markdown or "")
        session = session_name or None
        date_ymd = session_to_ymd(session_name, call_date) if session_name else None

        try:
            # Rows written by the earlier bullet-level indexer have no module_document_id. They
            # cannot be matched to blocks, so the first block-level index of a module replaces them.
            legacy = (await db.execute(select(func.count()).select_from(Requirement).filter(
                Requirement.customer_id == module.customer_id,
                Requirement.category == module.module_name,
                Requirement.module_document_id.is_(None),
            ))).scalar() or 0
            if legacy:
                logger.info(f"{module.module_name}: replacing {legacy} bullet-level rows with block rows.")
                await self._clear_module(db, module.customer_id, module.module_name, module.id)

            rows = {r.heading_key: r for r in (await db.execute(
                select(Requirement).filter(Requirement.module_document_id == module.id)
            )).scalars().all()}
            latest: dict[uuid.UUID, RequirementVersion] = {}
            if rows:
                for v in (await db.execute(
                    select(RequirementVersion)
                    .filter(RequirementVersion.requirement_id.in_([r.id for r in rows.values()]))
                    .order_by(RequirementVersion.version_number)
                )).scalars().all():
                    latest[v.requirement_id] = v          # ascending, so the last one wins

            to_embed: list[tuple[Requirement, RequirementVersion, dict[str, Any]]] = []
            stale_vectors: list[str] = []
            seen: set[str] = set()

            def new_version(row: Requirement, change_type: str, text: str) -> RequirementVersion:
                prev = latest.get(row.id)
                v = RequirementVersion(
                    id=uuid.uuid4(),
                    requirement_id=row.id,
                    version_number=(prev.version_number if prev else 0) + 1,
                    text=text,
                    change_type=change_type,
                    session=session,
                    discussed_date=call_date,
                    transcript_id=transcript_id,
                )
                db.add(v)
                if prev is not None and prev.vector_id:
                    stale_vectors.append(prev.vector_id)
                latest[row.id] = v
                return v

            for block in blocks:
                key = block["heading_key"]
                if key in seen:                     # same title twice in one document: first wins
                    continue
                seen.add(key)
                text = block_text(block)
                row = rows.get(key)

                if block["deprecated"]:
                    if row is not None and row.status == "active":
                        row.status = "deprecated"
                        new_version(row, "removed", row.current_text)
                        result["removed"] += 1
                    continue

                if row is None:
                    row = Requirement(
                        id=uuid.uuid4(), customer_id=module.customer_id,
                        module_document_id=module.id, heading_key=key, title=block["title"],
                        category=module.module_name, sub_category=_topic(block["title"]) or None,
                        current_text=text, canonical_text=text, status="active",
                    )
                    db.add(row)
                    rows[key] = row
                    to_embed.append((row, new_version(row, "added", text), block))
                    result["added"] += 1
                elif row.current_text != text or row.status != "active":
                    row.current_text = text
                    row.canonical_text = text
                    row.title = block["title"]
                    row.category = module.module_name
                    row.status = "active"
                    to_embed.append((row, new_version(row, "modified", text), block))
                    result["modified"] += 1
                else:
                    result["unchanged"] += 1

            # A live row whose block is no longer in the document was deleted by hand.
            for key, row in rows.items():
                if key not in seen and row.status == "active":
                    row.status = "removed"
                    new_version(row, "removed", row.current_text)
                    result["removed"] += 1

            vectors = self.embedding_service.generate_embeddings([block_text(b) for _, _, b in to_embed]) \
                if to_embed else []
            ids, payloads = [], []
            for (row, version, block), _vec in zip(to_embed, vectors):
                version.vector_id = str(version.id)
                ids.append(str(version.id))
                payloads.append({
                    "version_id": str(version.id),
                    "requirement_id": str(row.id),
                    "customer_id": str(module.customer_id),
                    "category": module.module_name,
                    "sub_category": row.sub_category or "",
                    "title": block["title"],
                    "requirement_text": row.current_text,
                    "canonical_text": row.current_text,
                    "module_document_id": str(module.id),
                    "module_version": module.current_version or "",
                    "session": session_name or "",
                    "date_ymd": date_ymd or "",
                    "transcript_id": str(transcript_id) if transcript_id else "",
                    "change_type": version.change_type,
                    "confirmed_by": "",
                    "proposed_by": "",
                })

            await db.commit()

            # Vectors after the commit: a vector whose row failed to save would be returned by
            # search and then fail to load. Old vectors go first so search never sees two
            # versions of the same requirement.
            if stale_vectors:
                try:
                    self.qdrant.delete_by_ids(collection_name=self.qdrant.requirements_collection,
                                              point_ids=stale_vectors)
                except Exception as exc:
                    logger.warning(f"{module.module_name}: could not delete {len(stale_vectors)} "
                                   f"superseded vectors: {exc}")
            if ids and not self.qdrant.add_vectors(vectors=vectors, payloads=payloads, ids=ids,
                                                   collection_name=self.qdrant.requirements_collection):
                result["error"] = "Vectors could not be written to Qdrant — run rebuild_module_history."

            result["indexed"] = sum(1 for r in rows.values() if r.status == "active")
            logger.info(
                f"Indexed {module.module_name}: {result['indexed']} live requirements "
                f"(+{result['added']} added, {result['modified']} modified, "
                f"{result['removed']} removed, {result['unchanged']} unchanged)."
            )
        except Exception as exc:
            await db.rollback()
            result["error"] = str(exc)
            logger.error(f"Indexing failed for '{module.module_name}': {exc}")

        return result

    async def rebuild_module_history(self, db: AsyncSession, module: ModuleDocument) -> dict[str, Any]:
        """
        Rebuild this module's rows, versions and vectors from scratch by replaying its revisions.

        Each stored revision's snapshot is indexed in date order with that revision's meeting, so
        the per-requirement history comes out exactly as if the meetings had been processed one by
        one. Used after a rollback (which removes revisions) and as the general repair path.
        A module with no revisions ends up with an empty index.
        """
        removed = await self._clear_module(db, module.customer_id, module.module_name, module.id)
        revisions = (await db.execute(
            select(ModuleDocumentRevision)
            .filter(ModuleDocumentRevision.module_document_id == module.id)
            .order_by(ModuleDocumentRevision.created_at)
        )).scalars().all()

        dates: dict[uuid.UUID, Optional[datetime]] = {}
        transcript_ids = {r.transcript_id for r in revisions if r.transcript_id}
        if transcript_ids:
            for t in (await db.execute(select(Transcript).filter(Transcript.id.in_(transcript_ids)))).scalars():
                dates[t.id] = t.call_date

        last: dict[str, Any] = {"indexed": 0, "error": ""}
        for rev in revisions:
            call_date = dates.get(rev.transcript_id)
            if call_date is not None and rev.session_name:
                call_date = session_to_datetime(rev.session_name, call_date)
            last = await self.index_module(
                db, module,
                session_name=rev.session_name or "",
                call_date=call_date,
                transcript_id=rev.transcript_id,
                content=rev.content_snapshot or "",
            )
            if last["error"]:
                break
        return {"revisions_replayed": len(revisions), "rows_cleared": removed,
                "indexed": last["indexed"], "error": last["error"]}


module_indexer = ModuleIndexer()
