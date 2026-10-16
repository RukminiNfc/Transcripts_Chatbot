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


module_reconciler = ModuleReconciler()
