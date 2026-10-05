"""The crew watch (user, 10-05): Orion and Archie ask a silent agent if it's OK, restart a step that stopped responding
(never Terraform mid-run), and Archie's advice reaches the agent's next step. No AWS, no real model."""
import asyncio
from datetime import datetime, timedelta, timezone

from app.agents import base, watch
from app.agents.base import Tool, obj
from app.db.base import SessionLocal
from app.db.models import Job
from app.orchestrator import crewchat, heartbeat, runner as runner_mod
from app.orchestrator.runner import JobRunner, handler
from tests.test_codereview import _model, _project, captured  # noqa: F401  (fixture)


async def _running(pid: str, kind: str, started_s_ago: float, payload: dict | None = None) -> str:
    async with SessionLocal() as db:
        j = Job(kind=kind, project_id=pid, payload=payload or {}, status="running",
                started_at=datetime.now(timezone.utc) - timedelta(seconds=started_s_ago))
        db.add(j)
        await db.commit()
        return j.id


async def _done(jid: str) -> None:
    async with SessionLocal() as db:
        (await db.get(Job, jid)).status = "done"
        await db.commit()


def test_a_silent_agent_is_asked_then_restarted(client, captured, monkeypatch):  # noqa: F811
    _, pid = _project("watched")
    aborted = []
    monkeypatch.setattr(runner_mod.runner, "abort", lambda jid, why: aborted.append((jid, why)) or True)
    jid = asyncio.run(_running(pid, "de.code", 600, {"tickets": []}))
    now = datetime.now(timezone.utc).timestamp()
    heartbeat.beat(pid, "de", "model")
    assert ("ask", jid) not in asyncio.run(watch.sweep(now + 60))  # beating: nothing to do
    heartbeat._beats[(pid, "de")]["at"] = now - 200  # 200 s without a streamed byte
    assert ("ask", jid) in asyncio.run(watch.sweep(now))
    heartbeat.beat(pid, "de", "model")  # it answers by moving again
    assert ("answered", jid) in asyncio.run(watch.sweep(time_now := datetime.now(timezone.utc).timestamp()))
    heartbeat._beats[(pid, "de")]["at"] = time_now - 500  # then goes silent for good
    assert ("restart", jid) in asyncio.run(watch.sweep(time_now))
    assert aborted and aborted[0][0] == jid and captured[-1] == ("de.code", {"tickets": [], "watch_restart": 1})
    msgs = asyncio.run(crewchat.history(pid))
    assert any(m["sender"] == "ta" and m["to"] == "de" and "Everything OK?" in m["text"] for m in msgs)  # Archie watches Dev
    assert any(m["sender"] == "ta" and "restarted the step" in m["text"] for m in msgs)
    asyncio.run(_done(jid))


def test_terraform_is_never_killed_and_restarts_are_capped(client, captured, monkeypatch):  # noqa: F811
    _, pid = _project("watchedtf")
    monkeypatch.setattr(runner_mod.runner, "abort", lambda jid, why: True)
    now = datetime.now(timezone.utc).timestamp()
    apply_job = asyncio.run(_running(pid, "tp.apply", 2000))
    heartbeat.forget(pid, "tp")
    acts = asyncio.run(watch.sweep(now))
    assert ("escalate", apply_job) in acts and ("restart", apply_job) not in acts  # tells the user instead
    asyncio.run(_done(apply_job))
    echo = asyncio.run(_running(pid, "intake.chat", 900, {"watch_restart": watch.MAX_RESTARTS}))
    heartbeat.forget(pid, "intake")
    assert ("escalate", echo) in asyncio.run(watch.sweep(now))  # Orion watches Echo; restarted twice already
    assert any(m["sender"] == "cto" and m["to"] == "intake" for m in asyncio.run(crewchat.history(pid)))
    asyncio.run(_done(echo))


def test_archies_advice_reaches_the_next_step(client, monkeypatch):
    _, pid = _project("advised")
    calls = _model(monkeypatch, [{"name": "look", "input": {}}, {"name": "finish", "input": {}}])

    async def look(_):
        heartbeat.advise(pid, "de", "Archie (TA)", "Parse the timestamp with fromisoformat")
        return "looked"
    tools = [Tool("look", "Look.", obj({}), look), Tool("finish", "Done.", obj({}), terminal=True)]
    asyncio.run(base.run_loop(project_id=pid, agent="de", system="s", messages=[{"role": "user", "content": "go"}], tools=tools, narrate=False))
    sent = [b for m in calls[1] if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"] if isinstance(b, dict)]
    assert any(b.get("type") == "text" and "Archie (TA), who is watching your work" in b["text"] for b in sent)
    assert heartbeat.take_advice(pid, "de") == []  # delivered once


def test_a_step_cut_off_by_a_restart_runs_again(client, captured):  # noqa: F811
    from sqlalchemy import select

    from app.db.models import AgentState

    _, pid = _project("revived")

    async def go():
        async with SessionLocal() as db:
            db.add(Job(kind="tp.deploy", project_id=pid, payload={"approved_infra": True}, status="interrupted",
                       started_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
            db.add(Job(kind="tp.apply", project_id=pid, payload={}, status="interrupted",  # changes AWS: never by itself
                       created_at=datetime.now(timezone.utc) - timedelta(minutes=10), started_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
            st = (await db.execute(select(AgentState).where(AgentState.project_id == pid, AgentState.agent == "tp"))).scalar_one()
            st.status, st.activity = "working", "terraform init"
            await db.commit()
        return await watch.sweep()
    acts = asyncio.run(go())
    assert [a for a, _ in acts].count("revive") == 1 and captured[-1] == ("tp.deploy", {"approved_infra": True, "watch_restart": 1})
    assert not [a for a, _ in asyncio.run(watch.sweep()) if a == "revive"]  # handled once


@handler("test.hang", resumable=False)
async def _hang(ctx):
    await asyncio.sleep(3600)


def test_a_hung_step_can_be_aborted(client):
    async def go():
        async with SessionLocal() as db:
            job = Job(kind="test.hang", project_id=None, payload={}, status="running", started_at=datetime.now(timezone.utc))
            db.add(job)
            await db.commit()
            await db.refresh(job)
        r = JobRunner(1)
        t = asyncio.create_task(r._run(job))
        await asyncio.sleep(0.2)
        assert r.abort(job.id, "no sign of life for 7 min")
        await asyncio.wait_for(t, 5)
        async with SessionLocal() as db:
            row = await db.get(Job, job.id)
            assert row.status == "interrupted" and "crew watch" in row.error
    asyncio.run(go())
