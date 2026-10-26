"""
Undo the module-document revisions one meeting created, restoring each module to the version
before them.

WHY THIS EXISTS
---------------
Re-running a meeting through the module pipeline (run_module_docs.py) is the test that shows
whether reconciliation recognises what the documents already say. If it does not, the re-run
adds near-duplicate bullets as new revisions. Every revision stores a complete snapshot of the
document, so the state before is never lost — but until this script there was no way back short
of hand-written SQL.

WHAT IT DOES, PER MODULE
------------------------
For each module that has revisions matching --session (and created after --since, if given):

    1. Those revisions must be the NEWEST ones for that module, with nothing from another
       meeting above them. If a later meeting has since revised the module, it is SKIPPED and
       reported — rolling it back would also throw away the later meeting's changes.
    2. The module's text and version go back to the revision just below the removed ones.
       If nothing is left (the meeting created the document's v1.0), the document is emptied
       and the version reset to v1.0, exactly as it was before any meeting.
    3. The removed revision rows are deleted.
    4. The module is re-indexed from the restored text, so the requirements table and Qdrant
       match the document again. Empty document -> index cleared.

Transcripts, conversation logs and minutes are never touched.

SAFETY
------
Dry run by default. Nothing is written without --apply.

    python rollback_module_revisions.py --list
        every revision, newest first, with time, session and size — to pick --since from
    python rollback_module_revisions.py --session may21th_grooming --since "2026-10-21 10:00"
        show what would be undone
    python rollback_module_revisions.py --session may21th_grooming --since "2026-10-21 10:00" --apply
        do it

--since is compared against the revision's created_at in LOCAL time and is strongly recommended:
the same session name is used by the original upload and by every re-run, and --since is what
tells them apart. Without it, EVERY revision of that session is undone, including the original.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine                              # noqa: E402
from app.models.database import ModuleDocument, ModuleDocumentRevision, Transcript  # noqa: E402


def _local(ts: Optional[datetime]) -> str:
    if not ts:
        return "-"
    return ts.astimezone().strftime("%Y-%m-%d %H:%M:%S")


async def list_revisions() -> None:
    async with AsyncSessionLocal() as db:
        modules = {m.id: m for m in (await db.execute(select(ModuleDocument))).scalars().all()}
        rows = (await db.execute(
            select(ModuleDocumentRevision).order_by(ModuleDocumentRevision.created_at.desc())
        )).scalars().all()
        if not rows:
            print("No revisions stored.")
            return
        print(f"{'CREATED (local)':<21}{'MODULE':<36}{'VER':<7}{'SESSION':<24}{'CHANGES':>8}{'CHARS':>8}")
        print("-" * 104)
        for r in rows:
            m = modules.get(r.module_document_id)
            print(f"{_local(r.created_at):<21}{(m.module_name if m else '?')[:35]:<36}"
                  f"{r.version_label:<7}{(r.session_name or '-')[:23]:<24}"
                  f"{len(r.line_diffs or []):>8}{len(r.content_snapshot or ''):>8}")
    await engine.dispose()


async def run(session: str, since: Optional[datetime], apply: bool) -> None:
    # Imported here: it builds an OpenAI client on construction, and --list should not need a key.
    from app.services.module_indexer import module_indexer

    undone = skipped = 0

    async with AsyncSessionLocal() as db:
        modules = (await db.execute(
            select(ModuleDocument).order_by(ModuleDocument.module_name)
        )).scalars().all()

        for module in modules:
            revisions = (await db.execute(
                select(ModuleDocumentRevision)
                .filter(ModuleDocumentRevision.module_document_id == module.id)
                .order_by(ModuleDocumentRevision.created_at)
            )).scalars().all()
            if not revisions:
                continue

            def targeted(r: ModuleDocumentRevision) -> bool:
                if (r.session_name or "").strip().lower() != session.strip().lower():
                    return False
                if since is not None and r.created_at.astimezone() < since.astimezone():
                    return False
                return True

            # The removable set must be a contiguous tail: everything above the first targeted
            # revision has to be targeted too, or a later meeting's work would be lost.
            first = next((i for i, r in enumerate(revisions) if targeted(r)), None)
            if first is None:
                continue
            tail = revisions[first:]
            foreign = [r for r in tail if not targeted(r)]
            remove = [r for r in tail if targeted(r)]
            restore_to = revisions[first - 1] if first > 0 else None

            print(f"\n{module.module_name}   (now {module.current_version}, "
                  f"{len(module.content_markdown or ''):,} chars)")
            if foreign:
                skipped += 1
                print("   SKIPPED — later revisions from another meeting sit above the target:")
                for r in foreign:
                    print(f"      {r.version_label:<7}{_local(r.created_at)}  {r.session_name or '-'}")
                continue

            for r in remove:
                print(f"   remove   {r.version_label:<7}{_local(r.created_at)}  "
                      f"{len(r.line_diffs or []):>4} change(s)  {len(r.content_snapshot or ''):>6,} chars")
            if restore_to is not None:
                print(f"   restore  {restore_to.version_label:<7}{_local(restore_to.created_at)}  "
                      f"from {restore_to.session_name or '-'}  "
                      f"{len(restore_to.content_snapshot or ''):>6,} chars")
            else:
                print("   restore  (empty document, v1.0 — this meeting created it)")
            undone += 1

            if not apply:
                continue

            for r in remove:
                await db.delete(r)
            if restore_to is not None:
                module.content_markdown = restore_to.content_snapshot
                module.current_version = restore_to.version_label
            else:
                module.content_markdown = None
                module.current_version = "v1.0"
            module.last_synced_at = restore_to.created_at if restore_to is not None else None
            await db.commit()

            # Rebuild the index by replaying the revisions that remain. Re-indexing only the
            # restored text would record the undo as yet another "modified" version of each
            # requirement; replaying makes the per-requirement history read as if the removed
            # meeting had never been processed.
            rebuilt = await module_indexer.rebuild_module_history(db, module)
            status = (f"{rebuilt['indexed']} requirements, history rebuilt from "
                      f"{rebuilt['revisions_replayed']} revision(s)"
                      if not rebuilt["error"] else f"INDEX ERROR: {rebuilt['error']}")
            print(f"   done     {module.current_version}, {status}")

    await engine.dispose()

    print(f"\n{'APPLIED' if apply else 'DRY RUN'}: {undone} module(s) rolled back, {skipped} skipped.")
    if not apply and undone:
        print("Re-run with --apply to make these changes.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="show every revision, newest first")
    parser.add_argument("--session", help="session name whose revisions to undo")
    parser.add_argument("--since", help='only revisions created at/after this local time, e.g. "2026-10-21 10:00"')
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()

    if args.list:
        asyncio.run(list_revisions())
        return
    if not args.session:
        parser.error("--session is required (or use --list)")
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).astimezone()
    asyncio.run(run(args.session, since, args.apply))


if __name__ == "__main__":
    main()
