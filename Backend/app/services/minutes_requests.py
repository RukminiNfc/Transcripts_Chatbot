"""Detect an explicit request for the Minutes of Meeting document.

Matched BEFORE the intent classifier runs, for the same reasons `social_replies.py` is:
it costs nothing, answers instantly, and behaves identically every time.

THE RULE THAT MATTERS
---------------------
Fire only when the user is asking for the DOCUMENT ITSELF, never when they are asking about
what was discussed. Those want a normal answer.

    "show me the minutes"              -> minutes document
    "what was discussed on 4 May?"     -> normal answer

This is the expensive mistake to avoid. A false negative is nearly free — the question falls
through to the normal pipeline and gets answered as it always has, with a link to the minutes
offered alongside. A false POSITIVE replaces a working answer with a 20-page document.

So matching is deliberately strict:

  * the message must START with a minutes phrase, or be one outright
  * "minutes" alone never matches — it is also a unit of time, and this project's requirements
    are full of timeouts and break durations ("30 minutes", "session timeout in minutes")
  * "meeting" alone never matches — almost every question here is about a meeting

Anything the matcher misses is caught afterwards by the `minutes` field on the intent
classifier, which handles phrasings no pattern list would predict.

Editable by design: add phrasings to _REQUEST_PATTERNS as real usage shows what people type.
Every addition must be added to the negative test list too.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

# The document, named. Requires a qualifier — bare "minutes" is excluded on purpose.
_DOC = r"(?:minutes\s+of\s+(?:the\s+)?meeting|meeting\s+minutes|mom|the\s+minutes|minutes\s+(?:for|of|from))"

# Asking for something, at the start of the message.
_ASK = r"(?:show|give|get|send|share|display|open|download|fetch|pull\s+up|bring\s+up|i\s+want|i\s+need|can\s+i\s+(?:get|have|see)|let\s+me\s+see)"

_REQUEST_PATTERNS = [
    # "show me the minutes", "download the MOM for 4 May", "can I get the meeting minutes"
    re.compile(rf"^{_ASK}\b[^?]{{0,40}}?\b{_DOC}\b", re.IGNORECASE),
    # "minutes of meeting", "MOM for the last call", "meeting minutes 4 May" — the doc named up front
    re.compile(rf"^{_DOC}\b", re.IGNORECASE),
    # "<doc> document / doc / file / report"
    re.compile(rf"^{_ASK}?\s*\b{_DOC}\s+(?:document|doc|file|report|pdf|word)\b", re.IGNORECASE),
    # "generate/create the minutes document" — asking for the artifact, not for it to be produced
    re.compile(rf"^(?:view|read)\s+(?:the\s+)?{_DOC}\b", re.IGNORECASE),
]

# Phrases that mean "tell me what happened", NOT "give me the document". Checked first and
# they win, because this is the boundary the whole design rests on.
_DISCUSSION_PATTERNS = [
    re.compile(r"\bwhat\s+(?:was|were|did|happened|got)\b", re.IGNORECASE),
    re.compile(r"\b(?:summari[sz]e|summary\s+of)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+(?:many|much|long)\b", re.IGNORECASE),
    re.compile(r"\b(?:why|who|when|where|which)\b", re.IGNORECASE),
    re.compile(r"\b(?:discussed|decided|talked\s+about|said|mentioned|agreed)\b", re.IGNORECASE),
]

# Dates people actually type. Year is optional — the caller resolves it against real call dates.
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MONTH_ALT = "|".join(m[:3] for m in _MONTHS)

_DATE_PATTERNS = [
    # 4 May, 4th May 2026, 21 Aug
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})[a-z]*\.?\s*(\d{{4}})?\b", re.IGNORECASE),
    # May 4, May 4th 2026
    re.compile(rf"\b({_MONTH_ALT})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})?\b", re.IGNORECASE),
    # 05-04-26 / 05-04-2026 / 05/04/26  — the session-name format used in this project (MM-DD-YY)
    re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b"),
    # ISO: 2026-05-04
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
]

_LATEST_PATTERNS = re.compile(
    r"\b(?:last|latest|most\s+recent|previous|recent)\s+(?:call|meeting|grooming|session|one)\b",
    re.IGNORECASE,
)


def _normalise(message: str) -> str:
    """Lowercase, strip punctuation noise and collapse whitespace — matching social_replies."""
    text = (message or "").strip().lower()
    text = re.sub(r"[\"'`]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .!?,;:")


def is_minutes_request(message: str) -> bool:
    """
    True only when the message is explicitly asking for the minutes DOCUMENT.

    Conservative by design. When in doubt it returns False and the question goes through the
    normal pipeline, where the intent classifier gets a second chance to catch it.
    """
    text = _normalise(message)
    if not text or len(text) > 200:
        # A very long message is a real question, not a document request.
        return False

    # "what was discussed in the meeting" must never be treated as a document request,
    # even though it contains "meeting".
    for pattern in _DISCUSSION_PATTERNS:
        if pattern.search(text):
            return False

    return any(pattern.search(text) for pattern in _REQUEST_PATTERNS)


def wants_latest(message: str) -> bool:
    """True when the user said 'the last call' rather than naming a date."""
    return bool(_LATEST_PATTERNS.search(_normalise(message)))


def mentioned_date_text(message: str) -> Optional[str]:
    """
    The raw date phrase the user typed, if any — "4 may", "9 december", "05-04-26".

    Needed because `extract_date` returns None for two very different situations:

        "show me the minutes"        no date mentioned      -> use the most recent
        "minutes for 9 December"     date with no such call -> SAY SO

    Without this distinction the second case silently returns the latest minutes, which looks
    like a correct answer and is not.
    """
    text = _normalise(message)
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


def extract_date(message: str, available_dates: Optional[list[date]] = None) -> Optional[date]:
    """
    Pull a call date out of the message.

    `available_dates` are the real call dates from the database. When the user omits the year
    ("minutes for 4 May") the year is taken from the matching real call rather than assumed,
    so a two-year-old project does not silently resolve to the wrong year.

    Returns None when no date is mentioned — the caller then uses the most recent minutes.
    """
    text = _normalise(message)
    month_index = {m[:3]: i + 1 for i, m in enumerate(_MONTHS)}
    candidates: list[tuple[int, int, Optional[int]]] = []   # (month, day, year|None)

    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            try:
                if pattern is _DATE_PATTERNS[0]:          # 4 May [2026]
                    day, mon = int(groups[0]), month_index[groups[1][:3].lower()]
                    year = int(groups[2]) if groups[2] else None
                elif pattern is _DATE_PATTERNS[1]:        # May 4 [2026]
                    mon, day = month_index[groups[0][:3].lower()], int(groups[1])
                    year = int(groups[2]) if groups[2] else None
                elif pattern is _DATE_PATTERNS[2]:        # MM-DD-YY
                    mon, day, raw = int(groups[0]), int(groups[1]), int(groups[2])
                    year = raw + 2000 if raw < 100 else raw
                else:                                      # YYYY-MM-DD
                    year, mon, day = int(groups[0]), int(groups[1]), int(groups[2])
            except (KeyError, ValueError):
                continue
            if 1 <= mon <= 12 and 1 <= day <= 31:
                candidates.append((mon, day, year))

    if not candidates:
        return None

    month, day, year = candidates[0]

    if year is None:
        # Year omitted. Prefer a real call that matches the month and day.
        for call_date in sorted(available_dates or [], reverse=True):
            if call_date.month == month and call_date.day == day:
                return call_date
        return None   # no such call — let the caller say so rather than invent a year

    try:
        return date(year, month, day)
    except ValueError:
        return None
