"""
Purge ORPHANED vectors from Qdrant — vectors whose backing row no longer exists in Postgres
(left behind when transcripts were deleted without cleaning the vector store).

Source of truth = Postgres:
  - conversations vector is valid IFF its payload 'transcript_id' is in the transcripts table.
  - requirements  vector is valid IFF its payload 'version_id'   is in the requirement_versions table.

SAFE BY DEFAULT: dry-run — prints what WOULD be deleted and deletes nothing.
Run with --apply to actually delete. Nothing is hardcoded; validity is read live from Postgres.
When applying, it also backfills requirement_versions.vector_id (= point id) so that FUTURE
transcript deletes correctly remove requirement vectors from Qdrant.

Usage (from Backend/):
    python purge_orphan_vectors.py            # dry-run
    python purge_orphan_vectors.py --apply     # delete + fix vector_ids
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qdrant_client import QdrantClient  # noqa: E402
from sqlalchemy.future import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.database import Transcript, RequirementVersion  # noqa: E402

PAGE = 256
DELETE_BATCH = 500

# (collection, payload key that must exist in Postgres)
PLAN = [
    ("conversations", "transcript_id"),
    ("requirements", "version_id"),
]


async def load_valid_ids():
    async with AsyncSessionLocal() as db:
        tids = {str(x) for x in (await db.execute(select(Transcript.id))).scalars().all()}
        vids = {str(x) for x in (await db.execute(select(RequirementVersion.id))).scalars().all()}
    return tids, vids


def scan(client: QdrantClient, collection: str, key: str, valid: set):
    """Return (ghost_ids, ghost_by_date, valid_count, unknown_count)."""
    ghost_ids, ghost_by_date = [], defaultdict(int)
    valid_count = unknown_count = 0
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, limit=PAGE, offset=offset,
            with_payload=True, with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            ref = payload.get(key)
            if ref is None:
                unknown_count += 1          # missing key -> DON'T delete (conservative)
            elif str(ref) in valid:
                valid_count += 1
            else:
                ghost_ids.append(p.id)
                ghost_by_date[payload.get("date_ymd", "?")] += 1
        if offset is None:
            break
    return ghost_ids, ghost_by_date, valid_count, unknown_count


def report(collection, ghost_ids, ghost_by_date, valid_count, unknown_count):
    print(f"\n=== {collection} ===")
    print(f"  valid (kept):        {valid_count}")
    print(f"  orphaned (ghosts):   {len(ghost_ids)}")
    if ghost_by_date:
        for d, n in sorted(ghost_by_date.items()):
            print(f"       - {d}: {n}")
    if unknown_count:
        print(f"  no-id (left alone):  {unknown_count}")


async def backfill_vector_ids():
    """Point id for a requirement vector == str(version.id); populate vector_id where NULL."""
    fixed = 0
    async with AsyncSessionLocal() as db:
        versions = (await db.execute(
            select(RequirementVersion).where(RequirementVersion.vector_id.is_(None))
        )).scalars().all()
        for v in versions:
            v.vector_id = str(v.id)
            fixed += 1
        await db.commit()
    print(f"\nBackfilled vector_id on {fixed} requirement_versions (future deletes will clean Qdrant).")


async def run(apply: bool):
    """All async DB work + Qdrant work in ONE event loop, disposing before it closes
    (avoids the Windows asyncpg 'NoneType has no send' teardown error across loops)."""
    print(f"{'APPLYING' if apply else 'DRY-RUN'} orphan purge on {settings.QDRANT_HOST}:{settings.QDRANT_PORT}")

    tids, vids = await load_valid_ids()
    print(f"Valid transcript_ids={len(tids)}  valid version_ids={len(vids)}")

    client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    valid_map = {"transcript_id": tids, "version_id": vids}

    for collection, key in PLAN:
        try:
            ghost_ids, ghost_by_date, valid_count, unknown_count = scan(client, collection, key, valid_map[key])
            report(collection, ghost_ids, ghost_by_date, valid_count, unknown_count)
            if apply and ghost_ids:
                for i in range(0, len(ghost_ids), DELETE_BATCH):
                    client.delete(collection_name=collection, points_selector=ghost_ids[i:i + DELETE_BATCH])
                print(f"  DELETED {len(ghost_ids)} orphaned vectors from {collection}.")
        except Exception as e:
            print(f"  ERROR on '{collection}': {e}")

    if apply:
        await backfill_vector_ids()
    else:
        print("\n(DRY-RUN - nothing deleted. Re-run with --apply to delete.)")

    await engine.dispose()


def main():
    asyncio.run(run("--apply" in sys.argv))


if __name__ == "__main__":
    main()
