"""
CLEAN-SLATE WIPE — deletes all transcript/requirement/conversation data so a fresh
backfill can build correct history from empty.

KEEPS: customers, team_subscriptions   (your project + email recipients stay)
DELETES:
  Postgres : transcripts, conversation_logs, requirements, requirement_versions,
             chat_sessions, query_logs
  Qdrant   : 'conversations' and 'requirements' collections (dropped & recreated empty)

SAFETY: it first PRINTS the exact target (which machine's DB/Qdrant) and how much it will
delete, then makes you TYPE 'WIPE' to proceed. Nothing is deleted until you confirm.

Works the same on local and server — it reads the connection from the .env on THIS machine.
So ALWAYS read the printed "Target" line before typing WIPE, to be sure you're on the right box.

Usage:
    python wipe_data.py
"""
import asyncio
from urllib.parse import urlparse

from sqlalchemy import select, func, delete

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.qdrant_client import QdrantVectorDB
from app.models.database import (
    Customer, TeamSubscription, Transcript, ConversationLog,
    Requirement, RequirementVersion, ChatSession, QueryLog,
)


def _host(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.hostname}:{p.port}{p.path}"
    except Exception:
        return "(unparsable url)"


async def main():
    qdrant = QdrantVectorDB()

    async with AsyncSessionLocal() as db:
        # --- Count what WILL be deleted, and what will be KEPT ---
        counts = {}
        for name, model in [
            ("transcripts", Transcript),
            ("conversation_logs", ConversationLog),
            ("requirements", Requirement),
            ("requirement_versions", RequirementVersion),
            ("chat_sessions", ChatSession),
            ("query_logs", QueryLog),
        ]:
            counts[name] = (await db.execute(select(func.count()).select_from(model))).scalar()

        keep_customers = (await db.execute(select(func.count()).select_from(Customer))).scalar()
        keep_subs = (await db.execute(select(func.count()).select_from(TeamSubscription))).scalar()

        conv_vecs = qdrant.client.count(qdrant.conversations_collection, exact=True).count
        req_vecs = qdrant.client.count(qdrant.requirements_collection, exact=True).count

        print("=" * 70)
        print("CLEAN-SLATE WIPE — review carefully before confirming")
        print("=" * 70)
        print(f"Target Postgres : {_host(settings.DATABASE_URL)}")
        print(f"Target Qdrant   : {settings.QDRANT_HOST}:{settings.QDRANT_PORT}")
        print("-" * 70)
        print("Will DELETE (Postgres):")
        for name, c in counts.items():
            print(f"    {name:<22}: {c}")
        print("Will DELETE (Qdrant):")
        print(f"    conversations vectors : {conv_vecs}")
        print(f"    requirements vectors  : {req_vecs}")
        print("-" * 70)
        print("Will KEEP:")
        print(f"    customers          : {keep_customers}")
        print(f"    team_subscriptions : {keep_subs}")
        print("=" * 70)

        confirm = input("Type 'WIPE' (all caps) to proceed, anything else to cancel: ").strip()
        if confirm != "WIPE":
            print("Cancelled. Nothing was deleted.")
            await engine.dispose()
            return

        # --- Delete Postgres rows (keep customers + team_subscriptions) ---
        for model in [QueryLog, ChatSession, RequirementVersion, Requirement,
                      ConversationLog, Transcript]:
            await db.execute(delete(model))
        await db.commit()
        print("Postgres rows deleted (customers + team_subscriptions kept).")

        # --- Drop & recreate the Qdrant collections (empty) ---
        for coll in [qdrant.conversations_collection, qdrant.requirements_collection]:
            try:
                qdrant.client.delete_collection(coll)
                print(f"Dropped Qdrant collection '{coll}'.")
            except Exception as e:
                print(f"  (could not drop '{coll}': {e})")
            qdrant.ensure_collection(coll)  # recreate empty
        print("Qdrant collections recreated empty.")

    await engine.dispose()
    print("\nWipe complete. You now have a clean slate (customers + recipients preserved).")


if __name__ == "__main__":
    asyncio.run(main())
