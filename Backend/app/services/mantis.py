"""Minimal Mantis client — creates Task tickets on the internal task app for approved MOM action items.

REST API (`/api/rest`) directly via httpx. Auth is the integration account's API token sent as-is
in `Authorization` (no "Bearer"); tickets show that account as the reporter. The token is never
logged; errors carry Mantis's own message so the admin can act on it.

Differences from Azure Boards / Jira that shape this module:
- The ticket "type" is not native — it is the "Task Type" custom field. It is sent only when linked
  to the project (`project_info`); unlinked, the server shows new tickets as Task by default, which
  is what we want anyway (verified on task.nfcsolutionsusa.com, 2026-10-22).
- Creating a ticket is slow on the live server (>60 s observed, likely notification emails sent
  inline), so writes get a long timeout — a premature timeout would fail an item whose ticket was
  in fact created, and the retry would duplicate it.
- Assignees are Mantis usernames and Mantis has no find-user-by-email call, so the username is the
  assignee email's prefix (jdoe@x.com → "jdoe"). If Mantis doesn't know that user, the ticket is
  created unassigned and the item carries a warning — it is not failed.
- Project and category are typed by hand per customer → `project_info` validates both against
  GET /projects before anything is approved.
"""
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)
_CREATE_TIMEOUT = httpx.Timeout(30.0, read=180.0)   # see module docstring: creates are slow

TASK_TYPE_FIELD = "Task Type"
TASK_TYPE_VALUE = "Task"


class MantisError(Exception):
    """Mantis rejected the request. The message is safe to show to an admin."""


@dataclass
class MantisProjectInfo:
    """The validated target for one approval."""
    project_id: int
    project_name: str
    category: str          # exact spelling as Mantis stores it
    has_task_type: bool    # "Task Type" custom field linked → send it explicitly


def _base() -> str:
    return settings.MANTIS_API_URL.rstrip("/")


def site_url() -> str:
    """Web root for ticket links: the REST root minus /api/rest[/index.php]."""
    return re.sub(r"/api/rest(/index\.php)?$", "", _base())


def _headers() -> dict:
    return {"Authorization": settings.MANTIS_API_TOKEN, "Accept": "application/json"}


def _error_text(resp: httpx.Response) -> str:
    """Mantis errors: {"message": "...", "code": 1100, "localized": "..."}."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    if not isinstance(body, dict):
        return resp.text[:500]
    return (body.get("localized") or body.get("message") or resp.text)[:500]


def _raise_for_mantis(resp: httpx.Response, what: str) -> None:
    if resp.status_code == 401:
        raise MantisError(
            "Mantis rejected the API token — check MANTIS_API_TOKEN. If the token is valid, the server "
            "may be dropping the Authorization header (PHP-FPM): add "
            "`SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1` to api/rest/.htaccess."
        )
    if resp.status_code == 403:
        raise MantisError(f"Mantis refused {what} (403) — the account lacks permission. {_error_text(resp)}".strip())
    if resp.status_code == 404 and not _base().endswith("index.php"):
        raise MantisError(
            f"Mantis returned 404 while {what}. If the server's rewrite rules are off, set "
            "MANTIS_API_URL to .../api/rest/index.php."
        )
    if resp.status_code >= 400:
        raise MantisError(f"Mantis error {resp.status_code} while {what}: {_error_text(resp)}")
    if "json" not in resp.headers.get("content-type", ""):
        raise MantisError(f"Mantis returned a non-JSON page while {what} — check MANTIS_API_URL.")


async def _request(client: httpx.AsyncClient, method: str, path: str, what: str, **kwargs) -> httpx.Response:
    try:
        resp = await client.request(method, f"{_base()}{path}", headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise MantisError(f"Could not reach Mantis: {exc.__class__.__name__}") from exc
    _raise_for_mantis(resp, what)
    return resp


def _check_enabled() -> None:
    if not settings.MANTIS_ENABLED:
        raise MantisError("Mantis integration is disabled (MANTIS_ENABLED=false).")


# ── Pure helpers (unit-tested offline in test_mantis_client.py) ──────────────

def find_project(projects: List[dict], name: str) -> Optional[dict]:
    """Project named `name` (case-insensitive), searching subprojects too."""
    want = name.strip().lower()
    for p in projects:
        if (p.get("name") or "").strip().lower() == want:
            return p
        found = find_project(p.get("subProjects") or [], name)
        if found:
            return found
    return None


def pick_category(project: dict, name: str) -> Optional[str]:
    """The category's exact name as Mantis stores it, or None."""
    want = name.strip().lower()
    for c in project.get("categories") or []:
        if (c.get("name") or "").strip().lower() == want:
            return c["name"]
    return None


def username_for(email: Optional[str]) -> Optional[str]:
    """Mantis username for an assignee email: the part before "@"."""
    prefix = (email or "").split("@", 1)[0].strip()
    return prefix or None


def has_custom_field(project: dict, name: str) -> bool:
    return any((f.get("name") or "").strip().lower() == name.lower() for f in project.get("custom_fields") or [])


def build_description(
    description: str,
    owner_name: Optional[str],
    due_text: Optional[str],
    due_date: Optional[date],
    session_name: Optional[str],
    call_date: Optional[date],
    mom_url: str,
) -> str:
    """Plain-text body with the same content as the Azure Boards / Jira descriptions.

    Mantis requires a non-blank description, so every value falls back to "—".
    """
    due = due_date.isoformat() if due_date else ""
    if due_text and due_text != due:
        due = f"{due} ({due_text})" if due else due_text
    meeting = (session_name or "") + (f" · {call_date.isoformat()}" if call_date else "")
    return (
        f"{description or '—'}\n\n"
        f"Owner (as in MOM): {owner_name or '—'}\n"
        f"Due: {due or '—'}\n"
        f"Meeting: {meeting or '—'}\n"
        f"Minutes: {mom_url}"
    )


# ── API calls ────────────────────────────────────────────────────────────────

async def project_info(project_name: str, category: str) -> MantisProjectInfo:
    """Validate the customer's project / category; note whether Task Type is linked. Raises MantisError."""
    _check_enabled()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _request(client, "GET", "/projects", "listing projects")
    project = find_project(resp.json().get("projects") or [], project_name)
    if not project:
        raise MantisError(f"Mantis project '{project_name}' was not found (or the API account can't see it).")
    cat = pick_category(project, category)
    if not cat:
        raise MantisError(f"Mantis project '{project['name']}' has no category '{category}'.")
    return MantisProjectInfo(project_id=int(project["id"]), project_name=project["name"], category=cat,
                             has_task_type=has_custom_field(project, TASK_TYPE_FIELD))


async def create_issue(
    info: MantisProjectInfo,
    title: str,
    description: str,
    assignee_email: Optional[str],
    due_date: Optional[date],
) -> Tuple[str, str, Optional[str]]:
    """Create one Task ticket. Returns (issue id, view URL, warning-or-None). Raises MantisError.

    The assignee is the email prefix. If Mantis rejects the create while a handler is set (e.g. no
    such user, or not allowed to handle issues in this project), it is retried once without the
    handler: a real problem elsewhere fails again with Mantis's own message, while a bad assignee
    leaves an unassigned ticket plus a warning that quotes the first rejection.
    """
    _check_enabled()
    username = username_for(assignee_email)

    payload = {
        "summary": title,
        "description": description,
        "project": {"id": info.project_id},
        "category": {"name": info.category},
    }
    if info.has_task_type:
        payload["custom_fields"] = [{"field": {"name": TASK_TYPE_FIELD}, "value": TASK_TYPE_VALUE}]
    if username:
        payload["handler"] = {"name": username}
    if due_date:
        payload["due_date"] = due_date.isoformat()

    warning = None
    async with httpx.AsyncClient(timeout=_CREATE_TIMEOUT) as client:
        try:
            resp = await _request(client, "POST", "/issues", "creating the ticket", json=payload)
        except MantisError as exc:
            # Only a plain 4xx rejection can be the assignee (not auth, 403, network or 5xx).
            if "handler" not in payload or not str(exc).startswith("Mantis error 4"):
                raise
            del payload["handler"]
            resp = await _request(client, "POST", "/issues", "creating the ticket", json=payload)
            warning = f"Created unassigned — Mantis did not accept user '{username}' as assignee: {exc}"
    issue_id = str(resp.json()["issue"]["id"])
    logger.info(f"Created Mantis issue {issue_id} in project {info.project_name}")
    if warning:
        logger.warning(f"Mantis issue {issue_id}: {warning}")
    return issue_id, f"{site_url()}/view.php?id={issue_id}", warning
