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
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import annotate, trace_span
from app.services import module_document_store as store
from app.services import module_registry as registry
from app.services.module_classifier import module_classifier, parse_sections
from app.services.module_document_writer import apply_decisions
from app.services.candidate_prefilter import select_candidate_blocks
from app.services.module_indexer import module_indexer
from app.services.requirement_blocks import parse_document_blocks

logger = logging.getLogger(__name__)


def _matches_module(area: str, mod_name: str, mod_sections: list[dict[str, Any]]) -> bool:
    if not area:
        return False
    a = re.sub(r"[^a-z0-9]+", "", area.lower())
    m = re.sub(r"[^a-z0-9]+", "", mod_name.lower())
    if a and (a in m or m in a):
        return True
    for s in mod_sections:
        s_norm = re.sub(r"[^a-z0-9]+", "", str(s.get("heading") or "").lower())
        if a and (a in s_norm or s_norm in a):
            return True
    return False


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

    # OBSERVABILITY (removable): one span per stage so the cost report can say what classify,
    # each module's reconcile, and each re-index cost. No-ops when tracing is off.
    with trace_span("module_classify", sections=len(sections), modules=len(modules)):
        classified = module_classifier.classify(sections, registry.as_classifier_block(modules))
        annotate(
            assigned=len(classified["assignments"]),
            proposed=len(classified["proposals"]),
            error=classified["error"] or None,
        )
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

    from app.services.mom_action_items import parse_action_items

    # Action Items from MOM's Consolidated Action Items (Priority 2)
    all_action_items = parse_action_items(minutes_markdown, call_date.date() if call_date else None)

    summary["modules_touched"] = len(by_module)
    date_str = call_date.strftime("%Y-%m-%d") if call_date else ""

    for module_name, module_sections in by_module.items():
        module = await registry.get_module(db, customer_id, module_name)
        if not module or module.status != "active":
            logger.warning(f"Skipping '{module_name}' — not an active module.")
            summary["failed"] += 1
            continue

        module_action_items = [
            item for item in all_action_items
            if _matches_module(item.get("area", ""), module_name, module_sections)
        ]

        # One MOM sub-section = one requirement (requirement_blocks.py). The model compares this
        # meeting's requirements with the module's current ones and returns decisions only; the
        # writer applies them, so requirements the meeting did not touch are never re-typed.
        incoming = [r for s in module_sections for r in (s.get("requirements") or [])]
        existing = [b for b in parse_document_blocks(module.content_markdown or "")
                    if not b["deprecated"]]
        candidate_existing = select_candidate_blocks(existing, incoming, max_candidates=10)
        with trace_span(
            "module_reconcile",
            module=module.module_name,
            document_chars_in=len(module.content_markdown or ""),
            sections=len(module_sections),
            existing_requirements=len(existing),
            candidates_sent=len(candidate_existing),
            bullets_in=len(incoming),     # the report's IN column: incoming requirements
        ):
            reconciled = module_reconciler.reconcile_blocks(
                module_name=module.module_name,
                module_description=module.description or "",
                existing=candidate_existing,
                incoming=incoming,
                session_name=session_name,
                call_date=date_str,
            )
            outcome: dict[str, Any] = {
                "error": reconciled["error"], "summary": reconciled["summary"],
                "document": "", "changed": False, "diffs": [],
            }
            if not reconciled["error"]:
                written = apply_decisions(
                    module.content_markdown,
                    module.module_name,
                    module.description or "",
                    reconciled["decisions"],
                    action_items=module_action_items,
                    session_name=session_name,
                )
                outcome.update(document=written["document"], changed=written["changed"],
                               diffs=written["diffs"])

            # The ADD / MODIFY / DEPRECATE / SKIP split per module is the number that says
            # whether change detection is working. Counted from the DECISIONS, per incoming
            # requirement; the diffs above count per existing requirement actually changed.
            def n(action: str) -> int:
                return sum(len(d["incoming_keys"]) for d in reconciled["decisions"]
                           if d["action"] == action)
            annotate(
                document_chars_out=len(outcome["document"] or ""),
                changed=bool(outcome["changed"]),
                added=n("ADD"),
                modified=n("MODIFY"),
                deprecated=n("DEPRECATE"),
                skipped=n("SKIP"),
                skipped_task_only=sum(len(d["incoming_keys"]) for d in reconciled["decisions"]
                                      if d["action"] == "SKIP" and d.get("reason") == "task_only"),
                task_bullets_dropped=sum(len(d.get("dropped") or []) for d in reconciled["decisions"]),
                requirements_changed=len(outcome["diffs"]),
                error=outcome["error"] or None,
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
        with trace_span("module_index", module=module.module_name):
            indexed = await module_indexer.index_module(
                db, module,
                session_name=session_name, call_date=call_date, transcript_id=transcript_id,
            )
            annotate(
                indexed=indexed["indexed"],
                removed=indexed["removed"],
                error=indexed["error"] or None,
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


async def backfill_single_module(
    db: AsyncSession,
    customer_id: uuid.UUID,
    module: Any,
) -> dict[str, Any]:
    """
    Populate a newly approved module by scanning existing meeting minutes.
    Processes only the sections and action items that belong to THIS module,
    leaving all other existing module documents completely untouched.
    """
    from sqlalchemy import select
    from app.models.database import MeetingMinutes
    from app.services.mom_action_items import parse_action_items
    from app.services.module_reconciler import module_reconciler

    summary: dict[str, Any] = {
        "meetings_checked": 0,
        "meetings_matched": 0,
        "requirements_added": 0,
        "error": "",
    }

    query = (
        select(MeetingMinutes)
        .filter(
            MeetingMinutes.customer_id == customer_id,
            MeetingMinutes.content_markdown.isnot(None),
        )
        .order_by(MeetingMinutes.call_date)
    )
    rows = (await db.execute(query)).scalars().all()
    if not rows:
        return summary

    all_modules = await registry.list_modules(db, customer_id)
    classifier_block = registry.as_classifier_block(all_modules)

    for mom in rows:
        summary["meetings_checked"] += 1
        sections = parse_sections(mom.content_markdown)
        if not sections:
            continue

        classified = module_classifier.classify(sections, classifier_block)
        if classified.get("error"):
            logger.warning(f"Classification failed for {mom.session_name}: {classified['error']}")
            continue

        target_sections: list[dict[str, Any]] = []
        for assignment in classified.get("assignments", []):
            idx = assignment.get("index")
            mod_name = (assignment.get("module") or "").strip()
            if mod_name.lower() == module.module_name.lower() and isinstance(idx, int) and 0 <= idx < len(sections):
                target_sections.append(sections[idx])

        if not target_sections:
            continue

        summary["meetings_matched"] += 1
        call_date = mom.call_date
        all_action_items = parse_action_items(mom.content_markdown, call_date.date() if call_date else None)
        module_action_items = [
            item for item in all_action_items
            if _matches_module(item.get("area", ""), module.module_name, target_sections)
        ]

        incoming = [r for s in target_sections for r in (s.get("requirements") or [])]
        if not incoming and not module_action_items:
            continue

        existing = [b for b in parse_document_blocks(module.content_markdown or "")
                    if not b["deprecated"]]
        candidate_existing = select_candidate_blocks(existing, incoming, max_candidates=10)

        session_name = mom.session_name or ""
        date_str = call_date.strftime("%Y-%m-%d") if call_date else ""

        with trace_span(
            "module_auto_backfill",
            module=module.module_name,
            session_name=session_name,
            existing_requirements=len(existing),
            candidates_sent=len(candidate_existing),
            bullets_in=len(incoming),
        ):
            reconciled = module_reconciler.reconcile_blocks(
                module_name=module.module_name,
                module_description=module.description or "",
                existing=candidate_existing,
                incoming=incoming,
                session_name=session_name,
                call_date=date_str,
            )

        if reconciled.get("error"):
            logger.error(f"Reconciliation error for {module.module_name}: {reconciled['error']}")
            summary["error"] = reconciled["error"]
            continue

        written = apply_decisions(
            module.content_markdown,
            module.module_name,
            module.description or "",
            reconciled["decisions"],
            action_items=module_action_items,
            session_name=session_name,
        )

        if written.get("changed") and written.get("document"):
            revision = await store.save_revision(
                db, module,
                new_content=written["document"],
                revision_summary=reconciled.get("summary") or f"Backfilled from {session_name}",
                session_name=session_name,
                transcript_id=mom.transcript_id,
                line_diffs=written.get("diffs"),
            )
            indexed = await module_indexer.index_module(
                db, module,
                session_name=session_name,
                call_date=call_date,
                transcript_id=mom.transcript_id,
            )
            summary["requirements_added"] += len(written.get("diffs", []))

    await db.refresh(module)
    return summary


async def merge_proposed_into_module(
    db: AsyncSession,
    customer_id: uuid.UUID,
    proposed_module: ModuleDocument,
    target_module: ModuleDocument,
) -> dict[str, Any]:
    """
    Merge an unapproved proposed module into an existing active module.

    1. Scans past meeting minutes for any sections assigned to or matching the proposed module.
    2. Reconciles those incoming requirements into target_module using candidate pre-filtering.
    3. Saves a revision and updates the vector index on target_module.
    4. Removes the proposed module from module_documents so it is no longer pending.
    """
    summary: dict[str, Any] = {
        "proposed_module": proposed_module.module_name,
        "target_module": target_module.module_name,
        "meetings_matched": 0,
        "requirements_merged": 0,
        "error": None,
    }

    query = (
        select(MeetingMinutes)
        .filter(
            MeetingMinutes.customer_id == customer_id,
            MeetingMinutes.content_markdown.isnot(None),
        )
        .order_by(MeetingMinutes.call_date)
    )
    rows = (await db.execute(query)).scalars().all()

    all_modules = await registry.list_modules(db, customer_id, include_proposed=False)
    classifier_modules = list(all_modules)
    if not any(m.id == proposed_module.id for m in classifier_modules):
        classifier_modules.append(proposed_module)
    classifier_block = registry.as_classifier_block(classifier_modules)

    from app.services.mom_action_items import parse_action_items

    for mom in rows:
        sections = parse_sections(mom.content_markdown)
        if not sections:
            continue

        classified = module_classifier.classify(sections, classifier_block)
        if classified.get("error"):
            logger.warning(f"Classification failed for {mom.session_name}: {classified['error']}")
            continue

        target_sections: list[dict[str, Any]] = []
        for assignment in classified.get("assignments", []):
            idx = assignment.get("index")
            mod_name = (assignment.get("module") or "").strip()
            if mod_name.lower() == proposed_module.module_name.lower() and isinstance(idx, int) and 0 <= idx < len(sections):
                target_sections.append(sections[idx])

        if not target_sections:
            continue

        summary["meetings_matched"] += 1
        call_date = mom.call_date
        all_action_items = parse_action_items(mom.content_markdown, call_date.date() if call_date else None)
        module_action_items = [
            item for item in all_action_items
            if _matches_module(item.get("area", ""), proposed_module.module_name, target_sections) or
               _matches_module(item.get("area", ""), target_module.module_name, target_sections)
        ]

        incoming = [r for s in target_sections for r in (s.get("requirements") or [])]
        if not incoming and not module_action_items:
            continue

        existing = [b for b in parse_document_blocks(target_module.content_markdown or "")
                    if not b["deprecated"]]
        candidate_existing = select_candidate_blocks(existing, incoming, max_candidates=10)

        session_name = mom.session_name or ""
        date_str = call_date.strftime("%Y-%m-%d") if call_date else ""

        reconciled = module_reconciler.reconcile_blocks(
            module_name=target_module.module_name,
            module_description=target_module.description or "",
            existing=candidate_existing,
            incoming=incoming,
            session_name=session_name,
            call_date=date_str,
            model=settings.OPENAI_COMPARISON_MODEL,
        )

        if reconciled.get("error"):
            logger.error(f"Reconciliation error while merging into {target_module.module_name}: {reconciled['error']}")
            summary["error"] = reconciled["error"]
            continue

        written = apply_decisions(
            target_module.content_markdown,
            target_module.module_name,
            target_module.description or "",
            reconciled["decisions"],
            action_items=module_action_items,
            session_name=session_name,
        )

        if written.get("changed") and written.get("document"):
            revision = await store.save_revision(
                db, target_module,
                new_content=written["document"],
                revision_summary=reconciled.get("summary") or f"Merged '{proposed_module.module_name}' from {session_name}",
                session_name=session_name,
                transcript_id=mom.transcript_id,
                line_diffs=written.get("diffs"),
            )
            indexed = await module_indexer.index_module(
                db, target_module,
                session_name=session_name,
                call_date=call_date,
                transcript_id=mom.transcript_id,
            )
            summary["requirements_merged"] += len(written.get("diffs", []))

    # Delete the proposed module from database so it is no longer pending
    await db.delete(proposed_module)
    await db.commit()
    await db.refresh(target_module)

    logger.info(f"Successfully merged '{proposed_module.module_name}' into '{target_module.module_name}'.")
    return summary


