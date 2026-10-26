"""
OFFLINE test for the Mantis client and its tracker dispatch. Makes NO changes and costs NOTHING —
no network (Mantis is faked with httpx.MockTransport), no database, no LLM.

WHAT IT PROVES
--------------
  find_project / pick_category    case-insensitive, subprojects searched, exact server spelling kept
  username_for                    email prefix → Mantis username
  build_description               plain text, no blank values, link to the MOM
  project_info                    unknown project / category → MantisError
  create_issue                    token sent as-is (no "Bearer"); Task Type sent only when linked; handler =
                                  email prefix; unknown handler → retried unassigned + warning;
                                  other errors still fail; 401 message names the token + .htaccess
  trackers                        customer.tracker = "mantis" routes here; incomplete target → not configured

Usage (from the Backend/ directory):
    python test_mantis_client.py
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
from app.services import mantis, trackers  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# ── Fake HTTP ────────────────────────────────────────────────────────────────
calls: list[tuple[str, str, dict | None, str | None]] = []
_RealClient = httpx.AsyncClient


def install(handler):
    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body, request.headers.get("authorization")))
        return handler(request, body)

    mantis.httpx.AsyncClient = lambda **kw: _RealClient(transport=httpx.MockTransport(transport), **kw)


settings.MANTIS_ENABLED = True
settings.MANTIS_API_URL = "https://task.example.com/api/rest"
settings.MANTIS_API_TOKEN = "tok123"

PROJECTS = {"projects": [
    {"id": 1, "name": "Other", "categories": [], "custom_fields": [],
     "subProjects": [
         {"id": 3, "name": "NFC Intellistaff",
          "categories": [{"id": 1, "name": "General"}],
          "custom_fields": [{"id": 2, "name": "Task Type"}, {"id": 1, "name": "Estimate"}]},
     ]},
    {"id": 4, "name": "No Type", "categories": [{"id": 1, "name": "General"}], "custom_fields": []},
]}

# ── Pure helpers ─────────────────────────────────────────────────────────────
proj = mantis.find_project(PROJECTS["projects"], "nfc intellistaff ")
check("subproject found", proj and proj["id"], 3)
check("missing project", mantis.find_project(PROJECTS["projects"], "Nope"), None)
check("category exact spelling", mantis.pick_category(proj, "general"), "General")
check("missing category", mantis.pick_category(proj, "Bugs"), None)
check("username prefix", mantis.username_for("JDoe@nfcsolutionsusa.com"), "JDoe")
check("no email", mantis.username_for(None), None)
check("site url", mantis.site_url(), "https://task.example.com")
settings.MANTIS_API_URL = "https://task.example.com/api/rest/index.php"
check("site url (index.php)", mantis.site_url(), "https://task.example.com")
settings.MANTIS_API_URL = "https://task.example.com/api/rest"

desc = mantis.build_description("", None, "one day", date(2026, 10, 2), "Grooming", date(2026, 10, 1), "http://app/minutes/1")
check("description first line", desc.splitlines()[0], "—")
check("description due", "Due: 2026-10-02 (one day)" in desc, True)
check("description link", desc.endswith("Minutes: http://app/minutes/1"), True)


def mantis_handler(known_users=("jdoe",), create_error=None):
    def handle(request, body):
        p = request.url.path
        if p == "/api/rest/projects" and request.method == "GET":
            return httpx.Response(200, json=PROJECTS)
        if p == "/api/rest/issues" and request.method == "POST":
            if create_error:
                return httpx.Response(create_error[0], json={"message": create_error[1]})
            handler_ = (body.get("handler") or {}).get("name")
            if handler_ and handler_ not in known_users:
                return httpx.Response(400, json={"message": f"User '{handler_}' not found", "code": 811})
            return httpx.Response(201, json={"issue": {"id": 1234, "summary": body["summary"]}})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {p}"})
    return handle


async def expect_error(label, coro, fragment):
    try:
        await coro
        failures.append(f"{label}: expected MantisError")
    except mantis.MantisError as e:
        check(f"{label} message", fragment in str(e), True)


async def run():
    # 1. Happy path: validated target, Task Type, assignee, due date, token sent as-is.
    calls.clear()
    install(mantis_handler())
    info = await mantis.project_info("NFC Intellistaff", "general")
    check("info", (info.project_id, info.category), (3, "General"))
    key, url, warn = await mantis.create_issue(info, "T", "D", "jdoe@nfcsolutionsusa.com", date(2026, 10, 31))
    check("created", (key, url, warn), ("1234", "https://task.example.com/view.php?id=1234", None))
    post = [b for m, p, b, a in calls if m == "POST"][0]
    check("auth header as-is", calls[0][3], "tok123")
    check("project by id", post["project"], {"id": 3})
    check("category", post["category"], {"name": "General"})
    check("task type", post["custom_fields"], [{"field": {"name": "Task Type"}, "value": "Task"}])
    check("handler", post["handler"], {"name": "jdoe"})
    check("due date", post["due_date"], "2026-10-31")
    check("no priority/severity", ("priority" in post, "severity" in post), (False, False))

    # 2. Unknown assignee → retried without handler, created with a warning.
    calls.clear()
    key, _, warn = await mantis.create_issue(info, "T", "D", "ghost@x.com", None)
    posts = [b for m, p, b, a in calls if m == "POST"]
    check("unknown assignee: two posts", len(posts), 2)
    check("retry has no handler", "handler" in posts[1], False)
    check("unknown assignee: created", key, "1234")
    check("unknown assignee warning", warn is not None and "ghost" in warn and "not found" in warn, True)

    # 3. No assignee → single post, no handler, no warning.
    calls.clear()
    _, _, warn = await mantis.create_issue(info, "T", "D", None, None)
    posts = [b for m, p, b, a in calls if m == "POST"]
    check("no assignee: one post, no handler", (len(posts), "handler" in posts[0], warn), (1, False, None))

    # 4. A 5xx is not retried — it fails the item.
    calls.clear()
    install(mantis_handler(create_error=(500, "boom")))
    await expect_error("5xx", mantis.create_issue(info, "T", "D", "jdoe@x.com", None), "boom")
    check("5xx not retried", len([1 for m, *_ in calls if m == "POST"]), 1)

    # 5. Preflight failures.
    install(mantis_handler())
    await expect_error("missing project", mantis.project_info("Nope", "General"), "'Nope' was not found")
    await expect_error("missing category", mantis.project_info("NFC Intellistaff", "Bugs"), "no category 'Bugs'")
    calls.clear()
    nt = await mantis.project_info("No Type", "General")
    check("task type not linked: info", nt.has_task_type, False)
    await mantis.create_issue(nt, "T", "D", None, None)
    check("task type not linked: field omitted", "custom_fields" in [b for m, p, b, a in calls if m == "POST"][0], False)
    install(lambda request, body: httpx.Response(401, json={}))
    await expect_error("401", mantis.project_info("NFC Intellistaff", "General"), ".htaccess")
    install(lambda request, body: httpx.Response(404, text="Not Found"))
    await expect_error("404 hint", mantis.project_info("NFC Intellistaff", "General"), "index.php")
    install(lambda request, body: httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"}))
    await expect_error("html page", mantis.project_info("NFC Intellistaff", "General"), "non-JSON")

    # ── trackers dispatch ────────────────────────────────────────────────────
    cust = SimpleNamespace(tracker="mantis", mantis_project="NFC Intellistaff", mantis_category="General")
    check("mantis configured", trackers.is_configured(cust), True)
    check("mantis w/o category", trackers.is_configured(
        SimpleNamespace(tracker="mantis", mantis_project="P", mantis_category=None)), False)
    item = SimpleNamespace(title="T", description="D", owner_name="J", due_text=None,
                           due_date=None, assignee_email="jdoe@x.com")
    install(mantis_handler())
    ctx = await trackers.prepare(cust)
    check("mantis via trackers", (await trackers.create(cust, ctx, item, "S", None, "u"))[0], "1234")
    install(lambda request, body: httpx.Response(401, json={}))
    try:
        await trackers.prepare(cust)
        failures.append("trackers: expected TrackerError")
    except trackers.TrackerError:
        pass
    settings.MANTIS_ENABLED = False
    check("disabled reason", "MANTIS_ENABLED" in trackers.not_configured_reason(cust), True)


asyncio.run(run())

if failures:
    print(f"FAIL — {len(failures)} check(s):")
    for f_ in failures:
        print("  -", f_)
    sys.exit(1)
print("PASS — all Mantis client / tracker dispatch checks")
