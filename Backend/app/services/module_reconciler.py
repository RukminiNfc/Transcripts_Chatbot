"""Fold a meeting's requirements into a module's living document.

This replaces requirement-by-requirement comparison. The old path embedded each extracted
requirement, searched Qdrant for the five nearest, then asked an LLM whether they matched and
whether anything had changed — roughly 113 calls and $1.73 per transcript, with change detection
spread across three gated calls whose combined effect was hard to reason about.

Here the unit of work is the module, not the requirement: one call per touched module receives
the document and the new bullets and returns the sub-sections it changed. There are usually
three to five touched modules in a transcript.

READ THE WHOLE DOCUMENT, WRITE ONLY WHAT CHANGED
------------------------------------------------
The model is given the document in full — that context is why it can tell "the same rule, with
more detail" from "a different rule", which is the judgement the whole design rests on. But it
returns only the "### " sub-sections it edited, and those are spliced back here.

Three things follow, and the third is the one that matters:

    cost and output size stop growing with the document, so a module cannot grow its way into
    the token cap and start having its revisions rejected;

    truncation stops being a live risk, because the response is proportional to the change
    rather than to the document;

    untouched text CANNOT drift. Rewriting the whole document every meeting meant the model
    retyped bullets it had no reason to touch, and it quietly improved their wording as it
    went — a re-run of the same meeting produced changes that should not have existed. Text
    that is never sent back cannot be reworded.

WHY CHANGE DETECTION GETS BETTER, NOT JUST CHEAPER
--------------------------------------------------
Deciding "has this changed?" in isolation is genuinely hard — two requirement statements out of
context, judged by a model that cannot see what else the document says. Here the model is
editing a document it can see in full, so "is this the same rule as one already written down?"
is answered with the rest of the specification in front of it. The diffs are a byproduct of
making the edit rather than a separate judgement about it.

FAILURE BEHAVIOUR
-----------------
Never raises, and never returns a partially-edited document. On any failure the caller keeps the
previous revision, which leaves the module one meeting behind — recoverable by re-running, and
far better than a document silently missing half its requirements.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings
from app.core.observability import TracedOpenAI as OpenAI

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_PROMPT_FILE = "module_reconcile_prompt.txt"
# Block-level reconciliation: one MOM sub-section = one requirement. See reconcile_blocks().
_BLOCKS_PROMPT_FILE = "module_reconcile_blocks_prompt.txt"
_BLOCKS_MAX_TOKENS = 12000
_ACTIONS = {"ADD", "MODIFY", "DEPRECATE", "SKIP"}
_GLOSSARY_PATH = _PROMPTS_DIR / "glossary.txt"

# The response carries only the changed sub-sections, so this covers the largest plausible single
# revision rather than the whole document. Kept generous: the cost of an unused cap is nothing,
# and the cost of hitting it is a rejected revision.
_MAX_TOKENS = 16000

# Input-side warning only. The document is still READ in full — that is deliberate (see the module
# docstring) — so a very large one costs more to send even though the response stays small.
_LARGE_DOCUMENT_CHARS = 60_000

# "### Applicant Source" -> the splice unit. "## " nodes are parsed too so that a section heading
# sitting between two sub-sections keeps its place when the document is rebuilt.
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+\S")


def _count_bullets(text: str) -> int:
    return sum(1 for line in (text or "").split("\n") if _BULLET.match(line))


def _norm_heading(heading: str) -> str:
    """Match headings that differ only in case, punctuation or spacing — not in meaning."""
    return re.sub(r"[^a-z0-9]+", "", (heading or "").lower())


def _split_sections(document: str) -> tuple[str, list[dict[str, Any]]]:
    """
    A document into (preamble, nodes).

    The preamble is everything before the first "## "/"### " heading — the "# Title" line and the
    module description. Each node keeps its own level so rebuilding restores the original shape.
    """
    preamble: list[str] = []
    nodes: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None

    for raw in (document or "").split("\n"):
        match = _HEADING.match(raw)
        if match:
            current = {"level": len(match.group(1)), "heading": match.group(2).strip(), "body": []}
            nodes.append(current)
            continue
        (current["body"] if current else preamble).append(raw)

    return "\n".join(preamble).rstrip(), nodes


def _rebuild(preamble: str, nodes: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if preamble.strip():
        parts.extend([preamble.rstrip(), ""])
    for node in nodes:
        parts.append(f"{'#' * node['level']} {node['heading']}")
        body = "\n".join(node["body"]).strip("\n")
        if body:
            parts.append(body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _apply_sections(
    document: str,
    sections: list[dict[str, Any]],
    module_name: str,
) -> tuple[str, int, int]:
    """
    Splice returned sub-sections into the document.

    Returns (new_document, replaced, appended). A heading that matches nothing is appended as a
    new sub-section rather than dropped — a stray sub-section is visible and fixable, whereas a
    silently discarded one loses the meeting's requirements.
    """
    preamble, nodes = _split_sections(document)
    index = {}
    for position, node in enumerate(nodes):
        if node["level"] == 3:
            index.setdefault(_norm_heading(node["heading"]), position)

    replaced = appended = 0

    for section in sections:
        heading = (section.get("heading") or "").strip().lstrip("#").strip()
        content = (section.get("content") or "").strip("\n")
        if not heading or not content:
            continue

        position = index.get(_norm_heading(heading))
        if position is None:
            nodes.append({"level": 3, "heading": heading, "body": content.split("\n")})
            index[_norm_heading(heading)] = len(nodes) - 1
            appended += 1
            if (section.get("action") or "").upper() == "REPLACE":
                # The model meant to edit something that is not there under that name.
                logger.warning(
                    f"{module_name}: '{heading}' was returned as REPLACE but matches no existing "
                    f"sub-heading — appended as a new sub-section."
                )
        else:
            nodes[position]["body"] = content.split("\n")
            replaced += 1

    return _rebuild(preamble, nodes), replaced, appended


class ModuleReconciler:
    """One LLM call per module: current document + new requirements -> updated document."""

    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=6, timeout=300.0)
        self.model = settings.OPENAI_COMPARISON_MODEL
        self._template = self._load(_PROMPT_FILE)
        self._blocks_template = self._load(_BLOCKS_PROMPT_FILE)

    @staticmethod
    def _load(filename: str) -> str:
        path = _PROMPTS_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        logger.error(f"Prompt file {filename} not found!")
        return ""

    @staticmethod
    def _glossary() -> str:
        """
        Canonical spellings, shared with chat and minutes.

        Read per call so a term added after a mis-transcription is noticed takes effect on the
        next run without restarting the worker.
        """
        try:
            if not _GLOSSARY_PATH.exists():
                return ""
            lines = [
                ln.strip() for ln in _GLOSSARY_PATH.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if not lines:
                return ""
            return (
                "PROJECT TERMINOLOGY (authoritative — these spellings override whatever the "
                "transcript contained, which is speech-to-text and mis-hears them):\n"
                + "\n".join(lines)
            )
        except Exception as exc:
            logger.error(f"Could not load glossary ({exc}); reconciling without it.")
            return ""

    @staticmethod
    def _starter_document(module_name: str, description: str) -> str:
        """The skeleton a module gets before its first meeting, so there is always a document
        to edit rather than a special 'create' path to maintain separately."""
        return (
            f"# {module_name}\n\n"
            f"{description}\n\n"
            f"## Requirements\n"
        )

    @staticmethod
    def _requirements_block(sections: list[dict[str, Any]]) -> str:
        """Today's requirements for this module, keeping the sub-headings they arrived under."""
        parts: list[str] = []
        for s in sections:
            parts.append(f"From: {s.get('heading', '(no heading)')}")
            subs = s.get("sub_headings") or []
            bullets = s.get("bullets") or []
            if subs:
                parts.append(f"  sub-headings: {'; '.join(subs)}")
            for b in bullets:
                parts.append(f"  - {b}")
            parts.append("")
        return "\n".join(parts).strip()

    def reconcile(
        self,
        module_name: str,
        module_description: str,
        current_document: Optional[str],
        sections: list[dict[str, Any]],
        session_name: str,
        call_date: str,
    ) -> dict[str, Any]:
        """
        Update one module's document with one meeting's requirements.

        Returns {"document": str, "changed": bool, "summary": str, "diffs": [...], "error": str}.
        `document` is empty on failure, which the caller must treat as "leave the module alone".
        `changed` is False when the meeting restated what the document already said — a correct
        and common outcome, and the caller should record no revision for it.
        """
        result: dict[str, Any] = {
            "document": "", "changed": False, "summary": "", "diffs": [], "error": "",
        }

        if not sections:
            result["error"] = "No requirements for this module."
            return result
        if not self._template:
            result["error"] = f"Prompt file {_PROMPT_FILE} is missing or empty."
            logger.error(result["error"])
            return result

        document = current_document or self._starter_document(module_name, module_description)

        if len(document) > _LARGE_DOCUMENT_CHARS:
            # Only the input side grows now. Splitting a module in two is the real answer at this
            # size — a specification this long is hard for a person to read as one document too.
            logger.warning(
                f"Module '{module_name}' is {len(document):,} chars and is sent in full on every "
                f"meeting — consider splitting it into two modules."
            )

        prompt = (
            self._template
            .replace("{{MODULE_NAME}}", module_name)
            .replace("{{MODULE_DESCRIPTION}}", module_description or "")
            .replace("{{CURRENT_DOCUMENT}}", document)
            .replace("{{NEW_REQUIREMENTS}}", self._requirements_block(sections))
            .replace("{{SESSION_NAME}}", session_name or "")
            .replace("{{CALL_DATE}}", call_date or "")
            .replace("{{GLOSSARY}}", self._glossary())
        )

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=_MAX_TOKENS,
                response_format={"type": "json_object"},
            )
            choice = resp.choices[0]
            data = json.loads(choice.message.content)

            # A truncated response stops mid-way, so the last sub-section it describes is
            # incomplete and splicing it in would delete the bullets after the cut. Refuse the
            # whole revision — the module keeps its previous one and a re-run recovers it.
            if getattr(choice, "finish_reason", None) == "length":
                result["error"] = (
                    f"Response hit the {_MAX_TOKENS}-token cap — the revision would be "
                    f"incomplete. Revision rejected."
                )
                logger.error(f"{module_name}: {result['error']}")
                return result

            summary = (data.get("revision_summary") or "").strip()
            diffs = data.get("diffs") or []
            changed_sections = data.get("sections") or []

            if changed_sections:
                updated, replaced, appended = _apply_sections(
                    document, changed_sections, module_name
                )
            elif (data.get("updated_document") or "").strip():
                # The model answered in the whole-document shape instead. Accepted rather than
                # rejected — the content is right even though the form is not — but logged,
                # because every such response reintroduces the drift this change removed.
                updated = data["updated_document"].strip()
                replaced = appended = 0
                logger.warning(
                    f"{module_name}: model returned a whole document instead of changed "
                    f"sub-sections; accepted, but untouched text may have been reworded."
                )
            else:
                # Nothing to change. Not a failure: most meetings leave most modules alone, and
                # recording a revision for it would fill the history with empty entries.
                result["document"] = document
                result["summary"] = summary or "No changes — the meeting restated existing rules."
                logger.info(f"{module_name}: no changes.")
                return result

            if len(updated) < 20:
                result["error"] = "Model returned an empty document."
                logger.error(f"{module_name}: {result['error']}")
                return result

            # A requirement can only ever be added, reworded in place, or struck through — never
            # removed — so the bullet count cannot legitimately fall. If it has, the splice lost
            # something, and keeping the previous revision is the safe outcome.
            before, after = _count_bullets(document), _count_bullets(updated)
            if after < before:
                result["error"] = (
                    f"Revision would drop {before - after} requirement(s) "
                    f"({before} -> {after}). Rejected."
                )
                logger.error(f"{module_name}: {result['error']}")
                return result

            result["document"] = updated
            result["changed"] = updated.strip() != document.strip()
            result["summary"] = summary
            result["diffs"] = diffs

            logger.info(
                f"{module_name}: {len(diffs)} change(s), {replaced} sub-section(s) replaced, "
                f"{appended} added, {before} -> {after} bullets — {summary[:60]}"
            )
        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"Reconciliation failed for '{module_name}': {exc}")

        return result


    # ── block-level reconciliation: one MOM sub-section = one requirement ─────────────────────

    @staticmethod
    def _numbered_blocks(prefix: str, blocks: list[dict[str, Any]], number_bullets: bool) -> str:
        parts: list[str] = []
        for i, block in enumerate(blocks, start=1):
            parts.append(f"{prefix}{i}. {block['title']}")
            for j, bullet in enumerate(block.get("bullets") or [], start=1):
                parts.append(f"   [{j}] {bullet}" if number_bullets else f"   - {bullet}")
            parts.append("")
        return "\n".join(parts).strip() or "(none yet — this module has no requirements)"

    @staticmethod
    def _is_next_gen(model: str) -> bool:
        return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))

    def reconcile_blocks(
        self,
        module_name: str,
        module_description: str,
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
        session_name: str,
        call_date: str,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Decide what each of a meeting's requirement blocks does to one module.

        `existing` is the module's current blocks (requirement_blocks.parse_document_blocks),
        deprecated ones excluded by the caller. `incoming` is this meeting's blocks for the module
        (requirement_blocks.build_section_requirements). Both use {"title", "heading_key", "bullets"}.

        Returns {"decisions": [...], "summary": str, "error": str}. Each decision is one of:

            {"action": "ADD", "incoming_keys": [...], "title", "bullets", "dropped": [...]}
            {"action": "MODIFY", "incoming_keys": [...],
             "updates": [{"target_key", "bullets"}, ...], "dropped": [...]}
            {"action": "DEPRECATE", "incoming_keys": [...], "target_key", "reason"}
            {"action": "SKIP", "incoming_keys": [...], "reason", "target_key" | None}

        ADD bullets are the incoming bullets verbatim minus the ones the model marked as tasks —
        the model never rewrites an added requirement. Only MODIFY carries model-written text.

        Every incoming block is accounted for: one the model left out is ADDED with a warning
        rather than lost, and a MODIFY/DEPRECATE naming an unknown target is downgraded to ADD.
        Never raises; on a failed call `error` is set and `decisions` is empty, which the caller
        must treat as "leave the module alone".
        """
        result: dict[str, Any] = {"decisions": [], "summary": "", "error": ""}
        if not incoming:
            result["error"] = "No requirements for this module."
            return result
        if not self._blocks_template:
            result["error"] = f"Prompt file {_BLOCKS_PROMPT_FILE} is missing or empty."
            logger.error(result["error"])
            return result

        new_ids = {f"N{i}": b for i, b in enumerate(incoming, start=1)}
        old_ids = {f"E{i}": b for i, b in enumerate(existing, start=1)}

        prompt = (
            self._blocks_template
            .replace("{{MODULE_NAME}}", module_name)
            .replace("{{MODULE_DESCRIPTION}}", module_description or "")
            .replace("{{EXISTING_REQUIREMENTS}}", self._numbered_blocks("E", existing, False))
            .replace("{{NEW_REQUIREMENTS}}", self._numbered_blocks("N", incoming, True))
            .replace("{{SESSION_NAME}}", session_name or "")
            .replace("{{CALL_DATE}}", call_date or "")
            .replace("{{GLOSSARY}}", self._glossary())
        )

        target_model = model or self.model
        try:
            kwargs: dict[str, Any] = {
                "model": target_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
            if self._is_next_gen(target_model):
                kwargs["max_completion_tokens"] = _BLOCKS_MAX_TOKENS
            else:
                kwargs["temperature"] = 0.0
                kwargs["max_tokens"] = _BLOCKS_MAX_TOKENS

            resp = self.client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                result["error"] = (f"Response hit the {_BLOCKS_MAX_TOKENS}-token cap — decisions "
                                   f"would be incomplete. Rejected.")
                logger.error(f"{module_name}: {result['error']}")
                return result
            data = json.loads(choice.message.content)
        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"Block reconciliation failed for '{module_name}': {exc}")
            return result

        result["summary"] = (data.get("revision_summary") or "").strip()
        decisions = self._normalize_decisions(module_name, data.get("decisions") or [],
                                              new_ids, old_ids)
        result["decisions"] = decisions

        counts = {a: sum(1 for d in decisions if d["action"] == a) for a in _ACTIONS}
        dropped = sum(len(d.get("dropped") or []) for d in decisions)
        logger.info(
            f"{module_name}: {len(incoming)} incoming -> ADD {counts['ADD']}, MODIFY "
            f"{counts['MODIFY']}, DEPRECATE {counts['DEPRECATE']}, SKIP {counts['SKIP']}; "
            f"{dropped} task bullet(s) dropped"
        )
        return result

    @staticmethod
    def _task_indexes(spec: Any, ids: list[str]) -> dict[str, set[int]]:
        """'task_bullets' as {new_id: {1-based bullet numbers}}. Accepts 3 or "N2.3"."""
        out: dict[str, set[int]] = {i: set() for i in ids}
        for item in spec or []:
            text = str(item).strip()
            if m := re.fullmatch(r"(N\d+)\.(\d+)", text):
                if m.group(1) in out:
                    out[m.group(1)].add(int(m.group(2)))
            elif text.isdigit() and ids:
                out[ids[0]].add(int(text))
        return out

    def _normalize_decisions(
        self,
        module_name: str,
        raw: list[Any],
        new_ids: dict[str, dict[str, Any]],
        old_ids: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        decisions: list[dict[str, Any]] = []
        seen: set[str] = set()
        modified_targets: set[str] = set()   # an existing requirement is updated by one MODIFY only

        def add_decision(ids: list[str], task_spec: Any) -> dict[str, Any]:
            tasks = self._task_indexes(task_spec, ids)
            bullets: list[str] = []
            dropped: list[str] = []
            for i in ids:
                for n, bullet in enumerate(new_ids[i].get("bullets") or [], start=1):
                    (dropped if n in tasks[i] else bullets).append(bullet)
            return {"action": "ADD", "incoming_keys": [new_ids[i]["heading_key"] for i in ids],
                    "title": new_ids[ids[0]]["title"], "bullets": bullets, "dropped": dropped}

        for item in raw:
            if not isinstance(item, dict):
                continue
            ids = item.get("new") or []
            ids = [ids] if isinstance(ids, str) else list(ids)
            ids = [str(i).strip() for i in ids if str(i).strip() in new_ids and str(i).strip() not in seen]
            action = str(item.get("action") or "").upper().strip()
            if not ids or action not in _ACTIONS:
                continue
            seen.update(ids)
            keys = [new_ids[i]["heading_key"] for i in ids]
            target = str(item.get("target") or "").strip()
            target_block = old_ids.get(target)

            # MODIFY may update several existing requirements. Accept the documented "updates"
            # list, and the single target + bullets form as a fallback.
            updates: list[dict[str, Any]] = []
            if action == "MODIFY":
                raw_updates = item.get("updates")
                if not raw_updates and target:
                    raw_updates = [{"target": target, "bullets": item.get("bullets")}]
                for u in raw_updates or []:
                    if not isinstance(u, dict):
                        continue
                    t = str(u.get("target") or "").strip()
                    if t not in old_ids or t in modified_targets:
                        if t not in old_ids:
                            logger.warning(f"{module_name}: MODIFY update names unknown target {t!r} — ignored.")
                        continue
                    bl = [str(b).strip().lstrip("-").strip() for b in (u.get("bullets") or [])]
                    bl = [b for b in bl if b]
                    if bl:
                        modified_targets.add(t)
                        updates.append({"target_key": old_ids[t]["heading_key"], "bullets": bl})

            if action == "MODIFY" and not updates:
                logger.warning(f"{module_name}: MODIFY for {ids} had no usable update — treated as "
                               f"ADD so nothing is lost.")
                action = "ADD"
            if action == "DEPRECATE" and target_block is None:
                logger.warning(f"{module_name}: DEPRECATE for {ids} names unknown target "
                               f"{target!r} — treated as ADD so nothing is lost.")
                action = "ADD"

            if action == "ADD" and len(ids) > 1:
                # One sub-section = one requirement. A merged ADD is split back into one ADD per
                # id; the task-bullet numbers can no longer be attributed, so each keeps all of its
                # bullets — over-keeping is visible and fixable, a wrongly dropped rule is not.
                logger.warning(f"{module_name}: ADD merged {ids} — split into {len(ids)} ADDs.")
                for i in ids:
                    decisions.append(add_decision([i], []))
                continue
            if action == "ADD":
                d = add_decision(ids, item.get("task_bullets"))
                if not d["bullets"]:
                    d = {"action": "SKIP", "incoming_keys": keys, "reason": "task_only",
                         "target_key": None, "dropped": d["dropped"]}
                decisions.append(d)
            elif action == "MODIFY":
                decisions.append({
                    "action": "MODIFY", "incoming_keys": keys, "updates": updates,
                    "dropped": [str(t) for t in (item.get("task_bullets_dropped") or [])],
                })
            elif action == "DEPRECATE":
                decisions.append({"action": "DEPRECATE", "incoming_keys": keys,
                                  "target_key": target_block["heading_key"],
                                  "reason": str(item.get("reason") or "").strip()})
            else:  # SKIP
                reason = str(item.get("reason") or "covered").strip().lower()
                decisions.append({
                    "action": "SKIP", "incoming_keys": keys,
                    "reason": "task_only" if "task" in reason else "covered",
                    "target_key": target_block["heading_key"] if target_block else None,
                    "dropped": list(new_ids[ids[0]].get("bullets") or []) if "task" in reason else [],
                })

        # Anything the model did not mention is kept, not lost.
        for i, block in new_ids.items():
            if i not in seen:
                logger.warning(f"{module_name}: no decision for {i} '{block['title'][:60]}' — ADDED.")
                decisions.append({"action": "ADD", "incoming_keys": [block["heading_key"]],
                                  "title": block["title"], "bullets": list(block.get("bullets") or []),
                                  "dropped": []})
        return decisions


module_reconciler = ModuleReconciler()
