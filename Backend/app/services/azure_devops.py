"""Minimal Azure DevOps Boards client — creates Task work items.

Uses the REST API directly (one endpoint) rather than the azure-devops SDK, which is sync-only and
heavy for a single call. Auth is a PAT via HTTP Basic with an empty username.
The PAT is never logged; errors surface ADO's own message so the admin can act on it
(e.g. "The identity value 'x@y.com' for field 'Assigned To' is an unknown identity").
"""
import base64
import html
import logging
from datetime import date
from typing import Optional, Tuple
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_API_VERSION = "7.1"
_TIMEOUT = httpx.Timeout(30.0)


class AzureDevOpsError(Exception):
    """ADO rejected the request. The message is safe to show to an admin."""


def _auth_header() -> dict:
    token = base64.b64encode(f":{settings.ADO_PAT}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


_DUE_DATE_FIELD = "Microsoft.VSTS.Scheduling.DueDate"


async def task_has_due_date_field(project: str) -> bool:
    """Whether this project's Task type has the standard Due Date field.

    Processes differ: Agile/CMMI-derived ones have it, Basic-derived ones usually don't, and
    sending an unknown field makes ADO reject the whole Task. Checked per approval (not cached) so
    adding the field to the process later takes effect without a restart.
    """
    url = (f"{settings.ADO_ORG_URL.rstrip('/')}/{quote(project)}"
           f"/_apis/wit/workitemtypes/Task/fields?api-version={_API_VERSION}")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=_auth_header())
    except httpx.HTTPError as exc:
        raise AzureDevOpsError(f"Could not reach Azure DevOps: {exc.__class__.__name__}") from exc
    if resp.status_code in (401, 203) or "text/html" in resp.headers.get("content-type", ""):
        raise AzureDevOpsError("Azure DevOps rejected the credentials — check ADO_PAT (expired or wrong scope?).")
    if resp.status_code >= 400:
        raise AzureDevOpsError(f"Could not read the Task type in project '{project}' (HTTP {resp.status_code}).")
    return any(f.get("referenceName") == _DUE_DATE_FIELD for f in resp.json().get("value", []))


def build_description(
    description: str,
    owner_name: Optional[str],
    due_text: Optional[str],
    due_date: Optional[date],
    session_name: Optional[str],
    call_date: Optional[date],
    mom_url: str,
) -> str:
    """HTML body for System.Description. Everything user-supplied is escaped."""
    e = lambda s: html.escape(s or "")
    due = due_date.isoformat() if due_date else ""
    if due_text and due_text != due:
        due = f"{due} ({e(due_text)})" if due else e(due_text)
    parts = [
        f"<p>{e(description)}</p>",
        "<p>",
        f"<b>Owner (as in MOM):</b> {e(owner_name) or '—'}<br/>",
        f"<b>Due:</b> {due or '—'}<br/>",
        f"<b>Meeting:</b> {e(session_name)}{' · ' + call_date.isoformat() if call_date else ''}<br/>",
        f'<b>Minutes:</b> <a href="{e(mom_url)}">{e(mom_url)}</a>',
        "</p>",
    ]
    return "".join(parts)


async def create_task(
    project: str,
    title: str,
    description_html: str,
    area_path: str,
    iteration_path: Optional[str],
    assignee_email: Optional[str],
    due_date: Optional[date],
) -> Tuple[int, str]:
    """Create one Task. Returns (work item id, web URL). Raises AzureDevOpsError on rejection."""
    if not settings.ADO_ENABLED:
        raise AzureDevOpsError("Azure DevOps integration is disabled (ADO_ENABLED=false).")

    ops = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {"op": "add", "path": "/fields/System.Description", "value": description_html},
        {"op": "add", "path": "/fields/System.AreaPath", "value": area_path},
    ]
    if iteration_path:
        ops.append({"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path})
    if assignee_email:
        ops.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assignee_email})
    if due_date:
        ops.append({"op": "add", "path": f"/fields/{_DUE_DATE_FIELD}", "value": due_date.isoformat()})

    url = (f"{settings.ADO_ORG_URL.rstrip('/')}/{quote(project)}"
           f"/_apis/wit/workitems/$Task?api-version={_API_VERSION}")
    headers = {**_auth_header(), "Content-Type": "application/json-patch+json"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=ops, headers=headers)
    except httpx.HTTPError as exc:
        raise AzureDevOpsError(f"Could not reach Azure DevOps: {exc.__class__.__name__}") from exc

    # An expired/invalid PAT gets a 203 + HTML sign-in page rather than a 401.
    if resp.status_code in (401, 203) or "text/html" in resp.headers.get("content-type", ""):
        raise AzureDevOpsError("Azure DevOps rejected the credentials — check ADO_PAT (expired or wrong scope?).")
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("message") or resp.text
        except ValueError:
            msg = resp.text
        raise AzureDevOpsError(f"Azure DevOps error {resp.status_code}: {msg[:500]}")

    body = resp.json()
    work_item_id = body["id"]
    web_url = (body.get("_links", {}).get("html", {}).get("href")
               or f"{settings.ADO_ORG_URL.rstrip('/')}/{quote(project)}/_workitems/edit/{work_item_id}")
    logger.info(f"Created ADO Task {work_item_id} in project '{project}'")
    return work_item_id, web_url
