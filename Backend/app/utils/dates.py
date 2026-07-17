"""Single source of truth for turning a transcript's session name into a clean date.

Session names look like "05-08-26_Grooming" (MM-DD-YY). We derive the date from the NAME
rather than the stored timestamp because the timestamp is timezone-shifted (e.g. the
"05-01-26" call is stored as 2026-04-30 18:30 UTC). Nothing here is hardcoded — it parses
whatever session name it is given and falls back to the provided timestamp if the name has
no recognizable date.
"""
import re
from datetime import date

# Leading MM-DD-YY at the start of a session name, e.g. "05-08-26_Grooming".
_SESSION_DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})")


def session_to_ymd(session_name: str, fallback_date=None) -> str:
    """Return YYYY-MM-DD for a session.

    Priority: date encoded in the session name -> provided fallback timestamp -> "".
    Never raises. An unparseable/invalid name (e.g. "13-40-26") falls through to the fallback.
    """
    m = _SESSION_DATE_RE.match(session_name or "")
    if m:
        mm, dd, yy = (int(g) for g in m.groups())
        try:
            return date(2000 + yy, mm, dd).isoformat()  # validates the month/day too
        except ValueError:
            pass  # not a real date; use the fallback below

    if fallback_date is not None:
        s = fallback_date.isoformat() if hasattr(fallback_date, "isoformat") else str(fallback_date)
        return s[:10]

    return ""
