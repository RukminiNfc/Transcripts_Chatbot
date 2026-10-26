"""
Apply a meeting's reconciliation decisions to a module document.

The model decides (module_reconciler.reconcile_blocks); this module writes. Splitting the two is
what keeps untouched requirements untouched: the model never returns the document, so it cannot
reword a requirement it was not asked about. Every byte of the output document comes either from
the existing document or from a decision about one named requirement.

DOCUMENT SHAPE
--------------
    # <Module name>

    <module description>

    ## Requirements

    ### <Topic>: <Sub-section>          <- one requirement (requirement_blocks.py)
    - detail
    - detail

    ### ~~DEPRECATED~~ <Topic>: <Sub-section>     <- withdrawn, kept for history, not indexed

    ## Action Items                     <- not requirements; carried through untouched here

Any `##` section other than Requirements is preserved verbatim, so Action Items (written by the
pipeline, not the model) survive every revision.

Pure functions, no I/O.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.requirement_blocks import (
    TITLE_SEPARATOR,
    heading_key,
    parse_document_blocks,
    render_block,
)

_H2 = re.compile(r"^##\s+(?!#)(.+?)\s*$")
_REQUIREMENTS_SECTION = re.compile(r"^requirements\b", re.IGNORECASE)
_ACTION_ITEMS_SECTION = re.compile(r"^action items\b", re.IGNORECASE)


def _topic(title: str) -> str:
    return title.split(TITLE_SEPARATOR, 1)[0].strip().lower() if TITLE_SEPARATOR in title else ""


def _as_text(bullets: list[str]) -> str:
    return "\n".join(f"- {b.strip()}" for b in bullets)


def split_document(markdown: str) -> tuple[str, list[dict[str, Any]], list[str], str]:
    """
    (preamble, requirement blocks, action items lines, other sections).

    The preamble is everything before the first `##` — the title and the module description.
    Requirement blocks come from parse_document_blocks (which skips Action Items).
    Action items lines come from the `## Action Items` section.
    Other sections are every remaining `##` section, verbatim and in order.
    """
    preamble: list[str] = []
    action_items_lines: list[str] = []
    other: list[str] = []
    zone = "preamble"
    for raw in (markdown or "").split("\n"):
        if m := _H2.match(raw.strip()):
            name = m.group(1).strip()
            if _REQUIREMENTS_SECTION.match(name):
                zone = "requirements"
            elif _ACTION_ITEMS_SECTION.match(name):
                zone = "action_items"
            else:
                zone = "other"
                other.append(raw)
            continue
        if zone == "preamble":
            preamble.append(raw)
        elif zone == "action_items":
            if raw.strip():
                action_items_lines.append(raw.strip())
        elif zone == "other":
            other.append(raw)
    return (
        "\n".join(preamble).strip(),
        parse_document_blocks(markdown),
        action_items_lines,
        "\n".join(other).strip(),
    )


def render_document(
    module_name: str,
    description: str,
    preamble: str,
    blocks: list[dict[str, Any]],
    action_items_lines: list[str],
    other_sections: str,
) -> str:
    head = preamble or f"# {module_name}\n\n{(description or '').strip()}".strip()
    parts = [head, "## Requirements"]
    parts.extend(render_block(b) for b in blocks)
    if action_items_lines:
        parts.append("## Action Items\n" + "\n".join(action_items_lines))
    if other_sections:
        parts.append(other_sections)
    return "\n\n".join(p.strip("\n") for p in parts if p is not None) + "\n"


def apply_decisions(
    document: str | None,
    module_name: str,
    description: str,
    decisions: list[dict[str, Any]],
    action_items: list[dict[str, Any]] | None = None,
    session_name: str = "",
) -> dict[str, Any]:
    """
    Returns {"document", "changed", "diffs", "touched_keys"}.

    `diffs` is the revision's change list, one entry per requirement that actually changed, in the
    shape the History tab reads ({"action", "sub_heading", "old_text", "new_text"}) plus
    "requirement_key" so the indexer knows exactly which requirements to re-version.

    A decision that would change nothing (a MODIFY that returns the same bullets, an ADD whose
    bullets are all already present) produces no diff, so "changed" is honest.
    """
    preamble, blocks, action_lines, other = split_document(document or "")
    by_key = {b["heading_key"]: b for b in blocks}
    diffs: list[dict[str, Any]] = []

    def insert_position(title: str) -> int:
        """After the last requirement of the same topic, so a topic's requirements stay together."""
        topic = _topic(title)
        if topic:
            for i in range(len(blocks) - 1, -1, -1):
                if _topic(blocks[i]["title"]) == topic:
                    return i + 1
        return len(blocks)

    def modify(block: dict[str, Any], new_bullets: list[str]) -> None:
        if [b.strip() for b in new_bullets] == [b.strip() for b in block["bullets"]] \
                and not block.get("deprecated"):
            return
        diffs.append({
            "action": "MODIFIED",
            "requirement_key": block["heading_key"],
            "sub_heading": block["title"],
            "old_text": _as_text(block["bullets"]),
            "new_text": _as_text(new_bullets),
        })
        block["bullets"] = list(new_bullets)
        block["deprecated"] = False   # modified again -> back in force

    for d in decisions:
        action = d.get("action")

        if action == "ADD":
            title = d["title"]
            key = heading_key(title)
            bullets = [b for b in (d.get("bullets") or []) if b.strip()]
            if not bullets:
                continue
            if key in by_key:
                # Same title as an existing requirement: fold in only what is new rather than
                # creating a second block with the same identity.
                block = by_key[key]
                seen = {b.strip().lower() for b in block["bullets"]}
                merged = block["bullets"] + [b for b in bullets if b.strip().lower() not in seen]
                modify(block, merged)
                continue
            block = {"title": title, "heading_key": key, "bullets": bullets, "deprecated": False}
            blocks.insert(insert_position(title), block)
            by_key[key] = block
            diffs.append({
                "action": "ADDED",
                "requirement_key": key,
                "sub_heading": title,
                "new_text": _as_text(bullets),
            })

        elif action == "MODIFY":
            for update in d.get("updates") or []:
                block = by_key.get(update.get("target_key"))
                bullets = [b for b in (update.get("bullets") or []) if b.strip()]
                if block is not None and bullets:
                    modify(block, bullets)

        elif action == "DEPRECATE":
            block = by_key.get(d.get("target_key"))
            if block is None or block.get("deprecated"):
                continue
            block["deprecated"] = True
            reason = (d.get("reason") or "").strip()
            diffs.append({
                "action": "DEPRECATED",
                "requirement_key": block["heading_key"],
                "sub_heading": f"{block['title']}" + (f" — {reason}" if reason else ""),
                "old_text": _as_text(block["bullets"]),
            })

    # --- Merge Action Items (Priority 2) ---
    def _norm_action(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", text.lower())

    seen_actions = {_norm_action(line) for line in action_lines}
    new_action_lines = list(action_lines)

    # 1. Action items from MOM's Consolidated Action Items for this module
    for item in (action_items or []):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        key = _norm_action(title)
        if key in seen_actions:
            continue
        seen_actions.add(key)

        owner = (item.get("owner_name") or "").strip()
        due = (item.get("due_text") or "").strip()
        item_sess = item.get("session_name") or session_name

        line = f"- [ ] {title}"
        if owner:
            line += f" — owner: {owner}"
        if due and due.lower() not in ("not stated", "none", "n/a", "na", "unspecified", "not specified"):
            line += f", due: {due}"
        if item_sess:
            line += f" ({item_sess})"
        new_action_lines.append(line)

    # 2. Task bullets dropped by the reconciler because they were actions/to-dos
    for d in decisions:
        for dropped in (d.get("dropped") or []):
            task_text = str(dropped).strip()
            if not task_text:
                continue
            key = _norm_action(task_text)
            if key in seen_actions:
                continue
            seen_actions.add(key)
            line = f"- [ ] {task_text}" + (f" ({session_name})" if session_name else "")
            new_action_lines.append(line)

    new_document = render_document(module_name, description, preamble, blocks, new_action_lines, other)
    has_action_item_change = len(new_action_lines) != len(action_lines)
    return {
        "document": new_document,
        "changed": (new_document.strip() != (document or "").strip()) and (bool(diffs) or has_action_item_change),
        "diffs": diffs,
        "touched_keys": [x["requirement_key"] for x in diffs],
    }
