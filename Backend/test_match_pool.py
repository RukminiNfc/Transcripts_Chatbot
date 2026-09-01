"""
REAL-POOL match test. Read-only (no DB/Qdrant writes).

Unlike test_change_detection.py (which fed ONE hand-picked candidate), this runs the ACTUAL
retrieval + match + identity-gate + classify against whatever requirements are currently stored
— the real ~90-requirement pool. That's the hard case that produced the false-positive emails.

RUN THIS WITH ONLY T1 LOADED (wipe -> upload the older transcript only), then run this script.
It feeds the 5 problem "modified" emails and checks the ONE thing that matters: does it EMAIL?
Email fires only on a genuine "modified". Everything else (added / unchanged) is silent.

Expected on clean T1-only data (only #2 should email):
  #1 employee comments   -> NO email (different rule: hide comments vs lesson-plan comments)
  #2 Azure DevOps        -> EMAIL   (genuine implementation change: any pipeline -> Azure DevOps)
  #3 dropdown params     -> NO email (different rule: HOW you select)
  #4 UI project examples -> NO email (just examples/elaboration of the same rule)
  #5 UI deploy scope     -> NO email (different aspect from API/UI ordering)

Usage:
    python test_match_pool.py
"""
import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal, engine
from app.models.database import Customer
from app.services.requirement_comparison import RequirementComparisonService

# (new requirement text from T2, should_email, label)
#   should_email = True only for a GENUINE modification of the same requirement.
CASES = [
    ("For non-normal order types, hide Comments for Employees and keep the internal comments "
     "field or Comments for TemPositions style field instead.",
     False, "#1 employee comments (vs 'lesson-plan info in order comments') — different rule"),

    ("All code deployments to servers must go through Azure DevOps pipelines or Release Hub going "
     "forward, and code must not be deployed directly from local machines to the server.",
     False, "#2 Azure DevOps pipelines (elaboration of pipeline rule - no email)"),

    ("Use dropdown values for deployment parameters where possible so users select valid values "
     "instead of typing them manually.",
     False, "#3 dropdown parameters (HOW you select — different rule)"),

    ("Each UI project in the TemPositions UI repository should have its own pipeline or YAML file, "
     "such as Admin Portal, Client Portal, Employee Portal, E-Register, IntelliStaff 2.",
     False, "#4 UI project pipelines (just adds examples — elaboration)"),

    ("The UI pipeline may build the overall project or repo, but it should deploy only the "
     "configured project's distribution folder.",
     False, "#5 UI deploy scope (different aspect from API/UI ordering)"),
]


async def _get_customer_id():
    async with AsyncSessionLocal() as db:
        c = (await db.execute(select(Customer))).scalars().first()
        return c.id if c else None


async def _cleanup():
    try:
        await engine.dispose()
    except Exception:
        pass


def main():
    cid = asyncio.run(_get_customer_id())
    if cid is None:
        print("No customer found. Load T1 first."); return

    svc = RequirementComparisonService()
    print("=" * 84)
    print(f"REAL-POOL MATCH TEST   (model: {svc.model})   — run with ONLY T1 loaded")
    print("=" * 84)

    correct = 0
    for text, should_email, label in CASES:
        vec = svc.embedding_service.generate_embeddings([text])[0]
        res = svc._search_and_match(text, vec, cid)
        decision = res["decision"]
        cands = res["candidates"]
        match = decision.get("match")

        if match is None:
            verdict = "added (new)"
        else:
            verdict = decision.get("status", "?")
        emails = (match is not None and decision.get("status") == "modified")

        ok = (emails == should_email)
        if ok:
            correct += 1
        flag = "PASS" if ok else "**FAIL**"
        print(f"\n[{flag}] {label}")
        print(f"    verdict     : {verdict}")
        print(f"    sends email : {emails}   (should be {should_email})")
        if match is not None and match < len(cands):
            mp = cands[match].get("payload", {})
            print(f"    matched     : {(mp.get('requirement_text') or mp.get('canonical_text',''))[:70]}")
        if decision.get("change_summary"):
            print(f"    reason      : {decision.get('change_summary')}")

    print("\n" + "=" * 84)
    print(f"RESULT: {correct}/{len(CASES)} correct")
    print("Target: ALL 5 cases stay silent. No emails should be sent.")
    print("====================================================================================")
    
    try:
        asyncio.run(_cleanup())
    except Exception:
        pass


if __name__ == "__main__":
    main()
