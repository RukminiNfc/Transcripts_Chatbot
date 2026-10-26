"""
Build module documents from minutes that already exist.

The pipeline normally runs automatically after minutes are generated. This runs it against
stored minutes instead, which is what you want for:

    testing          see the result without re-uploading a transcript
    backfilling      build documents from meetings already processed
    re-running       after fixing the taxonomy, a prompt, or approving a module

SAFE TO RE-RUN. Reconciliation reads each document as it currently stands, so a second run sees
its own previous output and should report few or no changes — if it reports many, the model is
rewriting rather than reconciling, which is worth knowing.

PROCESS OLDEST FIRST, which --all does. Documents accumulate, so applying May's meeting after
September's would record the earlier decision as the later one.

Usage (from Backend/):
    python run_module_docs.py --list
    python run_module_docs.py --session may8th_Grooming
    python run_module_docs.py --all
    python run_module_docs.py --all --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine          # noqa: E402
from app.core.observability import annotate, flush, trace_span   # noqa: E402
from app.models.database import MeetingMinutes                   # noqa: E402
from app.services import module_document_store as store          # noqa: E402
from app.services import module_registry as registry             # noqa: E402
from app.services.module_classifier import parse_sections        # noqa: E402
from app.services.module_pipeline import process_minutes         # noqa: E402


async def list_minutes() -> None:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(MeetingMinutes)
            .filter(MeetingMinutes.content_markdown.isnot(None))
            .order_by(MeetingMinutes.call_date)
        )).scalars().all()
        if not rows:
            print("No minutes with content found.")
            return
        print(f"{'SESSION':<26}{'DATE':<13}{'SECTIONS':>9}")
        for m in rows:
            n = len(parse_sections(m.content_markdown))
            date = m.call_date.strftime("%Y-%m-%d") if m.call_date else "-"
            print(f"{(m.session_name or '-')[:25]:<26}{date:<13}{n:>9}")
    await engine.dispose()


async def show_documents() -> None:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(MeetingMinutes).limit(1)
        )).scalars().first()
        if not rows:
            return
        docs = await store.document_summaries(db, rows.customer_id)
        print(f"\n{'MODULE':<40}{'VER':<7}{'REVS':>5}  CONTENT")
        for d in docs:
            print(f"{d['module_name'][:39]:<40}{d['current_version']:<7}"
                  f"{d['revision_count']:>5}  {'yes' if d['has_content'] else '-'}")
    await engine.dispose()


async def run(session: str | None, process_all: bool, dry_run: bool) -> None:
    async with AsyncSessionLocal() as db:
        query = select(MeetingMinutes).filter(MeetingMinutes.content_markdown.isnot(None))
        if session:
            query = query.filter(MeetingMinutes.session_name == session)
        # Oldest first: a module document accumulates, so order decides which decision is
        # recorded as the later one.
        rows = (await db.execute(query.order_by(MeetingMinutes.call_date))).scalars().all()

        if not rows:
            print("No matching minutes found. Try --list.")
            raise SystemExit(1)
        if not process_all and not session:
            rows = rows[-1:]   # most recent

        for mom in rows:
            sections = parse_sections(mom.content_markdown)
            print(f"\n{'=' * 66}")
            print(f"{mom.session_name}   {len(sections)} sections")
            print("=" * 66)

            if dry_run:
                mods = await registry.list_modules(db, mom.customer_id)
                print(f"  would classify {len(sections)} sections into {len(mods)} modules")
                for s in sections[:8]:
                    print(f"    - {s['heading'][:58]}")
                if len(sections) > 8:
                    print(f"    ... {len(sections) - 8} more")
                continue

            # Traced under the transcript's session like the Celery path, so a backfill run
            # shows up in `trace_cost_report.py --transcript <id>` alongside its upload.
            async with trace_span(
                "module_documents_backfill",
                session_id=str(mom.transcript_id) if mom.transcript_id else None,
                transcript_id=str(mom.transcript_id) if mom.transcript_id else None,
                session_name=mom.session_name,
            ):
                out = await process_minutes(
                    db=db,
                    customer_id=mom.customer_id,
                    minutes_markdown=mom.content_markdown,
                    session_name=mom.session_name or "",
                    call_date=mom.call_date,
                    transcript_id=mom.transcript_id,
                )
                annotate(
                    sections=out["sections"],
                    modules_touched=out["modules_touched"],
                    modules_updated=out["updated"],
                    modules_unchanged=out.get("unchanged", 0),
                    modules_failed=out["failed"],
                    module_changes=out["total_changes"],
                )
            flush()
            if out["error"]:
                print(f"  ERROR: {out['error']}")
                continue

            print(f"  modules touched {out['modules_touched']} | "
                  f"updated {out['updated']} | unchanged {out.get('unchanged', 0)} | "
                  f"failed {out['failed']} | changes {out['total_changes']}")
            for d in out["details"]:
                if d["status"] == "updated":
                    print(f"    {d['version']:<6}{d['module'][:34]:<36}"
                          f"{d['changes']} change(s), {d['indexed']} indexed")
                    if d.get("summary"):
                        print(f"           {d['summary'][:70]}")
                elif d["status"] == "unchanged":
                    # Expected, and the point of re-running being safe: the module already says
                    # what this meeting said, so no revision was recorded.
                    print(f"    {d['version']:<6}{d['module'][:34]:<36}unchanged")
                else:
                    print(f"    FAILED {d['module'][:34]} — {d.get('error','')[:40]}")
            if out["proposed"]:
                print(f"  PROPOSED (need approval): {', '.join(out['proposed'])}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="show minutes available to process")
    parser.add_argument("--session", help="one session name")
    parser.add_argument("--all", action="store_true", help="every meeting, oldest first")
    parser.add_argument("--dry-run", action="store_true", help="show what would be processed")
    parser.add_argument("--documents", action="store_true", help="show module document status")
    args = parser.parse_args()

    if args.list:
        asyncio.run(list_minutes())
    elif args.documents:
        asyncio.run(show_documents())
    else:
        asyncio.run(run(args.session, args.all, args.dry_run))


if __name__ == "__main__":
    main()
