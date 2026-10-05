"""
OFFLINE test for the Jira client and the tracker dispatch. Makes NO changes and costs NOTHING —
no network (Jira and Azure DevOps are faked with httpx.MockTransport), no database, no LLM.

WHAT IT PROVES
--------------
  pick_task_type / pick_account   strict choices: only a non-subtask "Task"; only one active human
                                  account per email (ambiguous → None, never a guess)
  build_description_adf           valid ADF, no empty text nodes, link to the MOM
  create_issue                    assignee resolved BEFORE create (unknown person → no stray issue);
                                  duedate/assignee sent only when the create screen has them;
                                  assign-after-create when Assignee is not on the screen
  trackers                        same item routed to ADO or Jira by customer.tracker; ADO skips the
                                  Due Date field when the project lacks it; errors become TrackerError

Usage (from the Backend/ directory):
    python test_jira_client.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import azure_devops, jira, trackers  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# ── Fake HTTP: route every client the modules create through a recorder ──────
calls: list[tuple[str, str, dict | None]] = []
_RealClient = httpx.AsyncClient


def install(handler):
    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        return handler(request, body)

    factory = lambda **kw: _RealClient(transport=httpx.MockTransport(transport), **kw)  # noqa: E731
    jira.httpx.AsyncClient = factory          # both modules import the same httpx module object,
    azure_devops.httpx.AsyncClient = factory  # so this patches it for both


settings.JIRA_ENABLED, settings.JIRA_BASE_URL = True, "https://example.atlassian.net"
settings.JIRA_EMAIL, settings.JIRA_API_TOKEN = "bot@example.com", "token"
settings.ADO_ENABLED, settings.ADO_ORG_URL, settings.ADO_PAT = True, "https://dev.azure.com/org", "pat"

# ── Pure helpers ─────────────────────────────────────────────────────────────
types_ = [{"id": "5", "name": "Sub-task", "subtask": True}, {"id": "10", "name": "Story"},
          {"id": "3", "name": "Task", "subtask": False}]
check("task type picked", jira.pick_task_type(types_), "3")
check("no task type", jira.pick_task_type([{"id": "1", "name": "Bug"}]), None)
check("subtask named Task ignored", jira.pick_task_type([{"id": "9", "name": "Task", "subtask": True}]), None)

amol = {"accountId": "a1", "accountType": "atlassian", "active": True, "emailAddress": "amol@x.com"}
check("exact email", jira.pick_account([amol], "AMOL@x.com"), "a1")
check("hidden email, single person", jira.pick_account([{"accountId": "h", "accountType": "atlassian"}], "z@x.com"), "h")
check("hidden email, two people", jira.pick_account([{"accountId": "h1", "accountType": "atlassian"},
                                                      {"accountId": "h2", "accountType": "atlassian"}], "z@x.com"), None)
check("app account ignored", jira.pick_account([{"accountId": "bot", "accountType": "app"}], "z@x.com"), None)
check("inactive ignored", jira.pick_account([{**amol, "active": False}], "amol@x.com"), None)
check("visible but different email", jira.pick_account([{**amol, "emailAddress": "other@x.com"}], "amol@x.com"), None)
check("no results", jira.pick_account([], "amol@x.com"), None)

adf = jira.build_description_adf("Do the thing.", None, None, None, None, None, "http://app/minutes/1")
texts = [n for p in adf["content"] for n in p["content"] if n["type"] == "text"]
check("ADF doc type", (adf["type"], adf["version"]), ("doc", 1))
check("ADF has no empty text nodes", all(n["text"] for n in texts), True)
check("ADF link mark", texts[-1].get("marks", [{}])[0].get("attrs", {}).get("href"), "http://app/minutes/1")
adf = jira.build_description_adf("x", "Amol", "one day", date(2026, 10, 2), "Grooming", date(2026, 10, 1), "u")
check("ADF due text", "2026-10-02 (one day)" in json.dumps(adf), True)


# ── create_issue against a fake Jira ─────────────────────────────────────────
def jira_handler(screen_fields, users):
    def handle(request, body):
        p = request.url.path
        if p.endswith("/createmeta/CAL/issuetypes"):
            return httpx.Response(200, json={"issueTypes": types_})
        if p.endswith("/createmeta/CAL/issuetypes/3"):
            return httpx.Response(200, json={"fields": [{"fieldId": f} for f in screen_fields]})
        if p == "/rest/api/3/user/search":
            return httpx.Response(200, json=users.get(request.url.params["query"], []))
        if p == "/rest/api/3/issue" and request.method == "POST":
            return httpx.Response(201, json={"id": "100", "key": "CAL-7"})
        if p.endswith("/assignee") and request.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(404, json={"errorMessages": [f"unexpected {request.method} {p}"]})
    return handle


async def run():
    users = {"amol@x.com": [amol]}

    # 1. Full create screen: duedate + assignee go in the create call itself.
    calls.clear()
    install(jira_handler(["summary", "duedate", "assignee"], users))
    info = await jira.project_info("CAL")
    check("info flags", (info.task_type_id, info.has_duedate, info.has_assignee), ("3", True, True))
    key, url, warn = await jira.create_issue(info, "T", {"type": "doc"}, "amol@x.com", date(2026, 10, 2))
    check("created key/url", (key, url, warn), ("CAL-7", "https://example.atlassian.net/browse/CAL-7", None))
    fields = [b for m, p, b in calls if m == "POST"][0]["fields"]
    check("duedate sent", fields.get("duedate"), "2026-10-02")
    check("assignee sent", fields.get("assignee"), {"accountId": "a1"})
    check("issuetype by id", fields["issuetype"], {"id": "3"})
    check("no labels", "labels" in fields, False)

    # 2. Minimal create screen: no duedate field sent; assignee set by a second call.
    calls.clear()
    install(jira_handler(["summary"], users))
    info = await jira.project_info("CAL")
    key, _, warn = await jira.create_issue(info, "T", {"type": "doc"}, "amol@x.com", date(2026, 10, 2))
    fields = [b for m, p, b in calls if m == "POST"][0]["fields"]
    check("duedate omitted", "duedate" in fields, False)
    check("assignee omitted from create", "assignee" in fields, False)
    check("assigned afterwards", [b for m, p, b in calls if m == "PUT"], [{"accountId": "a1"}])

    # 3. Unknown assignee: fails BEFORE any issue is created.
    calls.clear()
    install(jira_handler(["summary", "assignee"], users))
    info = await jira.project_info("CAL")
    try:
        await jira.create_issue(info, "T", {"type": "doc"}, "ghost@x.com", None)
        failures.append("unknown assignee: expected JiraError")
    except jira.JiraError as e:
        check("unknown assignee message", "ghost@x.com" in str(e), True)
    check("no issue created for unknown assignee", [m for m, p, b in calls if m == "POST"], [])

    # 4. Bad token → clear message.
    install(lambda request, body: httpx.Response(401, json={}))
    try:
        await jira.project_info("CAL")
        failures.append("401: expected JiraError")
    except jira.JiraError as e:
        check("401 message", "JIRA_API_TOKEN" in str(e), True)

    # 5. Project without a Task type.
    install(lambda request, body: httpx.Response(200, json={"issueTypes": [{"id": "1", "name": "Bug"}]}))
    try:
        await jira.project_info("CAL")
        failures.append("no Task type: expected JiraError")
    except jira.JiraError as e:
        check("no Task type message", "no 'Task' issue type" in str(e), True)

    # ── trackers dispatch ────────────────────────────────────────────────────
    item = SimpleNamespace(title="T", description="D", owner_name="Amol", due_text=None,
                           due_date=date(2026, 10, 2), assignee_email="amol@x.com")
    jira_cust = SimpleNamespace(tracker="jira", jira_project_key="CAL")
    ado_cust = SimpleNamespace(tracker="ado", ado_project="P", ado_area_path="P", ado_iteration_path=None)
    check("jira configured", trackers.is_configured(jira_cust), True)
    check("ado configured", trackers.is_configured(ado_cust), True)
    check("none configured", trackers.is_configured(SimpleNamespace(tracker=None)), False)
    check("jira w/o key", trackers.is_configured(SimpleNamespace(tracker="jira", jira_project_key=None)), False)

    install(jira_handler(["summary", "duedate", "assignee"], users))
    ctx = await trackers.prepare(jira_cust)
    check("jira via trackers", (await trackers.create(jira_cust, ctx, item, "S", None, "u"))[0], "CAL-7")

    def ado_handler(has_due):
        def handle(request, body):
            if request.method == "GET":   # Task fields
                refs = ["System.Title"] + (["Microsoft.VSTS.Scheduling.DueDate"] if has_due else [])
                return httpx.Response(200, json={"value": [{"referenceName": r} for r in refs]})
            return httpx.Response(200, json={"id": 1226, "_links": {"html": {"href": "https://ado/1226"}}})
        return handle

    for has_due in (False, True):
        calls.clear()
        install(ado_handler(has_due))
        ctx = await trackers.prepare(ado_cust)
        key, url, _ = await trackers.create(ado_cust, ctx, item, "S", None, "u")
        ops = [b for m, p, b in calls if m == "POST"][0]
        sent_due = any(o["path"].endswith("DueDate") for o in ops)
        check(f"ado key (has_due={has_due})", (key, url), ("1226", "https://ado/1226"))
        check(f"ado DueDate sent only when field exists (has_due={has_due})", sent_due, has_due)

    install(lambda request, body: httpx.Response(401, json={}))
    try:
        await trackers.prepare(jira_cust)
        failures.append("trackers: expected TrackerError")
    except trackers.TrackerError:
        pass


asyncio.run(run())

if failures:
    print(f"FAIL — {len(failures)} check(s):")
    for f_ in failures:
        print("  -", f_)
    sys.exit(1)
print("PASS — all Jira client / tracker dispatch checks")
