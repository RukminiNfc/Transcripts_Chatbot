"""Extract action items from a MOM's markdown and pre-match their owners to team subscribers.

Deterministic on purpose — no LLM call. The MOM prompt (prompts/mom_generation_prompt.txt) fixes
the bullet shape under "## Consolidated Action Items":

    ### [Area]
    - [Action] — owner: [name], due: [date if stated]

Real output also contains "owner: Naresh / QA", "due: not stated", "due: current week",
"due: one day", "due: after testing confirmation". Anything we cannot turn into a date is kept as
raw text for the admin to resolve on the review screen — never guessed.
"""
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from dateutil import parser as date_parser

# ADO's System.Title limit.
ADO_TITLE_MAX = 255

_SECTION_RE = re.compile(r"^##\s+Consolidated Action Items\s*$(.*?)(?=^##\s|\Z)", re.M | re.S | re.I)
_AREA_RE = re.compile(r"^###\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
# "<action> — owner: <owners>[, due: <due>]". Accepts em dash, en dash or hyphen before "owner:".
_ITEM_RE = re.compile(
    r"^(?P<action>.+?)\s+[—–-]+\s*owner:\s*(?P<owner>.+?)(?:\s*,\s*due:\s*(?P<due>.+?))?\s*$",
    re.I,
)
_OWNER_SPLIT_RE = re.compile(r"\s*(?:/|,|&|\band\b)\s*", re.I)

# Due values that mean "no date" rather than an unparseable one.
_NO_DUE = {"", "not stated", "not applicable", "none", "n/a", "na", "tbd", "not specified", "unspecified"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTH_WORDS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def split_owners(owner_name: Optional[str]) -> List[str]:
    if not owner_name:
        return []
    return [o for o in _OWNER_SPLIT_RE.split(owner_name.strip()) if o]


def parse_due(due_text: Optional[str], call_date: Optional[date]) -> Optional[date]:
    """Turn the MOM's due text into a date, relative to the call date. None when unsure."""
    if not due_text or not call_date:
        return None
    t = due_text.strip().lower().rstrip(".")
    if t in _NO_DUE:
        return None

    friday = call_date + timedelta(days=(4 - call_date.weekday()) % 7)  # Friday of the call's week
    if t in ("today", "eod", "end of day", "same day"):
        return call_date
    if t in ("tomorrow", "one day", "1 day", "next day"):
        return call_date + timedelta(days=1)
    if t in ("this week", "current week", "end of week", "end of the week", "eow", "by end of week"):
        return friday
    if t in ("next week", "end of next week"):
        return friday + timedelta(days=7)
    if t in ("end of month", "end of the month", "eom"):
        nxt = (call_date.replace(day=28) + timedelta(days=4))
        return nxt - timedelta(days=nxt.day)

    m = re.fullmatch(r"(?:in\s+)?(\d{1,2})\s+(?:working\s+|business\s+)?days?", t)
    if m:
        return call_date + timedelta(days=int(m.group(1)))

    # "by Friday" / "this Friday" = the next Friday after the call. "next Friday" is deliberately
    # NOT handled — people use it for both the coming one and the one after, so the admin decides.
    m = re.fullmatch(r"(?:by\s+|this\s+|on\s+)?(" + "|".join(_WEEKDAYS) + r")", t)
    if m:
        ahead = (_WEEKDAYS.index(m.group(1)) - call_date.weekday()) % 7 or 7
        return call_date + timedelta(days=ahead)

    # Explicit dates ("5 Oct", "2026-10-05", "10/05"). Require a digit AND (a month word or a
    # date-like separator) so free text such as "after build completion" is never parsed.
    if re.search(r"\d", t) and (any(w in t for w in _MONTH_WORDS) or re.search(r"\d[/\-.]\d", t)):
        try:
            default = datetime(call_date.year, call_date.month, call_date.day)
            return date_parser.parse(t, default=default, fuzzy=False).date()
        except (ValueError, OverflowError):
            return None
    return None


def parse_action_items(markdown: Optional[str], call_date: Optional[date]) -> List[Dict]:
    """Return one dict per bullet in the Consolidated Action Items section (in document order)."""
    if not markdown:
        return []
    section = _SECTION_RE.search(markdown)
    if not section:
        return []

    items, area = [], None
    for line in section.group(1).splitlines():
        heading = _AREA_RE.match(line)
        if heading:
            area = heading.group(1)
            continue
        bullet = _BULLET_RE.match(line)
        if not bullet:
            continue

        text = bullet.group(1)
        m = _ITEM_RE.match(text)
        action, owner, due_text = (m.group("action"), m.group("owner"), m.group("due")) if m else (text, None, None)
        action = action.strip().rstrip(".").strip() + "."
        if due_text and due_text.strip().lower().rstrip(".") in _NO_DUE:
            due_text = None

        title = action if len(action) <= ADO_TITLE_MAX else action[: ADO_TITLE_MAX - 1].rstrip() + "…"
        items.append({
            "area": area,
            "title": title,
            "description": action,
            "owner_name": owner.strip() if owner else None,
            "due_text": due_text.strip() if due_text else None,
            "due_date": parse_due(due_text, call_date),
        })
    return items


def match_assignee(owner_name: Optional[str], subscribers: List[Dict]) -> Optional[str]:
    """Email of the subscriber matching the item's owner, or None.

    `subscribers` = [{"name": ..., "email": ...}]. Owners are tried in the order the MOM lists
    them ("Naresh / QA" → "Naresh" first). A name only counts when it matches exactly ONE
    subscriber — full name first, then first name — so an ambiguous "Amol" never picks at random.
    """
    for owner in split_owners(owner_name):
        o = owner.strip().lower()
        full = {s["email"] for s in subscribers if (s.get("name") or "").strip().lower() == o}
        if len(full) == 1:
            return full.pop()
        first = {
            s["email"] for s in subscribers
            if (s.get("name") or "").strip() and (s["name"].split()[0].lower() == o.split()[0])
        }
        if len(first) == 1:
            return first.pop()
    return None
