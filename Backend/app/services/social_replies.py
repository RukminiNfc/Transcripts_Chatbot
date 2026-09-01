"""Deterministic replies for social / small-talk messages (greetings, farewells, thanks).

These are matched BEFORE any retrieval or LLM call, so the bot:
  - stays strictly IN CHARACTER as the Requirement Tracking Assistant (never drifts into
    generic-assistant chit-chat like "coding, AI, interviews" or "sweet dreams"),
  - responds the SAME on-brand way every time (100% consistent, local and server),
  - costs nothing and answers instantly for "hi" / "good morning" / "bye" / "thanks".

Editable by design: change the wording in SOCIAL_REPLIES or the phrases in SOCIAL_TRIGGERS.
Each time-of-day / social type has its OWN reply (good morning -> a morning message, good
night -> a night message, NOT a generic goodbye).

Matching is WHOLE-MESSAGE only (after lowercasing + trimming punctuation/emoji), so a real
question that merely CONTAINS a social word (e.g. "what did Prasad say in the morning call?")
is NOT treated as small-talk and still goes through the normal pipeline.
"""
from __future__ import annotations

import re
from typing import Optional

# On-brand replies, one per social type. Edit the wording freely; keep them short, warm,
# and in-character (always steer back to requirements/transcripts).
SOCIAL_REPLIES = {
    "greeting": (
        "Hello! 👋 I'm your Requirement Tracking Assistant. Ask me anything about your "
        "project's grooming-call transcripts, requirements, or decisions."
    ),
    "morning": (
        "Good morning! ☀️ Hope you're off to a great start. I'm your Requirement Tracking "
        "Assistant — ask me anything about your transcripts, requirements, or decisions."
    ),
    "afternoon": (
        "Good afternoon! 🌤️ Hope your day's going well. Ask me anything about your project's "
        "transcripts, requirements, or decisions."
    ),
    "evening": (
        "Good evening! 🌆 Hope you had a productive day. Ask me anything about your project's "
        "transcripts, requirements, or decisions."
    ),
    "night": (
        "Good night! 🌙 Rest well — I'm here whenever you need anything about your project's "
        "requirements or transcripts."
    ),
    "farewell": (
        "Goodbye! 👋 I'm here whenever you need anything about your project's requirements "
        "or transcripts."
    ),
    "thanks": (
        "You're welcome! 😊 Happy to help with anything about your requirements or transcripts."
    ),
    "smalltalk": (
        "I'm doing great, thanks for asking! 😊 I'm your Requirement Tracking Assistant — "
        "ready to help with your project's transcripts, requirements, and decisions. "
        "What would you like to know?"
    ),
    "identity": (
        "I'm your Requirement Tracking Assistant. I can answer questions about your project's "
        "grooming-call transcripts, requirements, and the decisions made in your meetings — "
        "for example, \"what requirements came from the last call?\" or \"who is our client?\". "
        "What would you like to know?"
    ),
}

# Whole-message trigger phrases -> reply category. Add/remove phrases as needed.
SOCIAL_TRIGGERS = {
    "greeting": {
        "hi", "hii", "hiii", "hey", "heya", "hello", "helo", "hallo", "yo",
        "greetings", "hi there", "hello there", "hey there",
    },
    "morning": {
        "good morning", "gud morning", "gm", "morning",
    },
    "afternoon": {
        "good afternoon",
    },
    "evening": {
        "good evening",
    },
    "night": {
        "good night", "goodnight", "gud night", "gn",
    },
    "farewell": {
        "bye", "byee", "goodbye", "good bye", "see you", "see ya",
        "see you later", "cya", "take care", "farewell",
    },
    "thanks": {
        "thanks", "thank you", "thankyou", "thx", "ty", "thank u",
        "thanks a lot", "thank you so much", "many thanks", "much appreciated",
    },
    "smalltalk": {
        "how are you", "how are you doing", "how are you doing today", "how r u",
        "how are u", "how ru", "hows it going", "how's it going", "how is it going",
        "whats up", "what's up", "sup", "wassup", "how do you do", "you good",
        "are you there", "you there",
    },
    "identity": {
        "who are you", "what are you", "who r u", "what can you do", "what do you do",
        "how can you help", "how can you help me", "what can i ask", "what can i ask you",
        "help", "what do you know",
    },
}

# Flat phrase -> category map for O(1) lookup (built once at import).
_PHRASE_TO_CATEGORY = {
    phrase: category
    for category, phrases in SOCIAL_TRIGGERS.items()
    for phrase in phrases
}


def _normalize(text: str) -> str:
    """Lowercase, trim, and strip punctuation/emoji/digits so 'Good Morning!!!', 'good morning 👋'
    and 'good morning.' all collapse to 'good morning'. Keeps only letters, spaces, apostrophes."""
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z' ]+", " ", t)   # drop punctuation, emoji, digits
    t = re.sub(r"\s+", " ", t).strip()
    return t


def match_social_reply(query: str) -> Optional[str]:
    """Return the fixed on-brand reply if `query` IS a pure greeting / farewell / thanks
    message (whole-message match); else None so the normal RAG pipeline handles it."""
    normalized = _normalize(query)
    if not normalized:
        return None
    category = _PHRASE_TO_CATEGORY.get(normalized)
    return SOCIAL_REPLIES[category] if category else None


# Short greeting OPENERS for a MIXED "greeting + question" message (e.g. "Hi, who won IPL?").
# Whether a message contains a greeting — and which KIND — is decided by the understanding model
# (gpt-5.5) BY MEANING, not a phrase list; this only maps that kind to a brief, friendly opener.
# These are generic salutations (no project logic), reused when the refusal reply can't greet itself.
_GREETING_PREFIXES = {
    "morning": "Good morning! ☀️",
    "afternoon": "Good afternoon! 🌤️",
    "evening": "Good evening! 🌆",
    "hi": "Hi there! 👋",
}


def greeting_prefix(kind: Optional[str]) -> str:
    """Map the understanding model's greeting KIND ('morning'/'afternoon'/'evening'/'hi') to a
    short opener. Returns '' for 'none'/unknown so callers can safely prepend unconditionally."""
    return _GREETING_PREFIXES.get((kind or "").strip().lower(), "")
