"""
Remove module-document revisions that recorded no change, and renumber what is left.

WHY THEY EXIST
--------------
Until the sub-section splice landed, the pipeline saved a revision for every module a meeting
touched — including the ones where the meeting only restated what the document already said. The
result is a version history whose newest entry reads "0 changes", and a document whose version
number counts events that never happened.

The pipeline no longer creates them. This clears the ones already stored.

WHAT IT DELETES — DELIBERATELY NARROW
-------------------------------------
A revision is removed only when BOTH are true:

    it recorded zero changes, AND
    its document is byte-identical to the revision before it

The second condition is the safety: a revision with no diffs but different text means something
changed outside the reconciler's account of it, and that is exactly the revision worth keeping.
The FIRST revision of a document is never removed — it is the document's origin, and a v1.0 with
no predecessor to compare against is not evidence of nothing happening.

RENUMBERING
-----------
Survivors are renumbered v1.0, v1.1, v1.2 … in date order, and the module's current_version is
set to the last of them. Gaps would be harmless to read but not to compare: the chat "changes"
lane finds a previous version by subtracting one, which assumes a contiguous chain.

SAFETY
------
Dry run by default. Nothing is written without --apply.

    python purge_empty_module_revisions.py            # show what would change
    python purge_empty_module_revisions.py --apply    # do it
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine          # noqa: E402
from app.models.database import ModuleDocument, ModuleDocumentRevision  # noqa: E402
from app.services.module_document_store import next_version      # noqa: E402


def _renumber(count: int) -> list[str]:
    labels, current = [], "v1.0"
    for i in range(count):
        labels.append(current if i == 0 else next_version(current))
        current = labels[-1]
    return labels


async def run(apply: bool) -> None:
    removed_total = relabelled_total = 0

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

            keep: list[ModuleDocumentRevision] = []
            drop: list[ModuleDocumentRevision] = []

            for index, revision in enumerate(revisions):
                no_changes = not (revision.line_diffs or [])
                same_text = (
                    index > 0
                    and (revision.content_snapshot or "")
                    == (revisions[index - 1].content_snapshot or "")
                )
                (drop if (no_changes and same_text) else keep).append(revision)

            labels = _renumber(len(keep))
            relabelled = [
                (r, new) for r, new in zip(keep, labels) if (r.version_label or "") != new
            ]

            if not drop and not relabelled:
                continue

            print(f"\n{module.module_name}")
            for revision in drop:
                print(f"   remove  {revision.version_label:<7}"
                      f"{(revision.session_name or '-').strip()[:22]:<24}0 changes, identical text")
            for revision, new in relabelled:
                print(f"   relabel {revision.version_label:<7}->  {new}")

            final = labels[-1] if labels else "v1.0"
            if (module.current_version or "") != final:
                print(f"   document version {module.current_version} -> {final}")

            removed_total += len(drop)
            relabelled_total += len(relabelled)

            if apply:
                for revision in drop:
                    await db.delete(revision)
                for revision, new in relabelled:
                    revision.version_label = new
                module.current_version = final
                await db.commit()

    await engine.dispose()

    print(f"\n{'APPLIED' if apply else 'DRY RUN'}: "
          f"{removed_total} empty revision(s) removed, {relabelled_total} relabelled.")
    if not apply and (removed_total or relabelled_total):
        print("Re-run with --apply to make these changes.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    asyncio.run(run(parser.parse_args().apply))


if __name__ == "__main__":
    main()
