"""The workbench (an agent's behind-the-scenes work), the cost model and price list, the kill switch, stale agent cards,
and the AWS page's sensitive-value handling. No AWS, no AI calls."""
import asyncio
import json

from fastapi.testclient import TestClient

from app.db.base import SessionLocal
from app.db.models import AgentState, Job, Project
from app.main import app
from app.orchestrator import flow
from app.orchestrator import runner as runner_mod
from app.services import inventory, pricing
from app.services.storage import ProjectStore
from tests.conftest import signup


def _project(name: str) -> tuple[TestClient, str]:
    c = TestClient(app)
    signup(c, name)
    return c, c.post("/api/projects", json={"name": name.title()}).json()["id"]


def test_workbench_shows_files_tests_and_steps_incrementally(client):
    c, pid = _project("benchie")
    s = ProjectStore(pid)
    log = lambda **r: s.append_log("work", {"agent": "de", **r})  # noqa: E731
    log(phase="turn", turn=1, purpose="code")
    log(phase="say", turn=1, text="Starting with the transform.")
    log(phase="call", turn=1, id="t1", tool="run_tests", input={"files": [{"path": "src/fn/handler.py", "content": "def h():\n    return 1\n"}], "delete": []})
    log(phase="tests", run=1, total=3, passed=2, failed=1, error=0, coverage=71.0, files={"src/fn/handler.py": {"percent": 71.0, "missing": [4]}},
        tests=[{"id": "tests/test_a.py::test_x", "outcome": "failed", "message": "assert 1 == 2"}], output="")
    log(phase="result", turn=1, id="t1", tool="run_tests", error=False, output="2/3 passed")
    w = c.get(f"/api/projects/{pid}/agents/de/workbench").json()
    assert w["total"] == 5 and w["run"]["purpose"] == "code"
    assert w["files"]["src/fn/handler.py"]["content"].startswith("def h()") and w["tests"][0]["coverage"] == 71.0
    titles = [e["title"] for e in w["events"]]
    assert any("Changed 1 file" in t for t in titles) and any("2/3 passed" in t for t in titles)
    log(phase="call", turn=2, id="t2", tool="run_tests", input={"files": [{"path": "tests/test_a.py", "content": "def test_x(): pass\n"}]})
    more = c.get(f"/api/projects/{pid}/agents/de/workbench", params={"since": w["total"]}).json()
    assert len(more["events"]) == 1 and list(more["files"]) == ["tests/test_a.py"]  # only what's new
    log(phase="turn", turn=1, purpose="sanity")  # a new run starts
    latest = c.get(f"/api/projects/{pid}/agents/de/workbench").json()
    assert len(latest["runs"]) == 2 and latest["run"]["purpose"] == "sanity" and latest["files"] == {}


def test_price_snapshot_and_cost_model(client):
    snap = json.loads(pricing.SNAPSHOT.read_text(encoding="utf-8"))
    assert set(pricing.WANT) <= set(snap["prices"]) and snap["sources"]["AWSLambda"]["published"]
    assert snap["prices"]["sqs_fifo"]["tiers"][0]["usd"] == 5e-07 and snap["prices"]["apigw_rest"]["tiers"][0]["usd"] == 3.5e-06
    c, pid = _project("costly")
    s = ProjectStore(pid)
    s.write("infra/lambda.tf", 'resource "aws_lambda_function" "fn" {\n  memory_size = 512\n  architectures = ["arm64"]\n}\n'
                               'resource "aws_lambda_permission" "p" {\n}\n')
    s.write("infra/sqs.tf", 'resource "aws_sqs_queue" "q" {\n  fifo_queue = true\n  redrive_policy = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dlq.arn })\n}\n'
                            'resource "aws_sqs_queue" "dlq" {\n  fifo_queue = true\n}\nresource "aws_cloudwatch_log_group" "l" {\n  retention_in_days = 14\n}\n')
    m = {r["address"]: r for r in pricing.model(pid)["resources"]}
    assert m["aws_lambda_function.fn"]["model"] == "lambda" and m["aws_lambda_function.fn"]["params"] == {"memory_mb": 512, "arch": "arm64", "timeout_s": None}
    assert m["aws_sqs_queue.q"]["params"] == {"fifo": True, "dlq": False} and m["aws_sqs_queue.dlq"]["params"]["dlq"] is True
    assert m["aws_lambda_permission.p"]["model"] == "free" and m["aws_cloudwatch_log_group.l"]["params"] == {"retention_days": 14}


def test_list_settings_are_not_mistaken_for_secrets():
    rows = [{"address": "aws_lambda_function.f", "type": "aws_lambda_function", "name": "f",
             "values": {"function_name": "orkestra-x-f", "architectures": ["x86_64"], "environment": [{"variables": {"A": "1"}}],
                        "filename": "../build/placeholder.zip", "source_code_hash": "abc", "kms_key_arn": "secret"},
             "sensitive": {"architectures": [False], "environment": [{}], "kms_key_arn": True}}]
    inv = inventory.build("prj_s", rows, {}, {"aws_lambda_function.f": ["architectures", "environment", "function_name", "filename"]}, None, "v1",
                          drift={"aws_lambda_function.f": ["timeout"]})
    res = inv["resources"][0]
    vals = {i["key"]: i["value"] for i in res["set"] + res["defaults"] + res["facts"]}
    assert vals["architectures"] == ["x86_64"] and vals["environment"] == [{"variables": {"A": "1"}}]
    assert vals["kms_key_arn"] == "(sensitive, hidden)" and "filename" not in vals and "source_code_hash" not in vals  # plumbing hidden
    assert inv["resources"][0]["drift"] == ["timeout"] and inv["drifted"] == 1
    assert not next(i for i in inv["resources"][0]["set"] if i["key"] == "function_name")["editable"]  # names change via renames


def test_agent_loops_cache_the_context_and_the_conversation():
    from app.agents.base import _cache_breakpoints

    msgs = [{"role": "user", "content": "requirement, mapping, LLD, files…"}]
    _cache_breakpoints(msgs)
    assert msgs[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    msgs += [{"role": "assistant", "content": [{"type": "text", "text": "writing"}]},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "saved"}]}]
    _cache_breakpoints(msgs)
    marked = [b for m in msgs for b in m["content"] if isinstance(b, dict) and "cache_control" in b]
    assert len(marked) == 2 and msgs[-1]["content"][-1].get("cache_control")  # the context + the conversation so far


def test_kill_switch_and_stale_working_cards(client):
    async def go():
        async with SessionLocal() as db:
            owner = (await db.execute(Project.__table__.select().limit(1))).first()
            p = Project(name="paused one", owner_id=owner.owner_id, paused=True)
            db.add(p)
            await db.flush()
            db.add(Job(kind="de.code", project_id=p.id, payload={}))
            db.add(AgentState(project_id=p.id, agent="tp", status="working", activity="🚀 terraform apply"))
            await db.commit()
            pid = p.id
        job = await runner_mod.runner._next_job()
        assert job is None or job.project_id != pid  # a paused project starts nothing
        await flow.reconcile(pid)
        async with SessionLocal() as db:
            st = (await db.execute(AgentState.__table__.select().where(AgentState.project_id == pid))).first()
            assert st.status == "failed" and "Try again" in st.activity  # no job behind it: interrupted, not "working" forever
            for j in (await db.execute(Job.__table__.select().where(Job.project_id == pid))).all():
                assert j.status == "queued"
            await db.execute(Job.__table__.delete().where(Job.project_id == pid))
            await db.commit()
    asyncio.run(go())
