"""
Per-stage cost and time for one transcript, across every trace it produced.

WHY THIS EXISTS
---------------
Langfuse records cost on GENERATION / EMBEDDING observations only — parent SPANs show no cost —
and the patched OpenAI client names every call "OpenAI-generation", so neither the trace view nor
a dashboard grouped by name can answer "how much did reconcile cost?". This walks each trace's
observation tree and rolls the costs up to the STAGE that contains them.

One transcript is processed by two Celery tasks — `transcript_upload` (parse, extract, compare)
and `mom_generation` (minutes, classify, reconcile per module, index per module) — which are two
separate traces. Both carry the transcript id as their Langfuse SESSION, and that is what this
report uses to add them up into one all-in number.

READ-ONLY. No LLM calls, no database, no writes — it only reads back traces already recorded,
so it costs nothing to run as often as you like.

Usage (from Backend/):
    python trace_cost_report.py                        # newest transcript: both traces, one table
    python trace_cost_report.py --transcript <uuid>    # a specific transcript (its session)
    python trace_cost_report.py <trace_id>             # one trace on its own
    python trace_cost_report.py --name mom_generation  # newest trace with that name, on its own
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.observability import TRACING_ENABLED  # noqa: E402
from langfuse import get_client                      # noqa: E402

# The order stages appear in the table — pipeline order, not cost order, so the same row is in
# the same place on every run and two runs can be compared by eye.
_STAGE_ORDER = [
    "parse", "extract", "compare",
    "minutes_generate", "module_classify", "module_reconcile", "module_auto_backfill", "module_index",
]
# Stages that are expected to show zero when a flag is off. Printed anyway so the saving is
# visible rather than the stage silently vanishing.
_EXPECTED_STAGES = {"transcript_upload": ["parse", "extract", "compare"],
                    "mom_generation": ["minutes_generate", "module_classify",
                                       "module_reconcile", "module_index"]}
_COSTED_TYPES = {"GENERATION", "EMBEDDING"}


def _money(value: float) -> str:
    return f"${value:,.4f}" if value >= 0.0001 else f"${value:.6f}"


@dataclass
class StageRow:
    trace_name: str
    stage: str
    module: str = ""
    cost: float = 0.0
    calls: int = 0
    tokens: int = 0
    seconds: float = 0.0
    unpriced: int = 0          # calls Langfuse recorded with no cost (model has no price set)
    metadata: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.stage} / {self.module}" if self.module else self.stage


def _stage_rows(trace) -> list[StageRow]:
    """
    Roll one trace's costed observations up to the stage span directly beneath the root.

    The stage is the LAST node before the root, not the root itself: a generation sits under
    `module_reconcile`, which sits under `mom_generation`, which has no parent. Several stage
    spans may share a name (one `module_reconcile` per module); they are told apart by the
    `module` metadata and become separate rows.
    """
    obs = list(trace.observations)
    by_id = {o.id: o for o in obs}

    def stage_of(o):
        node, seen = o, set()
        while node.parent_observation_id and node.parent_observation_id in by_id:
            if node.id in seen:
                break
            seen.add(node.id)
            parent = by_id[node.parent_observation_id]
            if parent.parent_observation_id is None:
                return node
            node = parent
        return None

    rows: dict[str, StageRow] = {}

    def row_for(stage_obs) -> StageRow:
        key = stage_obs.id if stage_obs is not None else "(root)"
        if key not in rows:
            if stage_obs is None:
                rows[key] = StageRow(trace.name or "?", "(directly under root)")
            else:
                meta = stage_obs.metadata or {}
                rows[key] = StageRow(
                    trace.name or "?",
                    stage_obs.name or "?",
                    module=str(meta.get("module") or ""),
                    seconds=stage_obs.latency or 0.0,
                    metadata=dict(meta),
                )
        return rows[key]

    # Every stage span gets a row even with no LLM calls inside — a zero row is information.
    for o in obs:
        if o.parent_observation_id and o.parent_observation_id in by_id \
                and by_id[o.parent_observation_id].parent_observation_id is None \
                and (o.type or "").upper() not in _COSTED_TYPES:
            row_for(o)

    for o in obs:
        if (o.type or "").upper() not in _COSTED_TYPES:
            continue
        stage = stage_of(o)
        # A generation that IS the stage (sits directly under the root) has no span above it.
        if stage is not None and stage.id == o.id:
            stage = None
        row = row_for(stage)
        row.cost += o.calculated_total_cost or 0.0
        row.calls += 1
        row.tokens += (o.usage.total or 0) if o.usage else 0
        # Cost is computed by Langfuse from a per-model price list. A model missing from that
        # list (a new model id, or a dated snapshot like gpt-5.5-2026-04-23) records tokens but
        # no cost, and the total would quietly come out too low. Counted so the table can say so.
        if o.calculated_total_cost is None and (o.usage.total if o.usage else 0):
            row.unpriced += 1

    return list(rows.values())


def _print_table(title: str, meta: dict, rows: list[StageRow], wall_seconds: float) -> None:
    width = 88
    print("=" * width)
    print(title)
    for key in ("session_name", "transcript_id", "customer_id"):
        if meta.get(key):
            print(f"   {key:<16}{meta[key]}")
    print("=" * width)

    total = sum(r.cost for r in rows)

    # 1. High-Level Executive Rollup
    categories = [
        ("Minutes Generation (MOM)", lambda r: r.stage == "minutes_generate"),
        ("Module Classification", lambda r: r.stage == "module_classify"),
        ("Module Reconciliation", lambda r: r.stage in ("module_reconcile", "module_auto_backfill")),
        ("Module Vector Indexing", lambda r: r.stage == "module_index"),
        ("Transcript Ingestion", lambda r: r.stage in ("parse", "extract", "compare")),
    ]

    seen_row_ids = set()
    rollup_data = []
    for cat_name, matcher in categories:
        matched = [r for r in rows if matcher(r)]
        if not matched:
            continue
        for r in matched:
            seen_row_ids.add(id(r))
        cat_cost = sum(r.cost for r in matched)
        cat_tokens = sum(r.tokens for r in matched)
        cat_calls = sum(r.calls for r in matched)
        cat_time = sum(r.seconds for r in matched)
        # If multiple modules, note how many
        mods = {r.module for r in matched if r.module}
        label = f"{cat_name} ({len(mods)} modules)" if len(mods) > 1 else cat_name
        rollup_data.append({
            "label": label,
            "cost": cat_cost,
            "share": (cat_cost / total * 100) if total else 0.0,
            "calls": cat_calls,
            "tokens": cat_tokens,
            "time": cat_time,
        })

    other_rows = [r for r in rows if id(r) not in seen_row_ids and (r.cost > 0 or r.tokens > 0)]
    if other_rows:
        cat_cost = sum(r.cost for r in other_rows)
        cat_tokens = sum(r.tokens for r in other_rows)
        cat_calls = sum(r.calls for r in other_rows)
        cat_time = sum(r.seconds for r in other_rows)
        rollup_data.append({
            "label": "Other Stages / Overhead",
            "cost": cat_cost,
            "share": (cat_cost / total * 100) if total else 0.0,
            "calls": cat_calls,
            "tokens": cat_tokens,
            "time": cat_time,
        })

    print(f"{'HIGH-LEVEL PIPELINE ROLLUP':<48}{'COST':>11}{'SHARE':>7}{'CALLS':>6}{'TOKENS':>9}{'TIME':>7}")
    print("-" * width)
    for rd in rollup_data:
        t_str = f"{rd['time']:.0f}s" if rd['time'] else "-"
        print(f"  {rd['label']:<46}{_money(rd['cost']):>11}{rd['share']:>6.1f}%{rd['calls']:>6}{rd['tokens']:>9}{t_str:>7}")
    print("-" * width)
    print(f"  {'TOTAL':<46}{_money(total):>11}{'100.0%':>7}{sum(r.calls for r in rows):>6}"
          f"{sum(r.tokens for r in rows):>9}{f'{wall_seconds:.0f}s':>7}")
    print()

    # 2. Detailed Per-Stage Breakdown
    print(f"{'DETAILED STAGE BREAKDOWN':<48}{'COST':>11}{'SHARE':>7}{'CALLS':>6}{'TOKENS':>9}{'TIME':>7}")
    print("-" * width)

    def order(r: StageRow):
        try:
            i = _STAGE_ORDER.index(r.stage)
        except ValueError:
            i = len(_STAGE_ORDER)
        return (i, r.module)

    current_trace = None
    for r in sorted(rows, key=lambda r: (r.trace_name, order(r))):
        if r.trace_name != current_trace:
            current_trace = r.trace_name
            print(f"[{current_trace}]")
        share = (r.cost / total * 100) if total else 0
        label = r.label[:46]
        print(f"  {label:<46}{_money(r.cost):>11}{share:>6.1f}%{r.calls:>6}{r.tokens:>9}"
              f"{(f'{r.seconds:.0f}s' if r.seconds else '-'):>7}"
              + (f"   ({r.unpriced} unpriced)" if r.unpriced else ""))
    print("-" * width)
    print(f"{'TOTAL':<48}{_money(total):>11}{'':>7}{sum(r.calls for r in rows):>6}"
          f"{sum(r.tokens for r in rows):>9}{f'{wall_seconds:.0f}s':>7}")
    unpriced = sum(r.unpriced for r in rows)
    if unpriced:
        print(f"\nWARNING: {unpriced} call(s) have tokens but NO cost — their model has no price in "
              f"Langfuse, so TOTAL is too low.")
        print("         Add the model under Langfuse > Settings > Models and re-run; the cost is")
        print("         recomputed from the stored tokens.")


def _print_reconcile_summary(rows: list[StageRow]) -> None:
    """The split that says whether change detection and candidate pre-filtering are working."""
    rec = [r for r in rows if r.stage in ("module_reconcile", "module_auto_backfill")]
    if not rec:
        return
    width = 88
    print()
    print(f"{'RECONCILE':<31}{'FILTERED':>10}{'IN':>5}{'ADD':>5}{'MOD':>5}{'DEP':>5}{'SKIP':>5}{'DROP':>5}{'CHARS IN':>9}{'OUT':>8}")
    print("-" * width)
    tot = defaultdict(int)
    for r in sorted(rec, key=lambda r: -r.cost):
        m = r.metadata
        vals = {k: int(m.get(k) or 0) for k in
                ("bullets_in", "added", "modified", "deprecated", "skipped",
                 "task_bullets_dropped", "document_chars_in", "document_chars_out")}
        for k, v in vals.items():
            tot[k] += v

        # Candidate pre-filtering info (candidates_sent / existing_requirements)
        cand = m.get("candidates_sent")
        exist = m.get("existing_requirements")
        if cand is not None and exist is not None:
            filter_str = f"{cand}/{exist}"
        elif exist:
            filter_str = f"-/{exist}"
        else:
            filter_str = "-"

        mod_title = r.module
        if r.stage == "module_auto_backfill":
            mod_title += " (bf)"
        flag = "  FAILED" if m.get("error") else ""
        print(f"{mod_title[:30]:<31}{filter_str:>10}{vals['bullets_in']:>5}{vals['added']:>5}{vals['modified']:>5}"
              f"{vals['deprecated']:>5}{vals['skipped']:>5}{vals['task_bullets_dropped']:>5}{vals['document_chars_in']:>9,}"
              f"{vals['document_chars_out']:>8,}{flag}")
    print("-" * width)
    print(f"{'all modules':<31}{'':>10}{tot['bullets_in']:>5}{tot['added']:>5}{tot['modified']:>5}"
          f"{tot['deprecated']:>5}{tot['skipped']:>5}{tot['task_bullets_dropped']:>5}")
    changes = tot["added"] + tot["modified"] + tot["deprecated"]
    if changes:
        print(f"\nmodification rate: {tot['modified']}/{changes} = "
              f"{tot['modified'] / changes * 100:.1f}% of recorded changes")
        if tot["added"] and tot["modified"] / changes < 0.05 and tot["document_chars_in"] > 5000:
            print("  NOTE: almost everything was ADDED against documents that already have content.")
            print("        On a call covering familiar topics that suggests the model is adding")
            print("        near-duplicates instead of modifying — check the new revisions.")
    largest = max(rec, key=lambda r: int(r.metadata.get("document_chars_out") or 0), default=None)
    if largest and int(largest.metadata.get("document_chars_out") or 0) > 40_000:
        print(f"  NOTE: '{largest.module}' is {int(largest.metadata['document_chars_out']):,} chars "
              f"and is sent in full every meeting — consider splitting it at ~60,000.")


def report_traces(traces, title: str) -> None:
    rows: list[StageRow] = []
    wall = 0.0
    meta: dict = {}
    for t in traces:
        rows.extend(_stage_rows(t))
        wall += t.latency or 0.0
        for key in ("session_name", "transcript_id", "customer_id"):
            if (t.metadata or {}).get(key) and key not in meta:
                meta[key] = t.metadata[key]
        # Expected stages that never opened (flag off, or the stage failed before its span)
        # are shown as zero rows so the saving is visible.
        present = {r.stage for r in rows if r.trace_name == t.name}
        for stage in _EXPECTED_STAGES.get(t.name or "", []):
            if stage not in present:
                rows.append(StageRow(t.name or "?", stage))
    _print_table(title, meta, rows, wall)
    _print_reconcile_summary(rows)


def _full(lf, trace_id: str):
    return lf.api.trace.get(trace_id)


def report_session(lf, session_id: str) -> None:
    page = lf.api.trace.list(session_id=session_id, limit=50)
    if not page.data:
        print(f"No traces found for transcript/session {session_id!r}.")
        raise SystemExit(1)
    # Oldest first: upload, then minutes — the order the pipeline ran in.
    traces = [_full(lf, t.id) for t in sorted(page.data, key=lambda t: t.timestamp)]
    names = " + ".join(t.name or "?" for t in traces)
    report_traces(traces, f"transcript {session_id}   ({names})")


def main() -> None:
    if not TRACING_ENABLED:
        print("Tracing is disabled — nothing to report.")
        raise SystemExit(1)

    args = sys.argv[1:]
    lf = get_client()

    if "--transcript" in args:
        report_session(lf, args[args.index("--transcript") + 1])
        return

    if "--name" in args:
        name = args[args.index("--name") + 1]
        page = lf.api.trace.list(name=name, limit=1)
        if not page.data:
            print(f"No trace named {name!r} found yet.")
            raise SystemExit(1)
        t = _full(lf, page.data[0].id)
        report_traces([t], f"{t.name}   {t.id}")
        return

    if args:
        t = _full(lf, args[0])
        report_traces([t], f"{t.name}   {t.id}")
        return

    # Default: the newest transcript. Found through its minutes trace (the last step), then
    # widened to the whole session so the upload trace is included.
    page = lf.api.trace.list(name="mom_generation", limit=1)
    if not page.data:
        page = lf.api.trace.list(name="transcript_upload", limit=1)
    if not page.data:
        print("No transcript traces found yet. Upload a transcript first.")
        raise SystemExit(1)
    newest = page.data[0]
    if newest.session_id:
        report_session(lf, newest.session_id)
    else:
        # Recorded before sessions were added — report it alone.
        t = _full(lf, newest.id)
        report_traces([t], f"{t.name}   {t.id}   (no session — traced before session linking)")


if __name__ == "__main__":
    main()
