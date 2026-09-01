"""
Bulk backfill of historical grooming-call transcripts.

Loads many .docx transcripts from one or more folders and runs each through the FULL
pipeline (parse -> extract -> compare) in strict chronological order, ONE AT A TIME,
with the change-notification email turned OFF.

Why sequential + date-ordered:
    Change detection compares each transcript against the requirements that already
    exist. Processing oldest -> newest, one finishing before the next starts, rebuilds
    the real day-by-day requirement history. Running them in parallel would compare
    everything against an empty baseline and produce duplicates and wrong history.

Resumable:
    A file is skipped if a transcript with the same content hash already exists, so a
    re-run continues where it left off. (If a previous run left a transcript in a
    'failed' state, delete that transcript from the dashboard before re-running it.)

Usage (run from the Backend/ directory):
    python backfill_transcripts.py "PATH\\TO\\FOLDER1" "PATH\\TO\\FOLDER2" --project tempositions

    # or target by customer id instead of name:
    python backfill_transcripts.py "PATH\\TO\\FOLDER" --customer-id 8190613c-...

Windows and Linux both work — only the folder paths differ.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import logging
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

# Make the `app` package importable when run from the Backend/ directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import func  # noqa: E402
from sqlalchemy.future import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.core.qdrant_client import QdrantVectorDB  # noqa: E402
from app.models.database import (  # noqa: E402
    ConversationLog,
    Customer,
    Requirement,
    RequirementVersion,
    Transcript,
)
from app.worker.tasks import _process_transcript_async  # noqa: E402

# Quieter logs: keep our INFO lines, silence SQLAlchemy's verbose echo.
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logger = logging.getLogger("backfill")

# Matches a leading date like "06-01-26" (MM-DD-YY) at the start of a filename.
_DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})")

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "uploads")


def parse_call_date(filename: str) -> datetime:
    """Parse the MM-DD-YY date at the start of the filename. Falls back to file mtime."""
    base = os.path.basename(filename)
    match = _DATE_RE.match(base)
    if match:
        mm, dd, yy = (int(g) for g in match.groups())
        try:
            # Noon UTC: keeps the calendar date stable across timezones (midnight slips a day in IST).
            return datetime(2000 + yy, mm, dd, 12, 0, 0, tzinfo=timezone.utc)
        except ValueError:
            logger.warning(f"Invalid date in filename '{base}'; using file modified time.")
    else:
        logger.warning(f"No MM-DD-YY date in filename '{base}'; using file modified time.")
    return datetime.fromtimestamp(os.path.getmtime(filename))


def collect_files(folders: list[str]) -> list[tuple[datetime, str]]:
    """Gather every .docx across the folders, return [(call_date, full_path)] sorted oldest->newest."""
    found: list[tuple[datetime, str]] = []
    for folder in folders:
        folder = folder.strip().strip('"')
        if not os.path.isdir(folder):
            logger.error(f"Folder not found, skipping: {folder}")
            continue
        for path in glob.glob(os.path.join(folder, "*.docx")):
            if os.path.basename(path).startswith("~$"):  # skip Word lock/temp files
                continue
            found.append((parse_call_date(path), path))
    # Sort by (date, filename) so same-day files keep a stable order (_1 before _2).
    found.sort(key=lambda t: (t[0], os.path.basename(t[1]).lower()))
    return found


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def resolve_customer(project: Optional[str], customer_id: Optional[str]) -> Customer:
    async with AsyncSessionLocal() as db:
        if customer_id:
            cust = (await db.execute(select(Customer).filter(Customer.id == uuid.UUID(customer_id)))).scalars().first()
            if not cust:
                raise SystemExit(f"No customer with id {customer_id}")
            return cust
        result = await db.execute(select(Customer).filter(Customer.name.ilike(project)))
        cust = result.scalars().first()
        if not cust:
            raise SystemExit(f"No customer named '{project}'. Create the project first or pass --customer-id.")
        return cust


async def find_existing(file_hash: str) -> tuple[Optional[str], Optional[str]]:
    """Return (transcript_id, status) of an existing transcript with this hash, or (None, None)."""
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(Transcript).filter(Transcript.file_hash == file_hash))
        ).scalars().first()
        return (str(existing.id), existing.status) if existing else (None, None)


async def delete_stale_transcript(transcript_id_str: str) -> None:
    """
    Cascade-delete an incomplete (failed/processing) transcript so it can be reprocessed:
    removes its Qdrant vectors, conversation logs, requirement versions, any now-orphaned
    requirements, and the transcript record itself.
    """
    tid = uuid.UUID(transcript_id_str)
    qdrant = QdrantVectorDB()
    async with AsyncSessionLocal() as db:
        versions = (
            await db.execute(select(RequirementVersion).filter(RequirementVersion.transcript_id == tid))
        ).scalars().all()
        vector_ids = [v.vector_id for v in versions if v.vector_id]
        affected_req_ids = list({v.requirement_id for v in versions})

        qdrant.delete_by_filter(qdrant.conversations_collection, {"transcript_id": transcript_id_str})
        if vector_ids:
            qdrant.delete_by_ids(qdrant.requirements_collection, vector_ids)

        for log in (
            await db.execute(select(ConversationLog).filter(ConversationLog.transcript_id == tid))
        ).scalars().all():
            await db.delete(log)
        for v in versions:
            await db.delete(v)
        await db.flush()

        for req_id in affected_req_ids:
            remaining = (await db.execute(
                select(func.count()).select_from(RequirementVersion)
                .filter(RequirementVersion.requirement_id == req_id)
            )).scalar() or 0
            if remaining == 0:
                orphan = (await db.execute(select(Requirement).filter(Requirement.id == req_id))).scalars().first()
                if orphan:
                    await db.delete(orphan)

        stale = (await db.execute(select(Transcript).filter(Transcript.id == tid))).scalars().first()
        if stale:
            await db.delete(stale)
        await db.commit()


async def create_transcript_record(customer_id: uuid.UUID, filename: str,
                                    session_name: str, call_date: datetime, file_hash: str) -> uuid.UUID:
    transcript_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(Transcript(
            id=transcript_id,
            customer_id=customer_id,
            filename=filename,
            session_name=session_name,
            call_date=call_date,
            status="processing",
            file_hash=file_hash,
            total_blocks=0,
        ))
        await db.commit()
    return transcript_id


async def fetch_summary(transcript_id: uuid.UUID) -> tuple[str, dict]:
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Transcript).filter(Transcript.id == transcript_id))).scalars().first()
        return (t.status, t.processing_summary or {}) if t else ("unknown", {})


async def run(folders: list[str], project: Optional[str], customer_id: Optional[str]) -> None:
    customer = await resolve_customer(project, customer_id)
    logger.info(f"Backfilling for project '{customer.name}' (client speaker: {customer.client_speaker_name})")

    files = collect_files(folders)
    if not files:
        raise SystemExit("No .docx files found in the given folders.")

    logger.info(f"Found {len(files)} transcript(s). Processing oldest -> newest, one at a time.\n")
    os.makedirs(UPLOADS_DIR, exist_ok=True)

    processed = skipped = failed = 0

    for i, (call_date, src_path) in enumerate(files, start=1):
        name = os.path.basename(src_path)
        tag = f"[{i}/{len(files)}] {call_date.date()}  {name}"

        try:
            file_hash = file_sha256(src_path)

            existing_id, prior = await find_existing(file_hash)
            if prior == "processed":
                skipped += 1
                logger.info(f"{tag}  ->  SKIP (already processed)")
                continue
            if existing_id and prior in ("failed", "processing"):
                # Incomplete from a previous run — clean it up and reprocess.
                logger.info(f"{tag}  ->  cleaning stale '{prior}' record, will reprocess")
                await delete_stale_transcript(existing_id)

            transcript_id = await create_transcript_record(
                customer_id=customer.id,
                filename=name,
                session_name=os.path.splitext(name)[0],
                call_date=call_date,
                file_hash=file_hash,
            )

            # Copy to the uploads dir; the pipeline deletes THIS copy at the end,
            # leaving the original source file untouched.
            temp_path = os.path.join(UPLOADS_DIR, f"{transcript_id}.docx")
            shutil.copyfile(src_path, temp_path)

            logger.info(f"{tag}  ->  processing (gpt-5.5)...")
            await _process_transcript_async(
                transcript_id_str=str(transcript_id),
                file_path=temp_path,
                customer_id_str=str(customer.id),
                session_name=os.path.splitext(name)[0],
                call_date_str=call_date.isoformat(),
                send_email=False,
            )

            status, summary = await fetch_summary(transcript_id)
            if status == "processed":
                processed += 1
                logger.info(
                    f"{tag}  ->  DONE  "
                    f"(extracted={summary.get('total_extracted', '?')}, "
                    f"added={summary.get('added', '?')}, modified={summary.get('modified', '?')})\n"
                )
            else:
                failed += 1
                logger.error(f"{tag}  ->  status='{status}' (treated as failed)\n")

        except Exception as exc:
            failed += 1
            logger.error(f"{tag}  ->  FAILED: {exc}\n")
            # Continue with the next file; this one can be retried on a later run.

    logger.info("=" * 60)
    logger.info(f"Backfill complete: {processed} processed, {skipped} skipped, {failed} failed, "
                f"{len(files)} total.")
    if failed:
        logger.info("Re-run the SAME command to retry failed files — already-processed ones "
                    "are skipped, and failed/incomplete ones are cleaned up and reprocessed automatically.")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk backfill historical transcripts (no emails).")
    parser.add_argument("folders", nargs="+", help="One or more folders containing .docx transcripts.")
    parser.add_argument("--project", help="Project/customer name (e.g. tempositions).")
    parser.add_argument("--customer-id", help="Customer UUID (alternative to --project).")
    args = parser.parse_args()

    if not args.project and not args.customer_id:
        parser.error("Provide --project NAME or --customer-id UUID.")

    asyncio.run(run(args.folders, args.project, args.customer_id))


if __name__ == "__main__":
    main()
