import os
import sys
import re
from typing import Any

sys.path.insert(0, os.getcwd())

from app.services.requirement_blocks import TITLE_SEPARATOR

_STOPWORDS = {
    "and", "the", "for", "with", "from", "into", "under", "this", "that", "these",
    "those", "are", "was", "were", "been", "being", "have", "has", "had", "does",
    "did", "doing", "would", "should", "could", "ought", "shall", "will", "can",
    "may", "might", "must", "not", "such", "than", "too", "very", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most", "other",
    "some", "only", "own", "same", "than", "too", "very", "item", "items", "rule"
}

def extract_topic(title: str) -> str:
    if TITLE_SEPARATOR in (title or ""):
        return title.split(TITLE_SEPARATOR, 1)[0].strip()
    return (title or "").strip()

def _clean_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}

def score_relevance(existing_block: dict[str, Any], incoming_block: dict[str, Any]) -> float:
    score = 0.0
    
    # 1. Topic match
    e_topic = (extract_topic(existing_block.get("title") or "") or "").strip().lower()
    i_topic = (extract_topic(incoming_block.get("title") or "") or "").strip().lower()
    
    if e_topic and i_topic:
        if e_topic == i_topic:
            score += 50.0
        elif e_topic in i_topic or i_topic in e_topic:
            score += 35.0
        else:
            e_top_tokens = _clean_tokens(e_topic)
            i_top_tokens = _clean_tokens(i_topic)
            if e_top_tokens and i_top_tokens:
                overlap = len(e_top_tokens & i_top_tokens) / max(len(e_top_tokens | i_top_tokens), 1)
                score += overlap * 30.0

    # 2. Title tokens overlap
    e_title_tokens = _clean_tokens(existing_block.get("title") or "")
    i_title_tokens = _clean_tokens(incoming_block.get("title") or "")
    shared_title = e_title_tokens & i_title_tokens
    score += len(shared_title) * 8.0

    # 3. Bullets content overlap
    e_text = " ".join(existing_block.get("bullets") or [])
    i_text = " ".join(incoming_block.get("bullets") or [])
    e_tokens = _clean_tokens(e_text)
    i_tokens = _clean_tokens(i_text)
    shared_content = e_tokens & i_tokens
    score += len(shared_content) * 1.5

    return score

def select_candidate_blocks(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    max_candidates: int = 10,
    min_threshold_count: int = 8,
) -> list[dict[str, Any]]:
    if len(existing) <= min_threshold_count:
        return existing
    if not incoming or not existing:
        return existing

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for idx, e in enumerate(existing):
        best_score = max((score_relevance(e, n) for n in incoming), default=0.0)
        scored.append((best_score, idx, e))

    # Keep blocks with positive score, sorted by relevance score desc
    relevant = [item for item in scored if item[0] > 0]
    relevant.sort(key=lambda x: x[0], reverse=True)

    # Take top candidates
    chosen_items = relevant[:max_candidates]
    
    # If no blocks scored positive, keep top 3 just in case
    if not chosen_items:
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen_items = scored[:min(3, len(scored))]

    # Restore original document order
    chosen_items.sort(key=lambda x: x[1])
    return [item[2] for item in chosen_items]
