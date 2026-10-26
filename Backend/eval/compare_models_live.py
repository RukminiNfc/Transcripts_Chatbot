"""Side-by-side comparison of gpt-5.5 vs gpt-5.4-mini on real transcript module data.

Loads the real CARV module before Call 2 and Call 2's incoming requirements,
runs reconcile_blocks with gpt-5.5 and gpt-5.4-mini, and prints an exact side-by-side diff.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.future import select
from app.core.database import AsyncSessionLocal
from app.models.database import MeetingMinutes, ModuleDocument, Customer
from app.services.module_classifier import parse_sections
from app.services.module_reconciler import module_reconciler
from app.services.candidate_prefilter import select_candidate_blocks
from app.services.requirement_blocks import parse_document_blocks


async def main():
    async with AsyncSessionLocal() as db:
        # 1. Fetch Customer
        cust = (await db.execute(select(Customer))).scalars().first()
        if not cust:
            print("No customer found.")
            return

        # 2. Fetch Call 2 minutes (10-05-26_Grooming)
        mom_call2 = (await db.execute(
            select(MeetingMinutes).filter(MeetingMinutes.session_name == "10-05-26_Grooming")
        )).scalars().first()

        if not mom_call2:
            print("Call 2 (10-05-26_Grooming) not found.")
            return

        # 3. Find CARV module
        module = (await db.execute(
            select(ModuleDocument).filter(
                ModuleDocument.customer_id == cust.id,
                ModuleDocument.module_name.ilike("%CARV%")
            )
        )).scalars().first()

        if not module:
            print("CARV module not found.")
            return

        print(f"Loaded module: {module.module_name}")
        print(f"Current version in DB: {module.current_version}")

        # Parse sections from Call 2
        sections = parse_sections(mom_call2.content_markdown)
        carv_sections = [s for s in sections if "carv" in s.get("heading", "").lower()]
        incoming = [r for s in carv_sections for r in (s.get("requirements") or [])]
        print(f"Incoming requirements from Call 2: {len(incoming)}")

        # Parse existing blocks
        existing = [b for b in parse_document_blocks(module.content_markdown or "") if not b.get("deprecated")]
        print(f"Existing requirements in CARV: {len(existing)}")

        # Pre-filter
        candidates = select_candidate_blocks(existing, incoming, max_candidates=10)
        print(f"Filtered candidate blocks sent to AI: {len(candidates)}")

        # Run with gpt-5.5
        print("\n" + "=" * 80)
        print("RUNNING WITH GPT-5.5 (Flagship Model)...")
        t0 = time.time()
        res_55 = module_reconciler.reconcile_blocks(
            module_name=module.module_name,
            module_description=module.description or "",
            existing=candidates,
            incoming=incoming,
            session_name="10-05-26_Grooming",
            call_date="2026-10-05",
            model="gpt-5.5",
        )
        t_55 = time.time() - t0
        print(f"GPT-5.5 finished in {t_55:.2f}s")

        # Run with gpt-5.4-mini
        print("\n" + "=" * 80)
        print("RUNNING WITH GPT-5.4-MINI (Cheaper Mini Model)...")
        t0 = time.time()
        res_mini = module_reconciler.reconcile_blocks(
            module_name=module.module_name,
            module_description=module.description or "",
            existing=candidates,
            incoming=incoming,
            session_name="10-05-26_Grooming",
            call_date="2026-10-05",
            model="gpt-5.4-mini",
        )
        t_mini = time.time() - t0
        print(f"GPT-5.4-MINI finished in {t_mini:.2f}s (Speedup: {t_55 / t_mini:.1f}x faster)")

        print("\n" + "=" * 80)
        print("DECISION COMPARISON:")
        print("=" * 80)

        def print_decisions(model_name, res):
            print(f"--- {model_name} Decisions ---")
            print(f"Summary: {res.get('summary')}")
            for d in res.get("decisions", []):
                action = d.get("action")
                keys = d.get("incoming_keys")
                updates = d.get("updates") or []
                tgt = updates[0].get("target_key") if updates else d.get("target_key")
                print(f"  * Action: {action:<8} Target: {str(tgt):<30} Incoming: {keys}")
                if action == "MODIFY":
                    for u in updates:
                        print(f"      Updated bullets count: {len(u.get('bullets', []))}")
                        for b in u.get("bullets", [])[:2]:
                            print(f"        - {b[:80]}...")
            print()

        print_decisions("GPT-5.5", res_55)
        print_decisions("GPT-5.4-MINI", res_mini)


if __name__ == "__main__":
    asyncio.run(main())
