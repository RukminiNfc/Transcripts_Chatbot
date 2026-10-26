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
from app.services.requirement_blocks import build_section_requirements, split_topics

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

    Returns [{"heading", "sub_headings": [...], "bullets": [...], "requirements": [...]}].
    Appendix sections are dropped. `sub_headings` and the flat `bullets` list are what the
    classifier reads. `requirements` is the same section as requirement blocks — one per `###`
    sub-section, titled "Topic: Sub-section" (see requirement_blocks.py) — which is the unit
    reconciliation and indexing work in.
    """
    sections: list[dict[str, Any]] = []

    for topic, lines in split_topics(markdown):
        if _APPENDIX.match(topic):
            continue
        sub_headings: list[str] = []
        bullets: list[str] = []
        for raw in lines:
            line = raw.strip()
            if m := re.match(r"^###\s+(.+?)\s*$", line):
                sub_headings.append(m.group(1).strip())
            elif m := re.match(r"^[-*+]\s+(.+?)\s*$", line):
                bullets.append(m.group(1).strip())
        sections.append({
            "heading": topic,
            "sub_headings": sub_headings,
            "bullets": bullets,
            "requirements": build_section_requirements(topic, lines),
        })

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

    @staticmethod
    def _is_next_gen(model: str) -> bool:
        return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))

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
across many meetings. File each section by its FUNCTIONAL SUBJECT and CAPABILITY, not by
superficial keywords or headings — headings describe a single meeting and are never reused.

EXISTING MODULES (with their scope and boundaries):
{module_block}

SECTIONS TO FILE:
{self._sections_block(sections)}

Rules:
1. PREFER EXISTING CORE DOMAINS: Software systems are organized into broad functional parent domains.
   Sub-features, sub-screens, document types, compliance forms, and workflow integrations MUST be filed
   under the existing parent module that owns that lifecycle:
   - Benefits, insurance forms, vaccination, checklists, and sign-offs -> "Onboarding, E-Register & Compliance"
   - Job boards, syndication (Monster, Indeed), job orders, rates, and requisition posting -> "Orders & Position Profiles"
   - End user agreements, terms of service, client divisions, and client account setup -> "Client & Contact Management"
   - Candidate resume search, candidate sourcing, talent pools, and applicant records -> "Candidate & Employee Management"
   - Document type definitions, global dropdowns, and admin screens -> "Administration, Tools & Configuration"
   - Payment gateways, credit cards, pay codes, and invoicing -> "Timesheets, Payroll, Billing & Time-Off"
2. PROPOSING NEW MODULES IS A RARE EXCEPTION: Propose a new module ONLY for major, standalone third-party
   product ecosystems (such as CARV AI video interviewing) that have zero conceptual connection to any
   of the existing modules. NEVER propose a new module for standard sub-screens, document types, or minor workflows.
3. CONFIDENCE:
   - "high": The section's functionality is directly described by or naturally belongs to the module.
   - "medium": Belongs to the module's broader business domain, touching cross-cutting details.
   - "low": Weak fit. Only propose a new module if it is a completely separate third-party software platform.

Respond with ONLY JSON:
{{"assignments":[{{"index":0,"module":"exact name from existing or proposed list","confidence":"high|medium|low","reason":"short"}}],
  "proposals":[{{"name":"Clean Module Name","description":"One-line functional scope","reason":"why nothing in the existing list covers this capability"}}]}}"""

        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
            if self._is_next_gen(self.model):
                kwargs["max_completion_tokens"] = 3000
            else:
                kwargs["temperature"] = 0.0
                kwargs["max_tokens"] = 3000

            resp = self.client.chat.completions.create(**kwargs)
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
