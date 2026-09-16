"""
Generate a Minutes of Meeting document for an ALREADY-PROCESSED transcript.

READ-ONLY by default. It reads conversation_logs and writes NOTHING back — no rows, no changes
to requirements or transcripts. The only side effects are an OpenAI call (which costs credits)
and, with --out, a file on disk.

Pass --save to ALSO store the result as a new meeting_minutes version. That path calls exactly
the same function the API endpoint and the Celery task use, so there is one code path, not two.
Sending email is never done here — that is a deliberate manual step in the admin UI.

This exists so the MOM prompt can be tuned against real calls without going near the upload
pipeline.

Usage (run from the Backend/ directory):

    # see what's available
    ../venv/Scripts/python.exe generate_mom.py --list

    # generate and print, writing nothing to the database
    ../venv/Scripts/python.exe generate_mom.py --session 08-20-26_Grooming --out mom.md

    # generate AND store it as a new version
    ../venv/Scripts/python.exe generate_mom.py --session 08-20-26_Grooming --save

Linux: use ../venv/bin/python instead. Windows and Linux both work.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid

# Make the `app` package importable when run from the Backend/ directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.future import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.database import ConversationLog, Customer, Transcript  # noqa: E402
from app.services.mom_generation import (  # noqa: E402
    _ts_to_seconds,
    generate_and_store_mom,
    mom_service,
)

# Quieter logs: keep our INFO lines, silence SQLAlchemy's verbose echo.
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logger = logging.getLogger("generate_mom")


async def _list_transcripts() -> None:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Transcript).order_by(Transcript.call_date)
        )).scalars().all()
        if not rows:
            print("No transcripts found.")
            return
        print(f"{len(rows)} transcript(s):\n")
        print(f"  {'SESSION':<26} {'DATE':<12} {'BLOCKS':>7}  {'STATUS':<11} ID")
        for t in rows:
            print(
                f"  {str(t.session_name or ''):<26} {str(t.call_date)[:10]:<12} "
                f"{(t.total_blocks or 0):>7}  {str(t.status or ''):<11} {t.id}"
            )
    await engine.dispose()


async def _generate(
    transcript_id: str | None,
    session: str | None,
    out_path: str | None,
    save: bool = False,
) -> None:
    async with AsyncSessionLocal() as db:
        # 1. Find the transcript, by id or by session name.
        query = select(Transcript)
        if transcript_id:
            try:
                query = query.filter(Transcript.id == uuid.UUID(transcript_id))
            except ValueError:
                raise SystemExit(f"'{transcript_id}' is not a valid UUID. Use --list to see ids.")
        else:
            query = query.filter(Transcript.session_name == session)

        transcript = (await db.execute(query)).scalars().first()
        if not transcript:
            raise SystemExit(
                f"No transcript matching {'id ' + transcript_id if transcript_id else 'session ' + str(session)}. "
                f"Run with --list to see what exists."
            )

        # 2. Who is the client? The prompt needs the name to tell requirements from status reports.
        customer = (await db.execute(
            select(Customer).filter(Customer.id == transcript.customer_id)
        )).scalars().first()
        client_speaker_name = customer.client_speaker_name if customer else ""
        if not client_speaker_name:
            logger.warning(
                "No client_speaker_name on the customer record — the prompt cannot distinguish "
                "client requirements from developer status reports. Minutes will be weaker."
            )

        # 3. Rebuild the blocks from conversation_logs, in spoken order.
        logs = (await db.execute(
            select(ConversationLog).filter(ConversationLog.transcript_id == transcript.id)
        )).scalars().all()
        if not logs:
            raise SystemExit(
                f"Transcript '{transcript.session_name}' has no conversation_logs rows. "
                f"It may have failed processing (status={transcript.status})."
            )

        ordered = sorted(logs, key=lambda r: _ts_to_seconds(r.call_timestamp))
        blocks = [
            {"speaker": r.speaker, "timestamp": r.call_timestamp, "text": r.text, "role": r.role}
            for r in ordered
        ]

        logger.info(
            f"Generating MOM for '{transcript.session_name}' "
            f"({len(blocks)} blocks, client='{client_speaker_name}', save={save})..."
        )

        session_name_out = transcript.session_name

        if save:
            # Same path the API endpoint and the Celery task use — one stored version, no
            # duplicate logic.
            mom = await generate_and_store_mom(db=db, transcript_id=transcript.id)
            if mom is None:
                raise SystemExit("Generation produced nothing; see the log above.")
            if mom.generation_error:
                raise SystemExit(f"MOM generation failed: {mom.generation_error}")
            content = mom.content_markdown or ""
            model_used = mom.model_used
            output_truncated = bool(mom.truncated)
            input_truncated = False
            logger.info(f"Stored as version {mom.version} (id={mom.id}, status={mom.status})")
        else:
            # Print-only: generate without touching the database at all.
            result = await asyncio.to_thread(
                mom_service.generate_mom,
                blocks=blocks,
                client_speaker_name=client_speaker_name,
                session_name=transcript.session_name,
                call_date=transcript.call_date,
            )
            if result["error"]:
                raise SystemExit(f"MOM generation failed: {result['error']}")
            content = result["content"]
            model_used = result["model_used"]
            output_truncated = result["output_truncated"]
            input_truncated = result["input_truncated"]

    await engine.dispose()

    # Report honestly before printing anything.
    if input_truncated:
        logger.warning("INPUT was truncated — the transcript exceeded the character budget.")
    if output_truncated:
        logger.warning(
            "OUTPUT was truncated — the model hit its token cap and this document is INCOMPLETE. "
            "Treat the tail as missing, not as the end of the meeting."
        )

    logger.info(f"Generated {len(content)} chars for '{session_name_out}' using {model_used}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        logger.info(f"Written to {out_path}")
    else:
        print()
        print(content)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a MOM for a processed transcript. Prints it; only --save writes to the DB."
    )
    parser.add_argument("--list", action="store_true", help="List available transcripts and exit.")
    parser.add_argument("--transcript-id", help="Transcript UUID.")
    parser.add_argument("--session", help="Session name, e.g. 08-20-26_Grooming.")
    parser.add_argument("--out", help="Write the markdown to this file instead of stdout.")
    parser.add_argument(
        "--save", action="store_true",
        help="Also store it as a new meeting_minutes version (default: print only, no DB write).",
    )
    args = parser.parse_args()

    if args.list:
        asyncio.run(_list_transcripts())
        return

    if not args.transcript_id and not args.session:
        parser.error("Provide --transcript-id UUID or --session NAME (or --list to see them).")

    asyncio.run(_generate(args.transcript_id, args.session, args.out, args.save))


if __name__ == "__main__":
    main()
