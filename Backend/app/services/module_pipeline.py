"""Turn one meeting's minutes into updated module documents.

The orchestration layer: the pieces it calls are each tested on their own, and this is where
they run in order for a real transcript.

    minutes -> classify sections into modules
            -> reconcile each touched module's document
            -> re-index that module for chat

WHY THE MINUTES AND NOT THE TRANSCRIPT
--------------------------------------
The minutes prompt already produces exactly the hierarchy this needs — `## area`, `###
sub-heading`, `- requirement` — so re-extracting from the transcript would pay a second LLM
pass over the same content to produce the same shape. It also means a human correcting a
mis-transcribed word in the minutes fixes the requirements too, which is a quality gate no
prompt tuning provides.

PER-MODULE ISOLATION
--------------------
Each module is reconciled, saved and indexed independently, and a failure on one does not stop
the others. Three modules updated and one failed is a better outcome than nothing updated — the
failed one keeps its previous revision and is recoverable by re-running, because reconciliation
reads the document as it currently stands rather than assuming what is in it.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import module_document_store as store
from app.services import module_registry as registry
from app.services.module_classifier import module_classifier, parse_sections
from app.services.module_indexer import module_indexer

logger = logging.getLogger(__name__)


async def process_minutes(
    db: AsyncSession,
    customer_id: uuid.UUID,
    minutes_markdown: str,
    session_name: str,
    call_date: Optional[datetime] = None,
    transcript_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """
    Fold one meeting's minutes into the project's module documents.

    Returns a summary suitable for logging and for the trace:
        {"sections", "modules_touched", "updated", "failed", "proposed",
         "total_changes", "details": [...], "error"}
    """
    summary: dict[str, Any] = {
        "sections": 0, "modules_touched": 0, "updated": 0, "unchanged": 0, "failed": 0,
        "proposed": [], "total_changes": 0, "details": [], "error": "",
    }

    sections = parse_sections(minutes_markdown)
    summary["sections"] = len(sections)
    if not sections:
        summary["error"] = "No sections found in the minutes."
        return summary

    modules = await registry.list_modules(db, customer_id)
    if not modules:
        summary["error"] = "No modules defined for this customer — run seed_modules.py first."
        logger.error(summary["error"])
        return summary

    # Imported here rather than at module scope: it builds an OpenAI client on construction,
    # and importing this module should not require a working API key.
    from app.services.module_reconciler import module_reconciler

    classified = module_classifier.classify(sections, registry.as_classifier_block(modules))
    if classified["error"]:
        summary["error"] = f"Classification failed: {classified['error']}"
        return summary

    # Group the meeting's sections by the module they were filed under, so each module is
    # reconciled once with everything that belongs to it rather than once per section.
    by_module: dict[str, list[dict[str, Any]]] = {}
    for assignment in classified["assignments"]:
        index = assignment.get("index")
        name = (assignment.get("module") or "").strip()
        if not isinstance(index, int) or not (0 <= index < len(sections)) or not name:
            continue
        by_module.setdefault(name, []).append(sections[index])

    # A module the classifier asked for that does not exist is recorded for admin approval, not
    # created. Its requirements are reported here and picked up once the module is approved and
    # the transcript re-run — losing them silently would be worse than asking.
    for proposal in classified.get("proposals", []):
        name = (proposal.get("name") or "").strip()
        if not name:
            continue
        await registry.propose_module(db, customer_id, name, proposal.get("description", ""))
        summary["proposed"].append(name)

    summary["modules_touched"] = len(by_module)
    date_str = call_date.strftime("%Y-%m-%d") if call_date else ""

    for module_name, module_sections in by_module.items():
        module = await registry.get_module(db, customer_id, module_name)
        if not module or module.status != "active":
            logger.warning(f"Skipping '{module_name}' — not an active module.")
            summary["failed"] += 1
            continue

        outcome = module_reconciler.reconcile(
            module_name=module.module_name,
            module_description=module.description or "",
            current_document=module.content_markdown,
            sections=module_sections,
            session_name=session_name,
            call_date=date_str,
        )

        if outcome["error"] or not outcome["document"]:
            # The document is left exactly as it was. Re-running reads it fresh, so a retry
            # picks up where this left off rather than compounding a partial edit.
            logger.error(f"Reconciliation failed for '{module_name}': {outcome['error']}")
            summary["failed"] += 1
            summary["details"].append({
                "module": module_name, "status": "failed", "error": outcome["error"],
            })
            continue

        # The meeting restated what this module already said. Recording a revision for it would
        # bump the version and store another full copy of an identical document, leaving the
        # history full of entries that say nothing happened. The index is already correct too,
        # since it was built from this same content.
        if not outcome.get("changed"):
            summary["unchanged"] += 1
            summary["details"].append({
                "module": module_name,
                "status": "unchanged",
                "version": module.current_version or "",
                "changes": 0,
                "indexed": 0,
                "summary": outcome["summary"],
            })
            continue

        revision = await store.save_revision(
            db, module,
            new_content=outcome["document"],
            revision_summary=outcome["summary"],
            session_name=session_name,
            transcript_id=transcript_id,
            line_diffs=outcome["diffs"],
        )

        # Indexed after saving, never before: the index is derived from the document, and
        # indexing content that failed to save would leave chat answering from a revision that
        # does not exist.
        indexed = await module_indexer.index_module(
            db, module,
            session_name=session_name, call_date=call_date, transcript_id=transcript_id,
        )

        summary["updated"] += 1
        summary["total_changes"] += len(outcome["diffs"])
        summary["details"].append({
            "module": module_name,
            "status": "updated",
            "version": revision.version_label,
            "changes": len(outcome["diffs"]),
            "indexed": indexed["indexed"],
            "summary": outcome["summary"],
        })

    logger.info(
        f"Module documents for '{session_name}': {summary['updated']} updated, "
        f"{summary['unchanged']} unchanged, {summary['failed']} failed, "
        f"{summary['total_changes']} change(s)"
        + (f", proposed {summary['proposed']}" if summary["proposed"] else "")
    )
    return summary
