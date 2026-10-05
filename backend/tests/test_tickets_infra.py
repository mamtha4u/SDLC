"""Tickets (the crew's bug board), Orion's ticket routing, and the AWS page (inventory + change requests). No AWS calls."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.agents import dispatch, qa
from app.db.base import SessionLocal
from app.db.models import Intake
from app.main import app
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext
from app.services import aws_access, inventory
from app.services import tickets as tk
from app.services.storage import ProjectStore
from tests.conftest import signup


@pytest.fixture
def captured(monkeypatch):
    jobs: list[tuple[str, dict]] = []

    async def enqueue(kind, project_id=None, **payload):
        jobs.append((kind, payload))
        return "job_x"
    monkeypatch.setattr(runner_mod.runner, "enqueue", enqueue)
    return jobs


def _project(name: str) -> tuple[TestClient, str]:
    c = TestClient(app)
    signup(c, name)
    return c, c.post("/api/projects", json={"name": name.title()}).json()["id"]


def test_ticket_lifecycle_through_the_api(client, captured):
    c, pid = _project("ticketeer")
    r = c.post(f"/api/projects/{pid}/tickets", json={"title": "Empty body gives 500", "steps": "POST {}", "expected": "400",
                                                       "actual": "500", "severity": "critical", "area": "code", "assignee": "de"})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["label"] == "TKT-001" and t["assignee"] == "de" and t["reporter"] == "user" and t["routed"] == "dispatched"
    assert captured[-1][0] == "cto.tickets"  # Orion hands it over at once when the crew is free
    t = c.post(f"/api/projects/{pid}/tickets/{t['id']}/comments", json={"text": "Seen in prod logs too"}).json()
    assert [x["kind"] for x in t["comments"]] == ["created", "comment"]
    t = c.patch(f"/api/projects/{pid}/tickets/{t['id']}", json={"assignee": "tp", "note": "Looks like a timeout"}).json()
    assert t["assignee"] == "tp" and "assigned to Terra" in t["comments"][-1]["text"]
    t = c.patch(f"/api/projects/{pid}/tickets/{t['id']}", json={"status": "closed"}).json()
    assert t["status"] == "closed"
    t = c.patch(f"/api/projects/{pid}/tickets/{t['id']}", json={"assignee": "de"}).json()
    assert t["status"] == "reopened"  # handing a finished ticket to someone reopens it
    lst = c.get(f"/api/projects/{pid}/tickets").json()
    assert lst["active"] == 1 and lst["tickets"][0]["label"] == "TKT-001"
    assert c.post(f"/api/projects/{pid}/tickets", json={"title": "x"}).status_code == 422


def test_orion_routes_infra_tickets_first_then_code_then_quinn(client, captured):
    c, pid = _project("router")
    ctx = JobContext("job_t", pid, {}, {}, None)

    async def go():
        a = await tk.create(pid, title="Lambda times out", area="infra", reporter="qa", check_id="LIVE-03", announce=False)
        b = await tk.create(pid, title="Wrong date format", area="code", reporter="qa", check_id="LIVE-01", announce=False)
        await dispatch.route_tickets(ctx)
        assert captured[-1] == ("tp.iac", {"tickets": [a.id]})
        await tk.change(a.id, "tp", "Raised the timeout", status="resolved", assignee="qa", say=False)
        await dispatch.route_tickets(ctx)
        assert captured[-1] == ("de.code", {"tickets": [b.id]})
        await tk.change(b.id, "de", "Fixed", status="resolved", assignee="qa", say=False)
        await dispatch.route_tickets(ctx)
        assert captured[-1][0] == "qa.talk"  # no approved test plan yet: Quinn talks to the tester, then writes it
        ProjectStore(pid).write(qa.PLAN_JSON, json.dumps({"status": "approved", "basis": qa.plan_basis(pid), "cases": []}))
        await dispatch.route_tickets(ctx)
        assert captured[-1][0] == "qa.live"  # plan approved, both resolved: Quinn retests
        await tk.change(a.id, "qa", "ok", status="closed", say=False)
        await tk.change(b.id, "qa", "ok", status="closed", say=False)
        aws_access.save(pid, {"status": "deployed", "deploys": 2, "live_ok": 2}, "code")
        n = len(captured)
        await dispatch.route_tickets(ctx)
        assert len(captured) == n  # nothing left: the project is done
    asyncio.run(go())


ROWS = [
    {"address": "aws_lambda_function.transform", "type": "aws_lambda_function", "name": "transform", "sensitive": {},
     "values": {"function_name": "orkestra-x-transform", "memory_size": 256, "timeout": 30, "arn": "arn:aws:lambda:eu-west-1:1:function:orkestra-x-transform",
                "reserved_concurrent_executions": -1, "tracing_config": [{"mode": "PassThrough"}], "tags_all": {"a": "b"}}},
    {"address": 'aws_sqs_queue.q["dlq"]', "type": "aws_sqs_queue", "name": "q", "sensitive": {},
     "values": {"name": "orkestra-x-dlq", "url": "https://sqs.eu-west-1.amazonaws.com/1/orkestra-x-dlq", "message_retention_seconds": 1209600}},
]
SCHEMA = {"aws_lambda_function": {"function_name": {"kind": "string", "required": True}, "memory_size": {"kind": "number", "optional": True},
                                  "timeout": {"kind": "number", "optional": True},
                                  "reserved_concurrent_executions": {"kind": "number", "optional": True, "desc": "Concurrency"},
                                  "arn": {"kind": "string", "computed": True}, "tracing_config": {"kind": "block", "optional": True}},
          "aws_sqs_queue": {"name": {"kind": "string", "optional": True}, "url": {"kind": "string", "computed": True},
                            "message_retention_seconds": {"kind": "number", "optional": True}}}
CONFIG = {"aws_lambda_function.transform": ["function_name", "memory_size", "timeout"], "aws_sqs_queue.q": ["name", "message_retention_seconds"]}


def test_inventory_splits_what_terra_set_from_aws_defaults():
    inv = inventory.build("prj_inv", ROWS, SCHEMA, CONFIG, None, "v1")
    fn, q = inv["resources"][0], inv["resources"][1]
    assert fn["service_label"] == "Lambda" and fn["kind"] == "Lambda function" and fn["name"] == "orkestra-x-transform"
    assert [i["key"] for i in fn["set"]] == ["function_name", "memory_size", "timeout"]
    assert {i["key"] for i in fn["defaults"]} == {"reserved_concurrent_executions", "tracing_config"}
    assert [i["key"] for i in fn["facts"]] == ["arn"] and all(i["key"] != "tags_all" for i in fn["set"] + fn["defaults"])
    assert fn["console"].endswith("#/functions/orkestra-x-transform")
    assert [i["key"] for i in q["set"]] == ["message_retention_seconds", "name"]  # indexed address → its config block
    assert "orkestra-x-dlq" in q["console"] and q["service_label"] == "SQS"
    both = inventory.build("prj_inv", ROWS + [{"address": "aws_api_gateway_rest_api.a", "type": "aws_api_gateway_rest_api", "values": {"name": "orkestra-x-api"}},
                                              {"address": "aws_apigatewayv2_api.b", "type": "aws_apigatewayv2_api", "values": {"name": "orkestra-x-http"}}], {}, {}, None, "v1")
    keys = [s["key"] for s in both["services"]]
    assert len(keys) == len(set(keys)) and next(s for s in both["services"] if s["key"] == "apigateway")["count"] == 2  # one group
    text, clean = inventory.change_text(inv, [
        {"address": "aws_lambda_function.transform", "key": "memory_size", "to": "512"},
        {"address": "aws_lambda_function.transform", "key": "timeout", "to": 30},  # unchanged: dropped
        {"address": 'aws_sqs_queue.q["dlq"]', "key": "message_retention_seconds", "to": "604800"}], "keep it cheap")
    assert [(c["key"], c["to"]) for c in clean] == [("memory_size", 512), ("message_retention_seconds", 604800)]
    assert "256 → **512**" in text and "Note: keep it cheap" in text
    with pytest.raises(inventory.ChangeError):
        inventory.change_text(inv, [{"address": "aws_lambda_function.transform", "key": "arn", "to": "x"}], "")
    with pytest.raises(inventory.ChangeError):
        inventory.change_text(inv, [{"address": "aws_lambda_function.transform", "key": "memory_size", "to": "lots"}], "")
    with pytest.raises(inventory.ChangeError):
        inventory.change_text(inv, [], " ")


def test_aws_page_changes_go_straight_to_terra(client, captured):
    c, pid = _project("awspage")
    assert c.get(f"/api/projects/{pid}/infra").json()["status"] == "none"
    assert c.post(f"/api/projects/{pid}/infra/changes", json={"changes": []}).status_code == 409  # nothing in AWS
    inventory.save(pid, inventory.build(pid, ROWS, SCHEMA, CONFIG, None, "v1"))
    aws_access.save(pid, {"status": "deployed", "outputs": {}}, "deploy")

    async def sign_off():
        async with SessionLocal() as db:
            db.add(Intake(project_id=pid, status="signed_off", requirement_md="# R"))
            await db.commit()
    asyncio.run(sign_off())
    info = c.get(f"/api/projects/{pid}/infra").json()
    assert info["can_change"] and info["inventory"]["count"] == 2
    r = c.post(f"/api/projects/{pid}/infra/changes", json={"changes": [
        {"address": "aws_lambda_function.transform", "key": "memory_size", "to": 1024}], "note": ""})
    assert r.status_code == 202, r.text
    cr = r.json()
    assert cr["route"] == "infra" and cr["status"] == "triage" and cr["triage"]["pending"]["edits"][0]["to"] == 1024
    assert captured[-1] == ("cto.review", {"cr_id": cr["id"]})  # Orion reviews the impact before anyone touches anything
    assert c.get(f"/api/projects/{pid}/infra").json()["changes"][0]["label"] == cr["label"]

    # Orion's verdict decides the route
    from app.agents import review
    from app.orchestrator import changes as changes_mod
    from app.orchestrator.runner import JobContext

    def go(verdict: str, plan_only: bool = False) -> tuple[str, dict]:
        async def run():
            await changes_mod.update(cr["id"], status="reviewing", triage={**cr["triage"], "verdict": verdict, "summary": "s", "terra_brief": "do it",
                                                                           "dev_brief": "adapt the code to 3.12", "pending": {"plan_only": plan_only}})
            await review.change_go(JobContext("j", pid, {"cr_id": cr["id"]}, {}, None))
        asyncio.run(run())
        return captured[-1]
    kind, payload = go("infra_only")
    assert kind == "tp.iac" and "Orion's brief for you: do it" in payload["feedback"]
    assert "followups" not in aws_access.load(pid, "deploy") or not aws_access.load(pid, "deploy")["followups"]
    kind, _ = go("affects_code")
    assert kind == "tp.iac" and aws_access.load(pid, "deploy")["followups"]["de"].endswith("adapt the code to 3.12")  # Dev after the check
    assert go("infra_only", plan_only=True)[0] == "tp.deploy"  # a rename: the names file is the change, Terra just plans
