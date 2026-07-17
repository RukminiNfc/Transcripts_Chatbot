"""
READ-ONLY data checker. Makes NO changes.

Run after each upload to verify the ingest is healthy. Two key checks:
  (a) NO DUPLICATES  — each requirement should have exactly ONE vector in the search
      store (Qdrant 'requirements'). This is what the supersede fix guarantees.
  (b) HISTORY KEPT   — every requirement's full version history is listed from Postgres
      (RequirementVersion), oldest -> newest, with the change_type of each version.

Works the same on local and server — it reads the connection from the .env on THIS machine.

Usage:
    python check_data.py            # summary + duplicate check + per-requirement history
    python check_data.py --brief    # summary + duplicate check only (no per-req history)
"""
import asyncio
import sys
from urllib.parse import urlparse

from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.qdrant_client import QdrantVectorDB
from app.models.database import (
    Customer, Transcript, Requirement, RequirementVersion, ConversationLog,
)

BRIEF = "--brief" in sys.argv


def _host(url: str) -> str:
    """Show host:port/db of a DB URL without leaking the password."""
    try:
        p = urlparse(url)
        return f"{p.hostname}:{p.port}{p.path}"
    except Exception:
        return "(unparbable url)"


def _scroll_requirement_vectors(qdrant: QdrantVectorDB):
    """Return {requirement_id: vector_count} for the 'requirements' collection."""
    counts = {}
    offset = None
    while True:
        points, offset = qdrant.client.scroll(
            collection_name=qdrant.requirements_collection,
            limit=500,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        for pt in points:
            rid = (pt.payload or {}).get("requirement_id", "(none)")
            counts[rid] = counts.get(rid, 0) + 1
        if offset is None:
            break
    return counts


async def main():
    qdrant = QdrantVectorDB()

    print("=" * 70)
    print("DATA CHECK  (read-only — nothing is modified)")
    print("=" * 70)
    print(f"Postgres : {_host(settings.DATABASE_URL)}")
    print(f"Qdrant   : {settings.QDRANT_HOST}:{settings.QDRANT_PORT}")
    print("-" * 70)

    async with AsyncSessionLocal() as db:
        # ---- Postgres counts ----
        n_customers = (await db.execute(select(func.count()).select_from(Customer))).scalar()
        n_transcripts = (await db.execute(select(func.count()).select_from(Transcript))).scalar()
        n_reqs = (await db.execute(select(func.count()).select_from(Requirement))).scalar()
        n_versions = (await db.execute(select(func.count()).select_from(RequirementVersion))).scalar()
        n_convo = (await db.execute(select(func.count()).select_from(ConversationLog))).scalar()

        print("POSTGRES (the record book — keeps full history):")
        print(f"  customers            : {n_customers}")
        print(f"  transcripts          : {n_transcripts}")
        print(f"  requirements (current): {n_reqs}")
        print(f"  requirement_versions : {n_versions}")
        print(f"  conversation_logs    : {n_convo}")

        # Transcripts in date order (so you can see the upload order)
        trows = (await db.execute(
            select(Transcript.session_name, Transcript.call_date, Transcript.status,
                   Transcript.processing_summary)
            .order_by(Transcript.call_date)
        )).all()
        if trows:
            print("\n  Transcripts (oldest -> newest):")
            for s, d, st, summ in trows:
                ds = d.date().isoformat() if d else "?"
                print(f"    - {ds}  {s}  [{st}]  {summ or ''}")

        # ---- Qdrant: duplicate check (a) ----
        conv_pts = qdrant.client.count(qdrant.conversations_collection, exact=True).count
        vec_counts = _scroll_requirement_vectors(qdrant)
        total_vecs = sum(vec_counts.values())
        distinct_reqs = len(vec_counts)
        dupes = {rid: c for rid, c in vec_counts.items() if c > 1}

        print("\nQDRANT (the search copy — should be ONE vector per requirement):")
        print(f"  conversations vectors        : {conv_pts}")
        print(f"  requirements vectors (total) : {total_vecs}")
        print(f"  distinct requirements        : {distinct_reqs}")

        print("\nCHECK (a) NO DUPLICATES in the search copy:")
        if not dupes:
            print("  PASS — every requirement has exactly ONE vector. (supersede working)")
        else:
            print(f"  FAIL — {len(dupes)} requirement(s) have duplicate vectors:")
            for rid, c in list(dupes.items())[:20]:
                print(f"    requirement_id {rid} -> {c} vectors")

        # Cross-check: Qdrant distinct requirements vs Postgres current requirements
        if distinct_reqs != n_reqs:
            print(f"  NOTE — Qdrant distinct requirements ({distinct_reqs}) != "
                  f"Postgres requirements ({n_reqs}). Expected equal after a clean run.")

        # ---- Per-requirement history (b) ----
        if not BRIEF:
            print("\nCHECK (b) HISTORY KEPT (from Postgres — every version is preserved):")
            reqs = (await db.execute(
                select(Requirement).order_by(Requirement.created_at)
            )).scalars().all()
            for r in reqs:
                versions = (await db.execute(
                    select(RequirementVersion)
                    .where(RequirementVersion.requirement_id == r.id)
                    .order_by(RequirementVersion.version_number)
                )).scalars().all()
                cur = (r.current_text or "")[:90]
                print(f"\n  [{r.category}/{r.sub_category}]  ({len(versions)} version(s))")
                print(f"    CURRENT: {cur}")
                for v in versions:
                    ds = v.discussed_date.date().isoformat() if v.discussed_date else "?"
                    txt = (v.text or "")[:80]
                    print(f"    v{v.version_number} [{v.change_type}] {ds} {v.session}: {txt}")

    await engine.dispose()
    print("\n" + "=" * 70)
    print("Done. (no changes were made)")


if __name__ == "__main__":
    asyncio.run(main())
