"""Reconciliation Golden Dataset Eval Runner.

Tests module requirement reconciliation against golden test cases in eval/reconcile_gold.json.
Evaluates:
  1. Action Accuracy: did the model choose ADD, MODIFY, DEPRECATE, or SKIP correctly?
  2. Target Linking Accuracy: for MODIFY / DEPRECATE, did it link to the exact right existing block?
  3. Content Preservation: did updated/added bullets include required domain content?
  4. Task Pruning Accuracy: did it successfully separate operational tasks from specifications?

Usage (from Backend/):
    ../venv/Scripts/python.exe eval/test_reconciliation.py
    ../venv/Scripts/python.exe eval/test_reconciliation.py --model gpt-5.4-mini
    ../venv/Scripts/python.exe eval/test_reconciliation.py --model gpt-4o-mini
    ../venv/Scripts/python.exe eval/test_reconciliation.py --case carv_skill_mapping_modify
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Ensure Backend/ is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services.module_reconciler import module_reconciler


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def evaluate_case(
    case: dict[str, Any],
    reconciled: dict[str, Any],
) -> dict[str, Any]:
    expected = case["expected"]
    exp_action = expected["action"]
    exp_target = expected.get("target_heading_key")
    must_include = expected.get("must_include") or []
    must_drop_task = expected.get("must_drop_task", False)

    decisions = reconciled.get("decisions") or []
    if not decisions:
        return {
            "passed": False,
            "action_pass": False,
            "target_pass": False,
            "content_pass": False,
            "task_drop_pass": False,
            "actual_action": "NONE",
            "actual_target": None,
            "notes": ["No decisions returned by model (or error occurred)"],
        }

    # Primary decision for incoming block
    d0 = decisions[0]
    act_action = d0.get("action")
    act_target = None

    if act_action == "MODIFY":
        updates = d0.get("updates") or []
        if updates:
            act_target = updates[0].get("target_key")
    elif act_action in ("DEPRECATE", "SKIP"):
        act_target = d0.get("target_key")

    notes = []

    # 1. Action Check
    action_pass = (act_action == exp_action)
    if not action_pass:
        notes.append(f"Action mismatch: expected {exp_action}, got {act_action}")

    # 2. Target Check
    target_pass = True
    if exp_target is not None:
        if act_target != exp_target:
            target_pass = False
            notes.append(f"Target mismatch: expected {exp_target!r}, got {act_target!r}")
    elif exp_action == "ADD" and act_action == "ADD":
        target_pass = True

    # 3. Content Check
    content_pass = True
    all_bullets = []
    if act_action == "MODIFY":
        for u in d0.get("updates") or []:
            all_bullets.extend(u.get("bullets") or [])
    elif act_action == "ADD":
        all_bullets.extend(d0.get("bullets") or [])

    bullets_text = " ".join(all_bullets).lower()
    for phrase in must_include:
        if phrase.lower() not in bullets_text:
            content_pass = False
            notes.append(f"Missing required phrase in output bullets: {phrase!r}")

    # 4. Task Drop Check
    task_drop_pass = True
    if must_drop_task:
        dropped = d0.get("dropped") or []
        if not dropped:
            task_drop_pass = False
            notes.append("Expected task bullets to be dropped, but none were dropped.")
        else:
            notes.append(f"Successfully pruned {len(dropped)} task bullet(s)")

    passed = action_pass and target_pass and content_pass and task_drop_pass

    return {
        "passed": passed,
        "action_pass": action_pass,
        "target_pass": target_pass,
        "content_pass": content_pass,
        "task_drop_pass": task_drop_pass,
        "actual_action": act_action,
        "actual_target": act_target,
        "notes": notes,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate module requirement reconciliation.")
    parser.add_argument("--model", type=str, default=None,
                        help=f"Model to test (default: {settings.OPENAI_COMPARISON_MODEL})")
    parser.add_argument("--dataset", type=str, default="eval/reconcile_gold.json",
                        help="Path to golden dataset JSON file")
    parser.add_argument("--case", type=str, default=None,
                        help="Run a specific case ID only")
    args = parser.parse_args()

    model_to_test = args.model or settings.OPENAI_COMPARISON_MODEL

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = Path(__file__).parent.parent / args.dataset

    if not dataset_path.exists():
        print(f"Error: Dataset not found at {dataset_path}")
        sys.exit(1)

    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data.get("cases", [])
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"Error: No case with id {args.case!r} found in dataset.")
            sys.exit(1)

    print("=" * 88)
    print(f"RECONCILIATION ACCURACY EVALUATION: {len(cases)} cases")
    print(f"Model Under Test: {model_to_test}")
    print("=" * 88)

    results = []
    total_start = time.time()

    for i, case in enumerate(cases, start=1):
        case_id = case["id"]
        exp_act = case["expected"]["action"]
        exp_tgt = case["expected"].get("target_heading_key") or "-"

        t0 = time.time()
        reconciled = module_reconciler.reconcile_blocks(
            module_name=case["module_name"],
            module_description=case.get("module_description", ""),
            existing=case.get("existing", []),
            incoming=case.get("incoming", []),
            session_name="Eval_Session",
            call_date="2026-10-26",
            model=model_to_test,
        )
        elapsed = time.time() - t0

        eval_res = evaluate_case(case, reconciled)
        eval_res["id"] = case_id
        eval_res["description"] = case.get("description", "")
        eval_res["expected_action"] = exp_act
        eval_res["expected_target"] = exp_tgt
        eval_res["elapsed"] = elapsed
        eval_res["error"] = reconciled.get("error")
        results.append(eval_res)

        status_tag = "[PASS]" if eval_res["passed"] else "[FAIL]"
        act_tgt = eval_res["actual_target"] or "-"
        print(f"{status_tag} {i:2d}. {case_id:<32} "
              f"Act: {exp_act}->{eval_res['actual_action']:<7} "
              f"Tgt: {exp_tgt}->{act_tgt:<26} ({elapsed:.1f}s)")
        for n in eval_res["notes"]:
            print(f"         * {n}")
        if eval_res["error"]:
            print(f"         * ERROR: {eval_res['error']}")

    total_time = time.time() - total_start
    total_cases = len(results)
    passed_cases = sum(1 for r in results if r["passed"])
    action_passes = sum(1 for r in results if r["action_pass"])
    target_passes = sum(1 for r in results if r["target_pass"])
    content_passes = sum(1 for r in results if r["content_pass"])
    task_drop_passes = sum(1 for r in results if r["task_drop_pass"])

    print("\n" + "=" * 88)
    print("EVALUATION SUMMARY & ACCURACY SCORE")
    print("-" * 88)
    print(f"  Overall Pass Rate:             {passed_cases}/{total_cases} ({passed_cases / total_cases * 100:.1f}%)")
    print(f"  Action Decision Accuracy:      {action_passes}/{total_cases} ({action_passes / total_cases * 100:.1f}%)")
    print(f"  Target Block Linking Accuracy: {target_passes}/{total_cases} ({target_passes / total_cases * 100:.1f}%)")
    print(f"  Content Preservation Rate:     {content_passes}/{total_cases} ({content_passes / total_cases * 100:.1f}%)")
    print(f"  Task Bullet Pruning Rate:      {task_drop_passes}/{total_cases} ({task_drop_passes / total_cases * 100:.1f}%)")
    print(f"  Total Benchmark Time:          {total_time:.1f}s (avg {total_time / total_cases:.2f}s per case)")
    print("=" * 88)

    if passed_cases < total_cases:
        failed_ids = [r["id"] for r in results if not r["passed"]]
        print(f"Failed cases: {', '.join(failed_ids)}")
        sys.exit(1)
    else:
        print("ALL RECONCILIATION BENCHMARKS PASSED SUCCESSFULLY!")
        sys.exit(0)


if __name__ == "__main__":
    main()
