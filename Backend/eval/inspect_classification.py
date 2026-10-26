import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.future import select
from app.core.database import AsyncSessionLocal
from app.models.database import MeetingMinutes, Customer
from app.services.module_classifier import module_classifier, parse_sections
from app.services import module_registry as registry

async def main():
    async with AsyncSessionLocal() as db:
        cust = (await db.execute(select(Customer))).scalars().first()
        active_modules = await registry.list_modules(db, cust.id, include_proposed=False)
        module_block = registry.as_classifier_block(active_modules)

        for session_name in ["10-06-26_Grooming", "10-08-26_Grooming"]:
            mom = (await db.execute(
                select(MeetingMinutes).filter(MeetingMinutes.session_name == session_name)
            )).scalars().first()
            if not mom:
                continue
            print("=" * 80)
            print(f"ANALYZING: {session_name}")
            print("=" * 80)
            sections = parse_sections(mom.content_markdown)
            res = module_classifier.classify(sections, module_block)
            print(f"Proposals ({len(res.get('proposals', []))}):")
            for p in res.get("proposals", []):
                print(f"  * Proposed: {p.get('name')} -- {p.get('reason')}")
            print("\nAssignments:")
            for a in res.get("assignments", []):
                idx = a.get("index")
                heading = sections[idx]["heading"] if 0 <= idx < len(sections) else "?"
                print(f"  [{a.get('confidence')}] Section '{heading[:40]}' -> Module: '{a.get('module')}' ({a.get('reason')})")

if __name__ == "__main__":
    asyncio.run(main())
