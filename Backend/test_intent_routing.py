"""
GATE 2 — prove a change to the intent-classifier prompt did not alter routing.

The classifier is one LLM call whose prompt decides how EVERY question is routed. Editing that
prompt is the only genuinely risky part of adding a new chat capability. This script captures
how 20 representative questions route, so the same 20 can be re-run afterwards and compared
field by field.

CHEAPER THAN THE FULL EVAL, ON PURPOSE. It calls analyze_intent only — no retrieval, no
re-ranking, no answer generation. 20 small calls per run against ~400 for one transcript upload.

Inputs are FIXED (dates are hardcoded, history is empty) so two runs are comparable regardless
of what is in the database on the day.

Usage (from the Backend/ directory):

    python test_intent_routing.py --capture          # before the prompt change
    python test_intent_routing.py --compare          # after it

Read-only: makes no database or Qdrant writes. Costs 20 LLM calls per run.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.query_processor import QueryProcessor  # noqa: E402

BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "eval", "intent_routing_baseline.json")

# Fixed, so "the last call" resolves identically on every run.
AVAILABLE_DATES = ["2026-05-01", "2026-05-04", "2026-05-05"]

# 20 questions chosen to exercise EVERY routing outcome at least once. If a prompt edit
# disturbs routing, one of these will move.
QUESTIONS = [
    # aggregate
    "how many requirements are there?",                              # count
    "list all requirements",                                         # list
    # changes, in its three shapes
    "what changed across all the calls?",                            # unscoped -> overview
    "what requirements were added for CI/CD? include the dates",     # topic-scoped
    "what was added in the May 1 call?",                             # date-scoped
    # complete requirements vs plain explanation — the pair that must stay distinct
    "full requirements on Jobs mobile app",                          # complete = true
    "what is the rollback requirement?",                             # complete = false
    # normal retrieval
    "explain eRegister integration",
    "compare how dev and test deployments should work",              # broad scope
    "what are the requirements for payroll tax calculation?",        # nothing to find
    # filters
    "what did Prasad say about deployments?",                        # speaker
    "who confirmed the rollback requirement?",                       # attribution
    "what requirements came from the May 1 call?",                   # call_date
    "what was discussed in the last call?",                          # relative date
    # transform
    "summarize that",                                                # previous_answer
    "summarise candidate management",                                # topic
    "give me the BRD",                                               # topic, document-ish
    # verify + metadata
    "are you sure?",
    "who is our client?",
    # the trap: contains "minutes", must still route as a normal question
    "what's the session timeout in minutes?",
]

# Fields that decide WHICH lane runs. A change in any of these is a routing regression.
ROUTING_FIELDS = [
    "intent", "mode", "transform_scope", "aggregate", "verify",
    "scope", "changes", "complete_requirements", "needs_dialogue",
]


def run_all() -> dict:
    processor = QueryProcessor()
    results = {}
    for i, question in enumerate(QUESTIONS, 1):
        print(f"  [{i:>2}/{len(QUESTIONS)}] {question[:58]}")
        intent = processor.analyze_intent(question, conversation_history=[],
                                          available_dates=AVAILABLE_DATES)
        record = {f: intent.get(f) for f in ROUTING_FIELDS}
        record["topic"] = (intent.get("topic") or "").strip().lower()
        filters = intent.get("filters", {}) or {}
        record["filters"] = {k: filters.get(k) for k in ("speaker", "session", "call_date")}
        results[question] = record
    return results


def capture() -> None:
    print("=" * 72)
    print("CAPTURING BASELINE — how these questions route TODAY")
    print("=" * 72)
    results = run_all()
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, sort_keys=True)
    print(f"\nSaved to {BASELINE_PATH}")
    print("Make the prompt change, then run with --compare.")


def compare() -> None:
    if not os.path.exists(BASELINE_PATH):
        raise SystemExit(f"No baseline at {BASELINE_PATH}. Run --capture first.")
    with open(BASELINE_PATH, encoding="utf-8") as handle:
        baseline = json.load(handle)

    print("=" * 72)
    print("COMPARING — routing must be identical for every non-minutes question")
    print("=" * 72)
    current = run_all()

    regressions = []
    print()
    for question, before in baseline.items():
        after = current.get(question)
        if after is None:
            print(f"  [SKIP] {question!r} — not in this run")
            continue
        changed = {k: (before[k], after[k]) for k in before if before[k] != after.get(k)}
        if changed:
            regressions.append((question, changed))
            print(f"  [CHANGED] {question}")
            for field, (was, now) in changed.items():
                print(f"       {field}: {was!r}  ->  {now!r}")
        else:
            print(f"  [same] {question[:60]}")

    print()
    print("=" * 72)
    if regressions:
        print(f"RESULT: {len(regressions)} question(s) route differently than before.")
        print("Review each. A change is only acceptable if it is the intended new behaviour.")
        raise SystemExit(1)
    print("RESULT: PASSED — routing unchanged on all 20 questions")
    print("=" * 72)


if __name__ == "__main__":
    if "--capture" in sys.argv:
        capture()
    elif "--compare" in sys.argv:
        compare()
    else:
        raise SystemExit(__doc__)
