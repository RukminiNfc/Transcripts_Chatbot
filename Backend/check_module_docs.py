"""
Which module documents exist in Google Drive, and which still need creating.

WHY THIS IS A MANUAL STEP
-------------------------
A service account owns nothing and has no Drive storage of its own, so it cannot CREATE files
in an ordinary Drive folder — Google reports that as "storage quota exceeded", which reads like
a full disk and is nothing of the sort. It can read and write files it does not own perfectly
well, so the documents are created by hand once and the pipeline writes to them from then on.

(On Google Workspace a Shared Drive removes this entirely, because files there are owned by the
drive rather than by whoever created them. Nothing in the code would need to change.)

New modules are already admin-approved before use, so creating one empty document at that same
moment fits the workflow rather than fighting it.

Usage (from Backend/):
    python check_module_docs.py                 # report, and link each document found
    python check_module_docs.py --names-only    # just the names to create, for copying
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings                      # noqa: E402
from app.core.database import AsyncSessionLocal, engine   # noqa: E402
from app.models.database import Customer                  # noqa: E402
from app.services.google_docs_service import google_docs  # noqa: E402
from app.services.module_registry import list_modules     # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--names-only", action="store_true",
                        help="print only the titles that need creating")
    parser.add_argument("--customer-id", help="defaults to the only customer")
    args = parser.parse_args()

    if not google_docs.enabled:
        print("Google Docs integration is not configured.")
        print("Run test_google_docs.py for the specific reason.")
        raise SystemExit(1)

    async with AsyncSessionLocal() as db:
        if args.customer_id:
            import uuid
            customer = (await db.execute(
                select(Customer).filter(Customer.id == uuid.UUID(args.customer_id))
            )).scalars().first()
        else:
            customer = (await db.execute(select(Customer))).scalars().first()
        if not customer:
            print("No customer found.")
            raise SystemExit(1)
        modules = await list_modules(db, customer.id)
    await engine.dispose()

    if not modules:
        print(f"No modules defined for {customer.name}. Run seed_modules.py first.")
        raise SystemExit(1)

    titles = [m.module_name for m in modules]
    result = google_docs.check_documents(titles)

    if args.names_only:
        for t in result["missing"]:
            print(t)
        return

    status = google_docs.status()
    print("=" * 68)
    print(f"MODULE DOCUMENTS — {customer.name}")
    print(f"Drive folder: {status['folder_name'] or '(unreadable)'}")
    print("=" * 68)

    for title in titles:
        if title in result["found"]:
            print(f"  OK       {title}")
            print(f"           https://docs.google.com/document/d/{result['found'][title]}/edit")
        else:
            print(f"  MISSING  {title}")

    print("\n" + "=" * 68)
    print(f"{len(result['found'])} of {len(titles)} documents exist")

    if result["missing"]:
        print(f"\nCreate these {len(result['missing'])} as empty Google Docs in the folder,")
        print("named EXACTLY as shown (the pipeline finds them by title):\n")
        for t in result["missing"]:
            print(f"    {t}")
        print(f"\nFolder: https://drive.google.com/drive/folders/{settings.GOOGLE_DRIVE_FOLDER_ID}")
        print("\nThe folder is already shared with the service account, so documents created")
        print("inside it are writable without any further sharing.")
    else:
        print("\nAll module documents are present — the pipeline can write to every module.")


if __name__ == "__main__":
    asyncio.run(main())
