"""Minimal Jira Cloud client — creates Task issues for approved MOM action items.

REST API v3 directly (a handful of endpoints) rather than the `jira` SDK, which is sync-only.
Auth is HTTP Basic with the account email + API token. The token is never logged; errors carry
Jira's own message so the admin can act on it.

Differences from Azure Boards that shape this module:
- Assignees are Atlassian account IDs, not emails → looked up per email (strictly: exactly one
  active Atlassian account, else the item fails visibly).
- Descriptions are Atlassian Document Format (ADF) JSON, not HTML.
- Which fields a project's Task accepts at create time (Due date, Assignee) depends on its create
  screen → read once per approval from createmeta (`project_info`).
"""
import base64
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


class JiraError(Exception):
    """Jira rejected the request. The message is safe to show to an admin."""


@dataclass
class JiraProjectInfo:
    """What one project's Task create screen accepts. Built once per approval."""
    project_key: str
    task_type_id: str
    has_duedate: bool
    has_assignee: bool
    account_ids: Dict[str, Optional[str]] = field(default_factory=dict)   # email(lower) → accountId; cache per push


def _base() -> str:
    return settings.JIRA_BASE_URL.rstrip("/")


def _headers() -> dict:
    token = base64.b64encode(f"{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


def _error_text(resp: httpx.Response) -> str:
    """Jira errors: {"errorMessages": [...], "errors": {"field": "message"}}."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    parts = list(body.get("errorMessages") or [])
    parts += [f"{k}: {v}" for k, v in (body.get("errors") or {}).items()]
    return "; ".join(parts)[:500] or resp.text[:500]


def _raise_for_jira(resp: httpx.Response, what: str) -> None:
    if resp.status_code == 401:
        raise JiraError("Jira rejected the credentials — check JIRA_EMAIL / JIRA_API_TOKEN (expired or revoked?).")
    if resp.status_code == 403:
        raise JiraError(f"Jira refused {what} (403) — the account lacks permission. {_error_text(resp)}".strip())
    if resp.status_code >= 400:
        raise JiraError(f"Jira error {resp.status_code} while {what}: {_error_text(resp)}")


async def _request(client: httpx.AsyncClient, method: str, path: str, what: str, **kwargs) -> httpx.Response:
    try:
        resp = await client.request(method, f"{_base()}{path}", headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise JiraError(f"Could not reach Jira: {exc.__class__.__name__}") from exc
    _raise_for_jira(resp, what)
    return resp


def _check_enabled() -> None:
    if not settings.JIRA_ENABLED:
        raise JiraError("Jira integration is disabled (JIRA_ENABLED=false).")


# ── Pure helpers (unit-tested offline in test_jira_client.py) ────────────────

def pick_task_type(issue_types: List[dict]) -> Optional[str]:
    """Id of the project's standard (non-subtask) issue type named "Task"."""
    for t in issue_types:
        if (t.get("name") or "").strip().lower() == "task" and not t.get("subtask"):
            return str(t["id"])
    return None


def field_ids(fields: List[dict]) -> set:
    return {f.get("fieldId") or f.get("key") for f in fields}


def pick_account(results: List[dict], email: str) -> Optional[str]:
    """Account id for `email` from /user/search results, or None.

    Only active human ("atlassian") accounts count. When Jira exposes emailAddress it must match;
    when privacy settings hide it, a single remaining candidate is accepted. Anything ambiguous →
    None, so the item fails visibly instead of being assigned to the wrong person.
    """
    people = [r for r in results if r.get("accountType") == "atlassian" and r.get("active", True)]
    exact = [r for r in people if (r.get("emailAddress") or "").lower() == email.lower()]
    if len(exact) == 1:
        return exact[0]["accountId"]
    hidden = [r for r in people if not r.get("emailAddress")]
    if not exact and len(people) == 1 and len(hidden) == 1:
        return hidden[0]["accountId"]
    return None


def _text(value: str, marks: Optional[list] = None) -> dict:
    node = {"type": "text", "text": value}
    if marks:
        node["marks"] = marks
    return node


def build_description_adf(
    description: str,
    owner_name: Optional[str],
    due_text: Optional[str],
    due_date: Optional[date],
    session_name: Optional[str],
    call_date: Optional[date],
    mom_url: str,
) -> dict:
    """ADF body with the same content as the Azure Boards HTML description.

    ADF rejects empty text nodes, so every value falls back to "—".
    """
    bold = [{"type": "strong"}]
    due = due_date.isoformat() if due_date else ""
    if due_text and due_text != due:
        due = f"{due} ({due_text})" if due else due_text
    meeting = (session_name or "") + (f" · {call_date.isoformat()}" if call_date else "")

    def line(label: str, value: str) -> List[dict]:
        return [_text(f"{label}: ", bold), _text(value or "—")]

    details = (
        line("Owner (as in MOM)", owner_name or "") + [{"type": "hardBreak"}]
        + line("Due", due) + [{"type": "hardBreak"}]
        + line("Meeting", meeting) + [{"type": "hardBreak"}]
        + [_text("Minutes: ", bold), _text(mom_url, [{"type": "link", "attrs": {"href": mom_url}}])]
    )
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [_text(description or "—")]},
            {"type": "paragraph", "content": details},
        ],
    }


# ── API calls ────────────────────────────────────────────────────────────────

async def project_info(project_key: str) -> JiraProjectInfo:
    """Read the project's Task type and its create-screen fields. Raises JiraError."""
    _check_enabled()
    key = quote(project_key)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _request(client, "GET", f"/rest/api/3/issue/createmeta/{key}/issuetypes?maxResults=200",
                              f"reading issue types of project {project_key}")
        body = resp.json()
        task_id = pick_task_type(body.get("issueTypes") or body.get("values") or [])
        if not task_id:
            raise JiraError(f"Jira project {project_key} has no 'Task' issue type.")
        resp = await _request(client, "GET", f"/rest/api/3/issue/createmeta/{key}/issuetypes/{task_id}?maxResults=200",
                              f"reading Task fields of project {project_key}")
        body = resp.json()
        ids = field_ids(body.get("fields") or body.get("values") or [])
    return JiraProjectInfo(project_key=project_key, task_type_id=task_id,
                           has_duedate="duedate" in ids, has_assignee="assignee" in ids)


async def account_id_for(info: JiraProjectInfo, email: str) -> str:
    """Resolve an email to an account id (cached on `info`). Raises JiraError if not exactly one match."""
    k = email.lower()
    if k not in info.account_ids:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await _request(client, "GET", f"/rest/api/3/user/search?query={quote(email)}&maxResults=10",
                                  f"looking up user {email}")
        info.account_ids[k] = pick_account(resp.json(), email)
    if not info.account_ids[k]:
        raise JiraError(
            f"No single active Jira user found for {email}. Check they are on the Jira site, or that the "
            "API account has the 'Browse users and groups' permission."
        )
    return info.account_ids[k]


async def create_issue(
    info: JiraProjectInfo,
    title: str,
    description_adf: dict,
    assignee_email: Optional[str],
    due_date: Optional[date],
) -> Tuple[str, str, Optional[str]]:
    """Create one Task. Returns (issue key, browse URL, warning-or-None). Raises JiraError on rejection.

    The assignee is resolved BEFORE creating, so an unknown person fails the item without leaving a
    stray issue. If the create screen has no Assignee field, the issue is created then assigned in a
    second call; if only that second call fails, the issue exists and the warning says so.
    """
    _check_enabled()
    account_id = await account_id_for(info, assignee_email) if assignee_email else None

    fields = {
        "project": {"key": info.project_key},
        "issuetype": {"id": info.task_type_id},
        "summary": title,
        "description": description_adf,
    }
    if due_date and info.has_duedate:
        fields["duedate"] = due_date.isoformat()
    if account_id and info.has_assignee:
        fields["assignee"] = {"accountId": account_id}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _request(client, "POST", "/rest/api/3/issue", "creating the issue", json={"fields": fields})
        key = resp.json()["key"]
        url = f"{_base()}/browse/{key}"
        logger.info(f"Created Jira issue {key} in project {info.project_key}")

        warning = None
        if account_id and not info.has_assignee:
            try:
                await _request(client, "PUT", f"/rest/api/3/issue/{quote(key)}/assignee",
                               f"assigning {key}", json={"accountId": account_id})
            except JiraError as exc:
                warning = f"Created, but could not assign to {assignee_email}: {exc}"
                logger.warning(f"Jira issue {key}: {warning}")
    return key, url, warning
