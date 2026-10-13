"""
Per-stage cost and time for a traced upload.

WHY THIS EXISTS
---------------
Langfuse records cost on GENERATION observations only — parent SPANs show no cost — and every
call is named "OpenAI-generation", so neither the trace view nor a dashboard grouped by name can
answer "how much did `compare` cost?". This walks the observation tree and rolls the costs up
to the stage that contains them.

READ-ONLY. No LLM calls, no database, no writes — it only reads back traces already recorded,
so it costs nothing to run as often as you like.

Usage (from Backend/):
    python trace_cost_report.py                  # newest transcript_upload trace
    python trace_cost_report.py <trace_id>       # a specific one
    python trace_cost_report.py --name mom_generation
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.observability import TRACING_ENABLED  # noqa: E402
from langfuse import get_client                      # noqa: E402


def _money(value: float) -> str:
    return f"${value:,.4f}" if value >= 0.0001 else f"${value:.6f}"


def report(trace_id: str) -> None:
    lf = get_client()
    trace = lf.api.trace.get(trace_id)

    obs = list(trace.observations)
    by_id = {o.id: o for o in obs}

    def stage_of(o):
        """
        Walk up to the stage directly beneath the root — parse / extract / compare.

        The stage is the LAST node before the root, not the root itself: a generation sits
        under `extract`, which sits under `transcript_upload`, which has no parent.
        """
        node, seen = o, set()
        while node.parent_observation_id and node.parent_observation_id in by_id:
            if node.id in seen:
                break
            seen.add(node.id)
            parent = by_id[node.parent_observation_id]
            if parent.parent_observation_id is None:
                # `parent` is the root, so `node` is the stage we want.
                return node.name if node.type != "GENERATION" else "(no stage — under root)"
            node = parent
        return "(outside any stage)"

    cost = defaultdict(float)
    calls = defaultdict(int)
    tokens = defaultdict(int)
    for o in obs:
        if o.type not in ("GENERATION", "EMBEDDING"):
            continue
        stage = stage_of(o)
        cost[stage] += o.calculated_total_cost or 0.0
        calls[stage] += 1
        tokens[stage] += (o.usage.total or 0) if o.usage else 0

    latency = {o.name: o.latency for o in obs if o.type == "SPAN"}
    total = sum(cost.values())

    print("=" * 74)
    print(f"{trace.name}   {trace.id}")
    meta = trace.metadata or {}
    for key in ("session_name", "transcript_id", "customer_id"):
        if meta.get(key):
            print(f"   {key:<16}{meta[key]}")
    print("=" * 74)
    print(f"{'STAGE':<26}{'COST':>12}{'SHARE':>8}{'CALLS':>7}{'TOKENS':>10}{'TIME':>9}")
    print("-" * 74)
    for stage in sorted(cost, key=lambda s: -cost[s]):
        share = (cost[stage] / total * 100) if total else 0
        secs = latency.get(stage)
        print(f"{stage:<26}{_money(cost[stage]):>12}{share:>7.1f}%{calls[stage]:>7}"
              f"{tokens[stage]:>10}{(f'{secs:.1f}s' if secs else '-'):>9}")
    print("-" * 74)
    total_secs = trace.latency or 0
    print(f"{'TOTAL':<26}{_money(total):>12}{'':>8}{sum(calls.values()):>7}"
          f"{sum(tokens.values()):>10}{f'{total_secs:.1f}s':>9}")

    # The split that signals fragmented version chains — see the compare span's metadata.
    for o in obs:
        m = o.metadata or {}
        if o.type == "SPAN" and "classified_added" in m:
            a, mod, un = (m.get("classified_added", 0), m.get("classified_modified", 0),
                          m.get("classified_unchanged", 0))
            revisits = mod + un
            print()
            print(f"Classification:  added {a} | modified {mod} | unchanged {un}")
            if a and revisits / (a + revisits) < 0.15:
                print("  NOTE: very few revisits. On a call covering familiar topics that is the")
                print("        signature of fragmented version chains, not genuinely new rules.")


def main() -> None:
    if not TRACING_ENABLED:
        print("Tracing is disabled — nothing to report.")
        raise SystemExit(1)

    args = [a for a in sys.argv[1:]]
    name = "transcript_upload"
    trace_id = None
    if "--name" in args:
        name = args[args.index("--name") + 1]
    elif args:
        trace_id = args[0]

    lf = get_client()
    if trace_id is None:
        page = lf.api.trace.list(name=name, limit=1)
        if not page.data:
            print(f"No trace named {name!r} found yet. Upload a transcript first.")
            raise SystemExit(1)
        trace_id = page.data[0].id

    report(trace_id)


if __name__ == "__main__":
    main()
