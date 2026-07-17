"""
One-time backfill: stamp a normalized `date_ymd` (YYYY-MM-DD) onto EXISTING vectors in both
Qdrant stores, so date-scoped questions can pre-filter server-side.

Nothing is hardcoded: the date for each vector is derived from the `session` already stored on
that vector (falling back to its stored timestamp). No re-embedding, no LLM, no re-extraction.

New uploads already write `date_ymd` at ingest, so this only needs to run ONCE per environment
(local now, server after deploy). Safe to re-run — it just re-writes the same value.

Usage (from the Backend/ directory):
    python backfill_date_ymd.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qdrant_client import QdrantClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.utils.dates import session_to_ymd  # noqa: E402

COLLECTIONS = ("conversations", "requirements")
PAGE = 256


def backfill_collection(client: QdrantClient, collection: str) -> None:
    print(f"\n=== {collection} ===")
    by_date: dict[str, list] = defaultdict(list)
    total = skipped = 0
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=PAGE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            total += 1
            payload = p.payload or {}
            session = payload.get("session")
            fallback = payload.get("call_date") or payload.get("discussed_date")
            ymd = session_to_ymd(session, fallback)
            if ymd:
                by_date[ymd].append(p.id)
            else:
                skipped += 1
        if offset is None:
            break

    for ymd, ids in by_date.items():
        # Set the SAME date_ymd on every point that resolved to it — one call per date group.
        client.set_payload(collection_name=collection, payload={"date_ymd": ymd}, points=ids)
        print(f"  {ymd}: stamped {len(ids)} vectors")

    print(f"  total scanned={total}  stamped={total - skipped}  skipped(no date)={skipped}")


def main() -> None:
    client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    print(f"Backfilling date_ymd on {settings.QDRANT_HOST}:{settings.QDRANT_PORT}")
    for coll in COLLECTIONS:
        try:
            backfill_collection(client, coll)
        except Exception as e:
            print(f"  ERROR on '{coll}': {e}")
    print("\nDone.")


if __name__ == "__main__":
    main()
