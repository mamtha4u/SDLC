"""Quinn's live run records scenario by scenario (10-05: 27 results in one submit_live were dropped by the model 9 times;
the user also wants to see "TC-07 running · 6/27 done"). The report and the sign-off are built from the records, with
each scenario in plain words. No AWS, no real model."""
import asyncio
import json

from app.agents import livekit, qa
from app.agents.base import Tool, obj
from app.services import aws_access
from app.services.storage import ProjectStore
from tests.test_codereview import _model, _project, captured  # noqa: F401  (fixture)
from tests.test_signoff_testing import PLAN


def test_quinn_records_each_scenario_and_the_report_is_built_from_them(client, captured, monkeypatch):  # noqa: F811
    c, pid = _project("liveqa")
    store = ProjectStore(pid)
    cases = [{**x, "flow": "1. Send the order. 2. Read the queue.", "example": "ORD1 → {\"order_id\": \"ORD1\"} in orders.fifo"} for x in PLAN["cases"]]
    store.write(qa.PLAN_JSON, json.dumps({**PLAN, "cases": cases, "version": "v1", "basis": qa.plan_basis(pid), "status": "approved",
                                          "approved_at": "2026-10-05 11:31 UTC"}))
    monkeypatch.setattr(aws_access, "assume", lambda pid, agent: {})

    def tools(pid, agent, creds, outputs, box):  # one stand-in live call (counts as evidence)
        async def call(a):
            box["calls"] = box.get("calls", 0) + 1
            return "200"
        return [Tool("http_request", "Call.", obj({"url": {"type": "string"}}), call)]
    monkeypatch.setattr(livekit, "tools", tools)
    rec = lambda i, s, saw="": {"name": "record_scenario", "input": {"id": i, "status": s, "title": "", "did": "Sent the order" if saw else "", "saw": saw}}  # noqa: E731
    submit = {"name": "submit_live", "input": {"summary": "1 of 2 passed.", "bugs": [{"check_id": "TC-02", "title": "Missing id accepted",
              "severity": "major", "area": "code", "steps": "POST", "expected": "400", "actual": "200"}], "tickets": [],
              "left_for_user": "", "risks": [], "signoff": ""}}
    _model(monkeypatch, [rec("TC-01", "running"), {"name": "http_request", "input": {"url": "x"}}, rec("TC-01", "passed", "200, JSON in the queue"),
                         submit,  # TC-02 not recorded yet: refused
                         rec("TC-02", "failed", "200 instead of 400"), submit])
    asyncio.run(qa.live(qa.JobContext("job_q", pid, {}, {}, None)))
    t = c.get(f"/api/projects/{pid}/testing").json()
    assert t["progress"]["done"] == 2 and t["progress"]["passed"] == 1 and t["progress"]["total"] == 2
    live = qa.load_live(pid)
    assert [(x["id"], x["passed"]) for x in live["checks"]] == [("TC-01", True), ("TC-02", False)]
    assert live["checks"][0]["did"] == "Sent the order" and live["checks"][0]["expected"] == PLAN["cases"][0]["expected"]
    report = store.read("reports/live_qa.md").decode()
    assert "## Scenario by scenario" in report and "**What Quinn did:** Sent the order" in report
    signoff_doc = store.read("signoff/10-test-signoff.md").decode()
    assert "## What was tested, step by step" in signoff_doc and "**The flow:** 1. Send the order" in signoff_doc and "**Example:**" in signoff_doc
    assert "**Example:**" in qa.plan_markdown({**PLAN, "cases": cases, "version": "v1"})
