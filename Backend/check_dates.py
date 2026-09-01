"""
READ-ONLY date diagnostic. Makes NO changes.

Shows, for every requirement version, the dates stored in BOTH places so we can see exactly
where a wrong date (e.g. Jan 5 instead of May 1) is coming from:
  - Postgres RequirementVersion: session, discussed_date
  - Qdrant 'requirements' payload : session, date_ymd, discussed_date
  - What session_to_ymd() WOULD derive from the session name (the correct value)

Usage:
    python check_dates.py
"""
import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal, engine
from app.core.qdrant_client import QdrantVectorDB
from app.models.database import RequirementVersion
from app.utils.dates import session_to_ymd


def qdrant_payloads_by_reqid(qdrant):
    """{requirement_id: [payload, ...]} for the requirements collection."""
    out = {}
    offset = None
    while True:
        pts, offset = qdrant.client.scroll(
            collection_name=qdrant.requirements_collection,
            limit=500, with_payload=True, with_vectors=False, offset=offset,
        )
        for p in pts:
            pl = p.payload or {}
            out.setdefault(pl.get("requirement_id", "?"), []).append(pl)
        if offset is None:
            break
    return out


async def main():
    qdrant = QdrantVectorDB()
    q_by_req = qdrant_payloads_by_reqid(qdrant)

    print("=" * 90)
    print("DATE DIAGNOSTIC (read-only)")
    print("=" * 90)

    async with AsyncSessionLocal() as db:
        versions = (await db.execute(
            select(RequirementVersion).order_by(RequirementVersion.discussed_date)
        )).scalars().all()

        for v in versions:
            dd = v.discussed_date.isoformat() if v.discussed_date else "None"
            correct = session_to_ymd(v.session or "", v.discussed_date)
            print(f"\nreq={str(v.requirement_id)[:8]}  v{v.version_number} [{v.change_type}]")
            print(f"  POSTGRES  session='{v.session}'  discussed_date={dd}")
            print(f"  CORRECT (from session name via session_to_ymd) = {correct}")
            for pl in q_by_req.get(str(v.requirement_id), []):
                print(f"  QDRANT    session='{pl.get('session')}'  "
                      f"date_ymd={pl.get('date_ymd')}  discussed_date={pl.get('discussed_date')}")

    await engine.dispose()
    print("\n" + "=" * 90)
    print("If POSTGRES/QDRANT dates differ from CORRECT, the wrong date came in at upload time.")


if __name__ == "__main__":
    asyncio.run(main())
