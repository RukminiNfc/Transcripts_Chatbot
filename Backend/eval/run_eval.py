"""Eval runner: run every eval case through the REAL chat pipeline, grade must_include /
must_not_include on the final answer, and diagnose retrieval-vs-generation on each miss.

Read-only except temporary '__eval__' chat sessions, which are deleted at the end.

Run from Backend/:  ../venv/Scripts/python.exe eval/run_eval.py
"""
import asyncio
import json
import re
import uuid

from sqlalchemy.future import select

from app.core.database import AsyncSessionLocal
from app.models.database import ChatSession
from app.services.chat_service import ChatService

EVAL_USER = "__eval__"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


async def run_case(svc: ChatService, db, case: dict) -> dict:
    sid = uuid.uuid4()
    answer, ctx_blob, intent = "", "", ""
    for turn in case["turns"]:
        res = await svc.process_query(db=db, session_id=sid, query=turn["user"], user_id=EVAL_USER)
        answer = res.get("answer", "")
        srcs = res.get("sources", []) or []
        ctx_blob = " ".join(
            f"{s.get('text','')} {s.get('category','')} {s.get('sub_category','')}" for s in srcs
        )
        intent = (res.get("context_metadata") or {}).get("intent", "")
        sid = res.get("session_id", sid)  # reuse the REAL session id for later turns

    exp = case.get("expect", {})
    a, ctx = _norm(answer), _norm(ctx_blob)
    missing = [m for m in exp.get("must_include", []) if _norm(m) not in a]
    forbidden = [m for m in exp.get("must_not_include", []) if _norm(m) in a]
    passed = not missing and not forbidden

    diag = []
    for m in missing:
        where = "GEN (retrieved but not answered)" if _norm(m) in ctx else "RETRIEVAL (not retrieved)"
        diag.append(f"{where}: {m!r}")
    for f in forbidden:
        diag.append(f"FORBIDDEN present: {f!r}")

    return {"id": case["id"], "scenario": case["scenario"], "passed": passed,
            "intent": intent, "diag": diag, "answer": answer[:160].replace("\n", " ")}


async def main():
    data = json.load(open("eval/eval_set.json", encoding="utf-8"))
    cases = data["cases"]
    svc = ChatService()
    results = []

    async with AsyncSessionLocal() as db:
        for c in cases:
            try:
                r = await run_case(svc, db, c)
            except Exception as e:
                r = {"id": c["id"], "scenario": c["scenario"], "passed": False,
                     "intent": "", "diag": [f"ERROR: {e}"], "answer": ""}
            results.append(r)
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"[{mark}] {r['id']:24} ({r['scenario']})  intent={r['intent']}")
            for d in r["diag"]:
                print(f"        {d}")

        # cleanup temp eval sessions
        evs = (await db.execute(select(ChatSession).filter(ChatSession.user_id == EVAL_USER))).scalars().all()
        for s in evs:
            await db.delete(s)
        await db.commit()

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    retr = sum(1 for r in results for d in r["diag"] if d.startswith("RETRIEVAL"))
    gen = sum(1 for r in results for d in r["diag"] if d.startswith("GEN"))
    print(f"\n==== SCORE: {passed}/{total} passed ({round(100 * passed / total)}%) ====")
    print(f"Missing-fact root cause -> retrieval failures: {retr} | generation failures: {gen}")
    fails = [r["id"] for r in results if not r["passed"]]
    if fails:
        print("FAILED:", ", ".join(fails))


if __name__ == "__main__":
    asyncio.run(main())
