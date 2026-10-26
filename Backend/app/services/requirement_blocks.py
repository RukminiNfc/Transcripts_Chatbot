"""
One MOM sub-section = one requirement.

The minutes already group a meeting the way a requirement needs to be grouped:

    ## California Compliance — Meal Break Questions          <- topic
    ### Second Meal Break Question — Option Changes          <- sub-section = ONE requirement
    - Add a third selectable option ...                      <- its details
    - Store the selected third option ...

So a requirement is built here, by code, straight from that structure — no model call, the same
result every time. Its title carries the topic as well as the sub-section, because sub-section
names alone are often generic ("Business Rule", "Constraints", "Scope / Entry Points") and would
be meaningless, and collide, inside a module document that accumulates many topics:

    "California Compliance — Meal Break Questions: Second Meal Break Question — Option Changes"

`heading_key` is the title normalized. Together with the module it is the requirement's identity
from one meeting to the next (requirements.heading_key, migration d4a8c1f6b2e9).

Pure functions, no I/O, no imports from the rest of the app — safe to use from the classifier,
the reconciler, the indexer and one-off scripts alike.
"""
from __future__ import annotations

import re
from typing import Any

# The separator between topic and sub-section in a title. A colon reads naturally and does not
# occur inside the MOM's own headings, which use "/" and "—".
TITLE_SEPARATOR = ": "

# Postgres column width for requirements.heading_key.
_HEADING_KEY_MAX = 500

_H2 = re.compile(r"^##\s+(?!#)(.+?)\s*$")
_H3 = re.compile(r"^###\s+(.+?)\s*$")
_BULLET = re.compile(r"^(\s*)[-*+•]\s+(.+?)\s*$")


def make_title(topic: str, sub_heading: str) -> str:
    """'Topic: Sub-section', or just the topic for bullets that sit directly under it."""
    topic = (topic or "").strip()
    sub_heading = (sub_heading or "").strip()
    if not sub_heading:
        return topic
    if not topic:
        return sub_heading
    return f"{topic}{TITLE_SEPARATOR}{sub_heading}"


def heading_key(title: str) -> str:
    """
    A title in the form used to recognise the same requirement across meetings.

    Case, punctuation, markdown emphasis and spacing are ignored; words and their order are not.
    '&' and 'and' are treated as the same word, since the MOM uses both for the same heading.
    """
    text = (title or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[*_`~]", "", text)            # markdown emphasis
    text = re.sub(r"[^a-z0-9]+", " ", text)        # any punctuation or dash -> one space
    return re.sub(r"\s+", " ", text).strip()[:_HEADING_KEY_MAX]


def build_section_requirements(
    topic: str,
    lines: list[str],
) -> list[dict[str, Any]]:
    """
    The requirements inside one `##` topic, given the lines below its heading.

    Returns [{"title", "heading_key", "topic", "sub_heading", "bullets": [...]}] in document order.
    Bullets above the first `###` become a requirement titled with the topic alone. A sub-section
    that appears twice under one topic is merged rather than duplicated. Sub-sections with no
    bullets are dropped — a heading with nothing under it states no requirement.

    Nested bullets keep their indentation relative to the top level, so detail lists survive.
    """
    blocks: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    current_sub = ""
    base_indent: int | None = None

    def block_for(sub: str) -> dict[str, Any]:
        title = make_title(topic, sub)
        key = heading_key(title)
        if key not in blocks:
            blocks[key] = {
                "title": title,
                "heading_key": key,
                "topic": topic.strip(),
                "sub_heading": sub.strip(),
                "bullets": [],
            }
            order.append(key)
        return blocks[key]

    for raw in lines:
        if m := _H3.match(raw.strip()):
            current_sub = m.group(1).strip()
            base_indent = None
            continue
        if m := _BULLET.match(raw.rstrip()):
            indent = len(m.group(1).replace("\t", "    "))
            if base_indent is None or indent < base_indent:
                base_indent = indent
            depth = (indent - base_indent) // 2
            text = m.group(2).strip()
            if text:
                block_for(current_sub)["bullets"].append(("  " * depth) + text)

    return [blocks[k] for k in order if blocks[k]["bullets"]]


def split_topics(markdown: str) -> list[tuple[str, list[str]]]:
    """A minutes document into [(topic heading, lines under it)], in order. No filtering."""
    topics: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for raw in (markdown or "").split("\n"):
        if m := _H2.match(raw.strip()):
            current = []
            topics.append((m.group(1).strip(), current))
            continue
        if current is not None:
            current.append(raw)
    return topics


# A block withdrawn by the client. Kept in the document for history, excluded from the index.
DEPRECATED_MARK = "~~DEPRECATED~~"

# Sections of a module document that are not requirements. Their `###` blocks are never parsed as
# requirements, never sent to reconciliation and never indexed.
_NON_REQUIREMENT_SECTIONS = re.compile(r"^(action items)\b", re.IGNORECASE)


def parse_document_blocks(markdown: str) -> list[dict[str, Any]]:
    """
    A module document into its requirement blocks, in document order.

    Returns [{"title", "heading_key", "bullets": [...], "deprecated": bool}]. Only `###` blocks
    under requirement sections count; anything under "## Action Items" is skipped. A block is
    deprecated when its title carries DEPRECATED_MARK; the mark is removed from the title and
    the key, so a deprecated requirement keeps its identity.
    """
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_requirements = True
    base_indent: int | None = None

    for raw in (markdown or "").split("\n"):
        line = raw.strip()
        if m := _H2.match(line):
            in_requirements = not _NON_REQUIREMENT_SECTIONS.match(m.group(1).strip())
            current = None
            continue
        if not in_requirements:
            continue
        if m := _H3.match(line):
            title = m.group(1).strip()
            deprecated = title.startswith(DEPRECATED_MARK)
            if deprecated:
                title = title[len(DEPRECATED_MARK):].strip()
            current = {"title": title, "heading_key": heading_key(title),
                       "bullets": [], "deprecated": deprecated}
            blocks.append(current)
            base_indent = None
            continue
        if current is not None and (m := _BULLET.match(raw.rstrip())):
            indent = len(m.group(1).replace("\t", "    "))
            if base_indent is None or indent < base_indent:
                base_indent = indent
            depth = (indent - base_indent) // 2
            if m.group(2).strip():
                current["bullets"].append(("  " * depth) + m.group(2).strip())

    return blocks


def render_block(requirement: dict[str, Any]) -> str:
    """One requirement as it appears in a module document: `### title` then its bullets."""
    mark = f"{DEPRECATED_MARK} " if requirement.get("deprecated") else ""
    lines = [f"### {mark}{requirement['title']}"]
    for bullet in requirement.get("bullets") or []:
        stripped = bullet.lstrip(" ")
        depth = (len(bullet) - len(stripped)) // 2
        lines.append(f"{'  ' * depth}- {stripped}")
    return "\n".join(lines)
