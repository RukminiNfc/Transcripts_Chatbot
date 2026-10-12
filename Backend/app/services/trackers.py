"""One interface over the two task trackers (Azure Boards, Jira) for MOM action items.

The router never talks to a tracker directly:

    is_configured(customer)          enabled in .env AND the customer's target is set
    ctx = await prepare(customer)    per-approval preflight; raises TrackerError BEFORE anything
                                     is approved or claimed, so an unreachable tracker fails cleanly
    await create(customer, ctx, …)   one Task → (key, url, warning)

Each customer uses exactly one tracker (customers.tracker).
"""
from datetime import date
from typing import Any, Optional, Tuple

from app.core.config import settings
from app.models.database import Customer, MOMActionItem
from app.services import azure_devops, jira

LABELS = {"ado": "Azure Boards", "jira": "Jira"}


class TrackerError(Exception):
    """A tracker rejected or could not be reached. Message is admin-readable."""


def is_configured(customer: Optional[Customer]) -> bool:
    if not customer:
        return False
    if customer.tracker == "ado":
        return settings.ADO_ENABLED and bool(customer.ado_project and customer.ado_area_path)
    if customer.tracker == "jira":
        return settings.JIRA_ENABLED and bool(customer.jira_project_key)
    return False


def not_configured_reason(customer: Optional[Customer]) -> str:
    if not customer or not customer.tracker:
        return "This project has no task tracker selected. Choose Azure Boards or Jira in Admin → Customer Settings."
    label = LABELS.get(customer.tracker, customer.tracker)
    flag = "ADO_ENABLED" if customer.tracker == "ado" else "JIRA_ENABLED"
    if not getattr(settings, flag):
        return f"{label} integration is disabled ({flag}=false in .env)."
    return f"This project's {label} target is incomplete. Fix it in Admin → Customer Settings."


async def prepare(customer: Customer) -> Any:
    """Per-approval preflight. ADO → whether Task has a Due Date field; Jira → project create-screen info."""
    try:
        if customer.tracker == "ado":
            return await azure_devops.task_has_due_date_field(customer.ado_project)
        if customer.tracker == "jira":
            return await jira.project_info(customer.jira_project_key)
    except (azure_devops.AzureDevOpsError, jira.JiraError) as exc:
        raise TrackerError(str(exc)) from exc
    raise TrackerError(not_configured_reason(customer))


async def create(
    customer: Customer,
    ctx: Any,
    item: MOMActionItem,
    session_name: Optional[str],
    call_date: Optional[date],
    mom_url: str,
) -> Tuple[str, str, Optional[str]]:
    """Create one Task for `item`. Returns (external key, URL, warning-or-None). Raises TrackerError."""
    desc = dict(
        description=item.description or item.title,
        owner_name=item.owner_name,
        due_text=item.due_text,
        due_date=item.due_date,
        session_name=session_name,
        call_date=call_date,
        mom_url=mom_url,
    )
    try:
        if customer.tracker == "ado":
            work_item_id, url = await azure_devops.create_task(
                project=customer.ado_project,
                title=item.title,
                description_html=azure_devops.build_description(**desc),
                area_path=customer.ado_area_path,
                iteration_path=customer.ado_iteration_path,
                assignee_email=item.assignee_email,
                due_date=item.due_date if ctx else None,   # ctx = Task has a Due Date field
            )
            return str(work_item_id), url, None
        if customer.tracker == "jira":
            return await jira.create_issue(
                info=ctx,
                title=item.title,
                description_adf=jira.build_description_adf(**desc),
                assignee_email=item.assignee_email,
                due_date=item.due_date,
            )
    except (azure_devops.AzureDevOpsError, jira.JiraError) as exc:
        raise TrackerError(str(exc)) from exc
    raise TrackerError(not_configured_reason(customer))
