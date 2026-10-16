"""Assign the sections of a minutes document to the project's fixed modules.

The minutes prompt writes a heading describing what THAT meeting covered — "Mobile App Links /
Mobile 2.4 / Forced Update". Across four real transcripts that produced 117 headings, all of
them unique, for roughly 14 product areas. Filing by heading would therefore create a document
per meeting-topic rather than a document per product area, which is the opposite of the point.

So headings are not used as identity. Each section is classified into one of the customer's
existing modules, and a section that genuinely fits none of them is PROPOSED rather than filed —
see module_registry.

ONE LLM CALL PER DOCUMENT, not per section. Sections of the same transcript compete for the same
modules, and judging them together is both cheaper and more consistent than judging them in
isolation. Only the heading and the first few bullets of each section are sent: enough to decide
the subject, without paying to ship a 300-bullet document.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.config import settings
from app.core.observability import TracedOpenAI as OpenAI

logger = logging.getLogger(__name__)

# Trailing sections of a MOM that summarise the meeting rather than describing the product.
# They repeat content already captured above, so filing them would duplicate requirements under
# whichever module the roll-up happened to mention.
_APPENDIX = re.compile(
    r"^(consolidated action items|team priorities|open items|"
    r"open items\s*/\s*to be confirmed)\b",
    re.IGNORECASE,
)

# How much of each section the classifier sees. The subject is almost always clear from the
# heading plus a couple of bullets; sending more is paying for tokens that change nothing.
_BULLETS_FOR_CONTEXT = 3


def parse_sections(markdown: str) -> list[dict[str, Any]]:
    """
    Split a minutes document into its `##` sections.

    Returns [{"heading", "sub_headings": [...], "bullets": [...]}]. Appendix sections are
    dropped. `###` sub-headings are kept because they become the structure inside a module
    document, and bullets are kept flat per section since that is all the classifier needs.
    """
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw in (markdown or "").split("\n"):
        line = raw.strip()

        # `^##\s` cannot match a `###` line — the third character is '#', not whitespace.
        if m := re.match(r"^##\s+(?!#)(.+?)\s*$", line):
            name = m.group(1).strip()
            current = None if _APPENDIX.match(name) else {
                "heading": name, "sub_headings": [], "bullets": [],
            }
            if current is not None:
                sections.append(current)
            continue

        if current is None:
            continue

        if m := re.match(r"^###\s+(.+?)\s*$", line):
            current["sub_headings"].append(m.group(1).strip())
        elif m := re.match(r"^[-*+]\s+(.+?)\s*$", line):
            current["bullets"].append(m.group(1).strip())

    return sections


class ModuleClassifier:
    """Maps minutes sections onto a customer's module list."""

    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=6, timeout=180.0)
        self.model = settings.OPENAI_EXTRACTION_MODEL

    @staticmethod
    def _sections_block(sections: list[dict[str, Any]]) -> str:
        parts = []
        for i, s in enumerate(sections):
            lines = [f"[{i}] {s['heading']}"]
            if s["sub_headings"]:
                lines.append(f"    sub-headings: {'; '.join(s['sub_headings'][:4])}")
            for b in s["bullets"][:_BULLETS_FOR_CONTEXT]:
                lines.append(f"    - {b[:160]}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def classify(
        self,
        sections: list[dict[str, Any]],
        module_block: str,
    ) -> dict[str, Any]:
        """
        Decide which module each section belongs to.

        Returns {"assignments": [{"index", "module", "confidence", "reason"}],
                 "proposals":  [{"name", "description", "reason"}],
                 "error": str}

        Never raises: a failure here must not take down a transcript that has already been
        parsed and minuted. On error every section comes back unassigned, which surfaces as
        work for a human rather than as silently mis-filed requirements.
        """
        result: dict[str, Any] = {"assignments": [], "proposals": [], "error": ""}
        if not sections:
            return result

        prompt = f"""You are filing sections of a meeting's minutes into a software product's
permanent module structure. Each module is ONE living document that accumulates requirements
across many meetings, so a section must be filed by its SUBJECT, not by the wording of its
heading — headings describe a single meeting and are never reused.

EXISTING MODULES (choose from these):
{module_block}

SECTIONS TO FILE:
{self._sections_block(sections)}

Rules:
- Pick the single best module for each section. Most sections fit one of the existing modules.
- Judge by what the section is ABOUT, not by shared words. A section about notifications for
  job offers belongs with notifications, not with job offers, if its subject is the delivery.
- confidence: "high" when the subject clearly belongs to one module; "medium" when two modules
  could both defend it; "low" when it is a poor fit for all of them.
- Only propose a NEW module for a product area genuinely absent from the list, and only when
  several sections would need it. A single odd section is a low-confidence assignment, not a
  new module — a list that grows per meeting stops being a structure.

Respond with ONLY JSON:
{{"assignments":[{{"index":0,"module":"exact name from the list","confidence":"high|medium|low","reason":"short"}}],
  "proposals":[{{"name":"...","description":"one line","reason":"why nothing fits"}}]}}"""

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            result["assignments"] = data.get("assignments", []) or []
            result["proposals"] = data.get("proposals", []) or []
            logger.info(
                f"Classified {len(result['assignments'])}/{len(sections)} sections; "
                f"{len(result['proposals'])} module(s) proposed."
            )
        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"Module classification failed: {exc}")

        return result


module_classifier = ModuleClassifier()
