"""
READ-ONLY test for the MOM action-item parser and owner matcher. Makes NO changes and costs
NOTHING — no LLM calls, no database, no network, no Azure DevOps. Runs in under a second.

WHAT IT PROVES
--------------
  parse_action_items   only "## Consolidated Action Items" is read; area headings, owners and due
                       text come out of every bullet shape the MOM prompt actually produces
  parse_due            relative due text resolves against the CALL date; anything ambiguous
                       ("after testing confirmation", "next Friday") stays None, never guessed
  match_assignee       a name only matches when exactly one subscriber fits

Usage (from the Backend/ directory):
    python test_mom_action_items.py
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.mom_action_items import match_assignee, parse_action_items, parse_due  # noqa: E402

CALL = date(2026, 9, 15)   # a Tuesday

SAMPLE = """# Meeting Requirements — 15 Sep 2026

## Credit Card

- A requirement bullet that must NOT become a task — owner: Nobody

## Open Items / To Be Confirmed

- Is CVV stored? — owner: Prasad

## Consolidated Action Items

### Exchange Server Users

- Complete dynamic exchange server user pulling and deploy after completion. — owner: Amol, due: current week
- Keep the current hard-coded logic until the dynamic pull is ready. — owner: Amol, due: not stated

### E-Profile / Migration

- Complete testing of E-Profile configuration. — owner: Naresh / QA, due: after testing confirmation
- Fix the location uppercase/lowercase issue – owner: Naresh, due: today
- Review remaining 1.0 applications - owner: naresh kumar, due: 5 Oct
- A bullet with no owner at all

## Team Priorities

### Amol

- Tokenization
"""

SUBSCRIBERS = [
    {"name": "Amol Patil", "email": "amol@example.com"},
    {"name": "Naresh Kumar", "email": "naresh@example.com"},
    {"name": "Prasad K", "email": "prasad@example.com"},
    {"name": "Ravi One", "email": "ravi1@example.com"},
    {"name": "Ravi Two", "email": "ravi2@example.com"},
]

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# ── parse_action_items ───────────────────────────────────────────────────────
items = parse_action_items(SAMPLE, CALL)
check("item count (only Consolidated Action Items)", len(items), 6)
if len(items) == 6:
    a, b, c, d, e, f = items
    check("area from ### heading", a["area"], "Exchange Server Users")
    check("owner", a["owner_name"], "Amol")
    check("due text kept", a["due_text"], "current week")
    check("current week -> Friday of call week", a["due_date"], date(2026, 9, 18))
    check("'not stated' -> no due text", b["due_text"], None)
    check("'not stated' -> no due date", b["due_date"], None)
    check("area changes with heading", c["area"], "E-Profile / Migration")
    check("multi-owner kept raw", c["owner_name"], "Naresh / QA")
    check("unparseable due -> None", c["due_date"], None)
    check("unparseable due text kept", c["due_text"], "after testing confirmation")
    check("en dash separator", d["owner_name"], "Naresh")
    check("today -> call date", d["due_date"], CALL)
    check("hyphen separator", e["owner_name"], "naresh kumar")
    check("explicit date uses call year", e["due_date"], date(2026, 10, 5))
    check("no owner -> None", f["owner_name"], None)
    check("no owner -> whole bullet is title", f["title"], "A bullet with no owner at all.")

check("no section -> []", parse_action_items("# Title\n\n## Other\n- x — owner: y", CALL), [])
check("empty markdown -> []", parse_action_items(None, CALL), [])
check("empty section -> []", parse_action_items("## Consolidated Action Items\n\n## Team Priorities\n", CALL), [])

long_title = parse_action_items("## Consolidated Action Items\n- " + "x" * 400 + " — owner: Amol", CALL)[0]
check("title capped at 255", len(long_title["title"]), 255)
check("full text kept in description", len(long_title["description"]), 401)

# ── parse_due ────────────────────────────────────────────────────────────────
for text, want in [
    ("one day", date(2026, 9, 16)),
    ("tomorrow", date(2026, 9, 16)),
    ("EOD", CALL),
    ("next week", date(2026, 9, 25)),
    ("by Friday", date(2026, 9, 18)),
    ("Tuesday", date(2026, 9, 22)),          # same weekday as the call -> the following one
    ("3 days", date(2026, 9, 18)),
    ("end of month", date(2026, 9, 30)),
    ("2026-10-05", date(2026, 10, 5)),
    ("next Friday", None),                    # ambiguous on purpose
    ("after build completion", None),
    ("today as stated for Ismail issues", None),
    ("TBD", None),
]:
    check(f"parse_due({text!r})", parse_due(text, CALL), want)
check("parse_due without call date", parse_due("today", None), None)

# ── match_assignee ───────────────────────────────────────────────────────────
check("first name, unique", match_assignee("Amol", SUBSCRIBERS), "amol@example.com")
check("full name, case-insensitive", match_assignee("naresh kumar", SUBSCRIBERS), "naresh@example.com")
check("first of multiple owners", match_assignee("Naresh / QA", SUBSCRIBERS), "naresh@example.com")
check("skips unknown owner, takes next", match_assignee("QA / Prasad", SUBSCRIBERS), "prasad@example.com")
check("ambiguous first name -> None", match_assignee("Ravi", SUBSCRIBERS), None)
check("full name disambiguates", match_assignee("Ravi Two", SUBSCRIBERS), "ravi2@example.com")
check("unknown -> None", match_assignee("Ismail", SUBSCRIBERS), None)
check("no owner -> None", match_assignee(None, SUBSCRIBERS), None)

if failures:
    print(f"FAIL — {len(failures)} check(s):")
    for f_ in failures:
        print("  -", f_)
    sys.exit(1)
print("PASS — all action-item parser / matcher checks")
