"""Archie's code review before the deploy (and the user's questions to him), "accept: nothing to rebuild", documentation-only
design changes, tasks given to an agent through a ticket, and Terra's plan document. A fake model: no AI calls, no AWS."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents import base, codereview, ta, tp
from app.db.base import SessionLocal
from app.db.models import ChangeRequest
from app.main import app
from app.orchestrator import flow
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext
from app.services import aws_access
from app.services import tickets as tk
from app.services.storage import ProjectStore
from tests.conftest import signup

CHECKS = [{"area": a, "item": f"{a} ok", "ok": True, "note": "src/transform/handler.py"} for a in ("design", "mapping", "error handling", "logging", "tests")]
NOTES = [{"topic": t, "now": "plain functions", "recommendation": "keep", "why": "small Lambda"} for t in codereview.TOPICS[:4]]
# Archie asks the user (10-05): structure always, plus what matters for this code; suggestions to accept or skip
CHOICES = [{"topic": "structure", "question": "Plain functions or classes?", "options": ["Plain functions", "Classes"],
            "current": "Plain functions", "recommended": "Plain functions", "why": "no state to keep"},
           {"topic": "docstrings", "question": "Docstrings on every public function?", "options": ["Every public function", "Only where unclear"],
            "current": "Only where unclear", "recommended": "Every public function", "why": "the team reads them in the console"}]
SUGG = [{"title": "Create the S3 client once", "detail": "Move boto3.client('s3') to module level in handler.py.",
         "example": "s3 = boto3.client('s3')  # module level", "benefit": "warm invocations reuse it"}]


@pytest.fixture
def captured(monkeypatch):
    jobs: list[tuple[str, dict]] = []

    async def enqueue(kind, project_id=None, **payload):
        jobs.append((kind, payload))
        return "job_x"
    monkeypatch.setattr(runner_mod.runner, "enqueue", enqueue)
    return jobs


def _model(monkeypatch, replies: list):
    """A fake model: each call answers with the next reply (a tool call dict, or text)."""
    calls = []

    async def fake_call(*, messages, **_):
        calls.append(messages)
        r = replies.pop(0)
        if isinstance(r, str):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=r)], stop_reason="end_turn"), {}
        use = SimpleNamespace(type="tool_use", id=f"t{len(calls)}", name=r["name"], input=r["input"])
        return SimpleNamespace(content=[use], stop_reason="tool_use"), {}
    monkeypatch.setattr(base.llm, "call", fake_call)
    return calls


def _project(name: str) -> tuple[TestClient, str]:
    c = TestClient(app)
    signup(c, name)
    pid = c.post("/api/projects", json={"name": name.title()}).json()["id"]
    s = ProjectStore(pid)
    s.write("src/transform/handler.py", "def lambda_handler(event, context):\n    return {'statusCode': 200}\n")
    s.write("tests/test_handler.py", "def test_ok():\n    assert True\n")
    s.write("reports/code.json", json.dumps({"summary": "Handler", "notes": [], "changes": "", "files": ["src/transform/handler.py"], "version": "v1",
                                             "test_runs": 1, "coverage": 95.0, "coverage_gate": 80, "fixed_bugs": []}))
    s.write("reports/pytest.json", json.dumps({"total": 1, "passed": 1, "failed": 0, "error": 0, "tests": [],
                                               "coverage": {"percent": 95.0, "files": {"src/transform/handler.py": {"percent": 95.0, "missing": [3]}}}}))
    return c, pid


def test_archie_reviews_dev_code_then_the_user_decides(client, captured, monkeypatch):
    c, pid = _project("reviewer")
    must = {"severity": "must", "file": "src/transform/handler.py", "issue": "XML parser allows external entities", "fix": "use resolve_entities=False"}
    _model(monkeypatch, [{"name": "submit_code_review", "input": {"verdict": "changes", "summary": "One must-fix.", "checks": CHECKS,
                                                                  "findings": [must], "design_notes": NOTES, "choices": CHOICES, "suggestions": SUGG, "for_user": ["handler.py", "coverage report"]}}])
    asyncio.run(codereview.code_review(JobContext("job_r1", pid, {"tickets": [], "changes": ""}, {}, None)))
    kind, payload = captured[-1]
    assert kind == "de.code" and "XML parser allows external entities" in payload["feedback"]  # round 1: straight back to Dev
    _model(monkeypatch, [
        {"name": "submit_code_review", "input": {"verdict": "approve", "summary": "Built to the LLD.", "checks": CHECKS, "findings": [must],
                                                 "design_notes": NOTES, "choices": CHOICES, "suggestions": SUGG, "for_user": ["a", "b"]}},  # approve + must: rejected by the verifier
        {"name": "submit_code_review", "input": {"verdict": "approve", "summary": "Built to the LLD.", "checks": CHECKS,
                                                 "findings": [{**must, "severity": "nice", "issue": "rename x"}], "design_notes": NOTES, "choices": CHOICES, "suggestions": SUGG,
                                                 "for_user": ["src/transform/handler.py", "the coverage report"]}}])
    asyncio.run(codereview.code_review(JobContext("job_r2", pid, {"tickets": [], "changes": "fixed XXE"}, {}, None)))
    r = c.get(f"/api/projects/{pid}/code-review").json()
    assert r["review"]["verdict"] == "approve" and r["review"]["round"] == 2 and r["pending"]["title"].startswith("Archie approved Dev's code")
    md = ProjectStore(pid).read(codereview.REVIEW_MD).decode()
    assert "✅ Approved: fit to deploy" in md and "## Archie's recommendations" in md and "Round 1" in md and "Ask Archie" in md
    assert c.get(f"/api/projects/{pid}/build").json()["code"]["review"]["verdict"] == "approve"

    # the user asks Archie, he answers from the code; a second question waits for the answer
    assert c.post(f"/api/projects/{pid}/code-review/ask", json={"question": "Classes or functions here?"}).status_code == 202
    assert captured[-1][0] == "ta.review_chat"
    assert c.post(f"/api/projects/{pid}/code-review/ask", json={"question": "and docstrings?"}).status_code == 409
    calls = _model(monkeypatch, ["Plain functions: `lambda_handler` in src/transform/handler.py has no state to keep."])
    asyncio.run(codereview.review_chat(JobContext("job_c", pid, {}, {}, None)))
    chat = c.get(f"/api/projects/{pid}/code-review").json()["chat"]
    assert [m["role"] for m in chat] == ["user", "archie"] and "Plain functions" in chat[1]["text"]
    assert calls[0][-1]["content"][0]["text"] == "Classes or functions here?" if isinstance(calls[0][-1]["content"], list) else True
    assert "Classes or functions here?" in codereview.discussion(pid)  # goes to Dev with a change request

    # "All fine: deploy it" → Dev deploys, with the tickets from the review record
    asyncio.run(flow.decide(pid, r["pending"]["id"], "approve", ""))
    assert captured[-1][0] == "de.deploy"
    assert "signoff/07-code-review.md" in {f["path"] for f in ProjectStore(pid).tree()}


def test_accept_nothing_to_rebuild_and_documentation_only_designs(client, captured):
    c, pid = _project("acceptor")
    s = ProjectStore(pid)
    a = asyncio.run(flow.request_approval(pid, "design", "Archie's design (HLD, LLD, diagram)", "…", []))
    r = c.post(f"/api/projects/{pid}/approvals/{a.id}", json={"decision": "accept"})
    assert r.status_code == 409  # nothing after the design is built yet: approving is the only way on
    assert not next(x for x in c.get(f"/api/projects/{pid}/approvals").json() if x["id"] == a.id)["can_accept"]
    s.write("infra/plan_preview.json", json.dumps({"resources": [], "validate": {"ok": True}}))  # Terra's work exists
    assert next(x for x in c.get(f"/api/projects/{pid}/approvals").json() if x["id"] == a.id)["can_accept"]
    n = len(captured)
    r = c.post(f"/api/projects/{pid}/approvals/{a.id}", json={"decision": "accept"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and len(captured) == n  # nobody downstream starts
    assert "Accepted" in c.get(f"/api/projects/{pid}").json()["last_activity"]

    prev = {"lld_markdown": "# LLD", "hld_markdown": "# HLD", "resources": [{"name": "q"}], "quality_gates": {"min_coverage_percent": 80}}
    assert ta.docs_only(pid, prev, {**prev, "hld_markdown": "# HLD (reworded)"})  # wording only: no gate, nothing redone
    assert not ta.docs_only(pid, prev, {**prev, "lld_markdown": "# LLD with a new queue"})
    assert not ta.docs_only(pid, prev, {**prev, "resources": [{"name": "q"}, {"name": "dlq"}]})
    assert not ta.docs_only(pid, None, prev)


def test_a_task_for_an_agent_is_a_ticket_and_closes_when_done(client, captured):
    c, pid = _project("tasker")

    async def signed():
        from app.db.models import Intake

        async with SessionLocal() as db:
            i = await db.get(Intake, pid)
            if i is None:
                db.add(Intake(project_id=pid, status="signed_off", requirement_md="# R", answers={}, uploads=[], chat=[], rounds=[], decisions={}, gap_answers={}))
            else:
                i.status, i.busy = "signed_off", None
            await db.commit()
    asyncio.run(signed())
    t = c.post(f"/api/projects/{pid}/tickets", json={"title": "Add a DLQ monitoring section to the LLD", "assignee": "ta", "area": "design",
                                                     "severity": "minor"}).json()
    assert t["routed"].startswith("change request CR-")

    async def done():
        async with SessionLocal() as db:
            from sqlalchemy import select

            cr = (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == pid))).scalars().one()
            assert cr.source == f"ticket:{t['label']}"
            cr.status = "done"
            await db.commit()
        return await tk.close_for_crs(pid, "ta", "Done: the LLD has the section.")
    assert asyncio.run(done()) == [t["label"]]
    t2 = c.get(f"/api/projects/{pid}/tickets/{t['id']}").json()
    assert t2["status"] == "closed" and "Done: the LLD has the section." in t2["comments"][-1]["text"]


def test_terras_plan_document_says_what_was_checked_and_what_approving_does():
    md = tp.plan_markdown({"version": "v1", "role": "orkestra-x-terra", "counts": {"create": 2, "update": 0, "replace": 1, "delete": 0},
                           "planned_at": "2026-10-02T17:00:00Z", "changes": [
                               {"action": "create", "address": "aws_lambda_function.transform", "name": "orkestra-x-transform", "fields": []},
                               {"action": "replace", "address": "aws_sqs_queue.orders", "name": "orkestra-x-orders.fifo", "fields": ["fifo_queue"]}],
                           "checks": {"validate": True, "state_bucket": "orkestra-tfstate-x", "placeholder": True, "monthly_usd": 3.76}})
    assert "ready for `terraform apply`" in md and "Nothing has changed in AWS yet" in md and "✅ `terraform validate`" in md
    assert "## ⚠️ Removed or replaced" in md and "**replace** `aws_sqs_queue.orders`" in md and "$3.76/month" in md
    assert "1. Terra runs `terraform apply`" in md and "placeholder code" in md


def test_an_on_demand_review_opens_no_gate_and_parked_tasks_resume(client, captured, monkeypatch):
    c, pid = _project("ondemand")
    must = {"severity": "must", "file": "src/transform/handler.py", "issue": "no size limit on the body", "fix": "reject bodies over 1 MB"}
    _model(monkeypatch, [{"name": "submit_code_review", "input": {"verdict": "changes", "summary": "One must-fix.", "checks": CHECKS,
                                                                  "findings": [must], "design_notes": NOTES, "choices": CHOICES, "suggestions": SUGG, "for_user": ["a", "b"]}}])
    n = len(captured)
    assert c.post(f"/api/projects/{pid}/code-review/start").status_code == 202 and captured[-1] == ("ta.code_review", {"on_demand": True})
    asyncio.run(codereview.code_review(JobContext("job_od", pid, {"on_demand": True}, {}, None)))
    r = c.get(f"/api/projects/{pid}/code-review").json()
    assert r["review"]["verdict"] == "changes" and r["pending"] is None  # a review only: no gate
    assert [k for k, _ in captured[n:]] == ["ta.code_review"]  # and nothing went back to Dev on its own

    # a task for Dev while a gate waits is parked; accepting that gate (nobody starts after it) hands it over
    s = ProjectStore(pid)
    s.write("diagrams/design.json", "{}")
    a = asyncio.run(flow.request_approval(pid, "mapping", "Atlas's data mapping", "…", []))
    t = c.post(f"/api/projects/{pid}/tickets", json={"title": "Add docstrings", "assignee": "de", "area": "code", "severity": "minor"}).json()
    assert t["routed"] == "queued"
    assert c.post(f"/api/projects/{pid}/approvals/{a.id}", json={"decision": "accept"}).status_code == 200
    assert captured[-1][0] == "cto.tickets"
