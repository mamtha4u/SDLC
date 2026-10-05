"""Agents fix their own problems (user, 10-04): a stuck Terra or Dev asks Archie (diagnosis) and Orion (decision); the
fix comes back as new jobs, a plan still waits for the user, and after MAX_AUTO rounds it's the user's call. No AWS, no
real model."""
import asyncio
import base64
import io
import zipfile

from sqlalchemy import select

from app.agents import unblock
from app.db.base import SessionLocal
from app.db.models import AgentState
from app.orchestrator import crewchat
from app.orchestrator.runner import JobContext
from app.services import progress
from app.services.requirement_template import parse_upload
from tests.test_codereview import _model, _project, captured  # noqa: F401  (fixture)


async def _state(pid: str, agent: str) -> AgentState:
    async with SessionLocal() as db:
        return (await db.execute(select(AgentState).where(AgentState.project_id == pid, AgentState.agent == agent))).scalar_one()


def _dx(category="terraform", fix="Add depends_on = [aws_iam_role_policy.send] to aws_lambda_function.echo"):
    return {"name": "diagnose", "input": {"cause": "The function was created before its role could send to the queue.", "category": category,
                                          "fix": fix, "design_change": False, "confidence": "high"}}


def _decide(route, **kw):
    return {"name": "decide", "input": {"route": route, "brief": kw.get("brief", ""), "user_message": kw.get("msg", "Terra hit an ordering problem; fixing it."),
                                        "question": kw.get("question", ""), "suggested_answer": kw.get("answer", ""),
                                        "retry_after_s": kw.get("wait", 0)}}


def test_a_stuck_agent_asks_archie_and_orion_until_the_rounds_are_used(client, captured):  # noqa: F811
    _, pid = _project("stuck")
    ctx = JobContext("job_apply", pid, {"approved_infra": True}, {}, None)
    took = asyncio.run(unblock.escalate(ctx, "tp", "tp.apply", "terraform apply failed", RuntimeError("Error: InvalidParameterValueException")))
    assert took and captured[-1][0] == "cto.unblock"
    assert captured[-1][1]["retry"] == {"approved_infra": True} and captured[-1][1]["attempt"] == 1
    st = asyncio.run(_state(pid, "tp"))
    assert st.status == "blocked" and "Archie and Orion" in st.activity  # not a red "Try again"
    asyncio.run(unblock.escalate(ctx, "tp", "tp.apply", "terraform apply failed", RuntimeError("again")))
    assert not asyncio.run(unblock.escalate(ctx, "tp", "tp.apply", "terraform apply failed", RuntimeError("third")))  # the user's call now
    assert len([k for k, _ in captured if k == "cto.unblock"]) == unblock.MAX_AUTO
    unblock.clear(pid, "tp")  # it worked later: fresh rounds for a new problem
    assert asyncio.run(unblock.escalate(ctx, "tp", "tp.apply", "terraform apply failed", RuntimeError("new problem")))


def test_archie_diagnoses_orion_routes_the_fix_to_terra(client, captured, monkeypatch):  # noqa: F811
    _, pid = _project("unblockfix")
    ctx = JobContext("job_apply", pid, {}, {}, None)
    asyncio.run(unblock.escalate(ctx, "tp", "tp.apply", "terraform apply failed", RuntimeError("Error: role not ready")))
    _model(monkeypatch, [_dx(), _decide("fix_terraform", brief="Add depends_on to aws_lambda_function.echo")])
    asyncio.run(unblock.unblock(JobContext("job_u", pid, captured[-1][1], {}, None)))
    kind, payload = captured[-1]
    assert kind == "tp.iac" and payload["unblock"] and "depends_on" in payload["feedback"]  # Terra fixes; the plan then waits for the user
    msgs = asyncio.run(crewchat.history(pid))
    assert any(m["sender"] == "ta" and m["to"] == "tp" and "here's what I see" in m["text"] for m in msgs)
    assert any(m["sender"] == "cto" and m["to"] == "tp" and "apply this fix" in m["text"] for m in msgs)
    assert unblock.load(pid)["tp"]["history"][-1]["route"] == "fix_terraform"
    # once Terra's fix is applied, the stuck step continues from where it stopped (no extra AWS check for the user)
    assert unblock.load(pid)["resume"] == {**unblock.load(pid)["resume"], "kind": "tp.apply", "agent": "tp", "payload": {}}


def test_platform_problems_and_hiccups(client, captured, monkeypatch):  # noqa: F811
    _, pid = _project("unblockplat")
    ctx = JobContext("job_plan", pid, {"x": 1}, {}, None)
    asyncio.run(unblock.escalate(ctx, "tp", "tp.deploy", "The plan failed", RuntimeError("explicit deny in a permissions boundary")))
    _model(monkeypatch, [_dx("permissions", "Allow s3:GetBucketObjectLockConfiguration"), _decide("platform")])
    asyncio.run(unblock.unblock(JobContext("job_u", pid, captured[-1][1], {}, None)))
    st = asyncio.run(_state(pid, "tp"))
    assert st.status == "failed" and "Outside the crew's reach" in st.activity and captured[-1][0] == "cto.unblock"  # no loop
    unblock.clear(pid, "tp")

    async def no_wait(_):
        return None
    monkeypatch.setattr(unblock.asyncio, "sleep", no_wait)
    asyncio.run(unblock.escalate(ctx, "tp", "tp.deploy", "The plan failed", RuntimeError("Throttling: Rate exceeded")))
    _model(monkeypatch, [_dx("transient", ""), _decide("retry", wait=20)])
    asyncio.run(unblock.unblock(JobContext("job_u2", pid, captured[-1][1], {}, None)))
    assert captured[-1] == ("tp.deploy", {"x": 1})  # the same step again, same payload


def test_dev_gets_the_fix_and_the_user_gets_real_decisions(client, captured, monkeypatch):  # noqa: F811
    _, pid = _project("unblockdev")
    ctx = JobContext("job_code", pid, {}, {}, None)
    asyncio.run(unblock.escalate(ctx, "de", "de.code", "Writing code that passes the gates failed", RuntimeError("2 tests fail")))
    _model(monkeypatch, [_dx("code", "Parse the date with fromisoformat"), _decide("fix_code", brief="Parse the date with fromisoformat")])
    asyncio.run(unblock.unblock(JobContext("job_u", pid, captured[-1][1], {}, None)))
    assert captured[-1][0] == "de.code" and "fromisoformat" in captured[-1][1]["feedback"]
    asyncio.run(unblock.escalate(ctx, "de", "de.deploy", "Dev's deploy failed", RuntimeError("image too big")))
    _model(monkeypatch, [_dx("decision", "Container image or zip?"), _decide("ask_user", question="Use a container image (≈ $0.10/month ECR)?",
                                                                          answer="Yes: the package is over 250 MB")])
    asyncio.run(unblock.unblock(JobContext("job_u2", pid, captured[-1][1], {}, None)))
    st = asyncio.run(_state(pid, "de"))
    assert st.status == "failed" and "Needs your decision" in st.activity
    assert any("My suggestion: Yes" in m["text"] for m in asyncio.run(crewchat.history(pid)) if m["to"] == "user")


def test_only_real_front_doors_need_an_http_call():
    from app.agents import livekit

    hosts = livekit.allowed_hosts({"dlq_url": "https://sqs.eu-west-1.amazonaws.com/1/orkestra-x-dlq", "bucket": "orkestra-x-out",
                                   "api": "https://abc123.execute-api.eu-west-1.amazonaws.com/dev/orders",
                                   "alb": "orkestra-x-alb-1.eu-west-1.elb.amazonaws.com"})
    assert livekit.front_doors(hosts) == {"abc123.execute-api.eu-west-1.amazonaws.com", "orkestra-x-alb-1.eu-west-1.elb.amazonaws.com"}
    assert livekit.front_doors({"sqs.eu-west-1.amazonaws.com"}) == set()  # a schedule → Lambda → S3 flow with a DLQ


def test_progress_estimate_never_claims_done():
    assert progress.percent(0, 100) == 1 and progress.percent(50, 100) == 45 and progress.percent(100, 100) == 90
    assert 90 < progress.percent(300, 100) <= 98 and progress.percent(10_000, 100) == 98


def test_echo_accepts_any_file(client):
    c, pid = _project("uploader")
    py = c.post(f"/api/projects/{pid}/intake/upload-json", json={"name": "logger (2).py", "data": base64.b64encode(b"import logging\n").decode()})
    assert py.status_code == 200 and py.json()["name"] == "logger (2).py"  # through a proxy that blocks multipart .py uploads
    image = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    png = c.post(f"/api/projects/{pid}/intake/upload-json", json={"name": "diagram.png", "data": base64.b64encode(image).decode()})
    assert png.status_code == 200 and "diagram.png" in [u["name"] for u in png.json()["state"]["uploads"]]  # kept, not refused
    assert "isn't text" in parse_upload("diagram.png", image)["text"]  # the crew is told it's there but unreadable
    assert c.post(f"/api/projects/{pid}/intake/upload-json", json={"name": "x.py", "data": "not base64!"}).status_code == 422
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:  # a minimal .xlsx: one sheet, a shared string and a number
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>OrderId</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c></row></sheetData></worksheet>')
    assert "OrderId | 42" in parse_upload("mapping.xlsx", buf.getvalue())["text"]


def test_one_review_round_then_sign_off(client):
    from app.api import intake as intake_api

    assert intake_api.MIN_ROUNDS == 1  # user, 10-04: "2 reviews is too much"
