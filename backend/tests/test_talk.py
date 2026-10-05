"""Kickoff conversations (user, 10-05: "each phase is handled by a different person… in each phase we interact with the
user"): Echo asks only the business, each later agent interviews its own specialist, then works. A fake model: no AI calls."""
import asyncio
from types import SimpleNamespace

import pytest

from app.agents import base, talk
from app.agents.buildkit import talk_notes
from app.db.base import SessionLocal
from app.db.models import Project
from app.orchestrator import flow
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext
from app.services.requirement_template import questions
from app.services.storage import ProjectStore
from tests.test_codereview import _project


@pytest.fixture
def captured(monkeypatch):
    jobs: list[tuple[str, dict]] = []

    async def enqueue(kind, project_id=None, **payload):
        jobs.append((kind, payload))
        return "job_x"
    monkeypatch.setattr(runner_mod.runner, "enqueue", enqueue)
    return jobs


def _model(monkeypatch, replies: list[tuple[str, dict]]):
    """Each call: the agent's reply text plus its capture call, like the real model's turn."""
    calls = []

    async def fake_call(*, messages, system=None, **_):
        calls.append({"messages": messages, "system": system})
        text, cap = replies.pop(0)
        use = SimpleNamespace(type="tool_use", id=f"t{len(calls)}", name="capture", input=cap)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text), use], stop_reason="tool_use"), {}
    monkeypatch.setattr(base.llm, "call", fake_call)
    return calls


def _ctx(pid: str, payload: dict | None = None) -> JobContext:
    return JobContext("job_t", pid, payload or {}, {}, None)


def test_echo_asks_only_the_business():
    ids = set(questions())
    assert {"overview.summary", "flow.steps", "flow.source", "flow.destination", "volume.count"} <= ids
    technical = [i for i in ids if i.startswith(("systems.", "messages.", "build.")) or i in ("problems.retries", "volume.size")]
    assert technical == []  # samples/mapping → Atlas; services, language, environment, naming → Archie
    assert not questions()["flow.source"].get("required") and not questions()["flow.destination"].get("required")
    for agent in talk.AGENTS:  # every specialist has its own topics
        assert talk.topics(agent) and talk.person(agent).startswith("the ")
    assert "tech.language" in talk.topics("ta") and "transform.needed" in talk.topics("ba") and "build.naming" in talk.topics("ta")


def test_each_phase_starts_with_its_kickoff():
    assert flow.STAGES["plan"]["next"] == "ba.talk" and flow.STAGES["mapping"]["next"] == "ta.talk"
    assert flow.STAGES["design"]["next"] == "tp.talk"
    assert flow.STAGES["mapping"]["redo"] == "ba.map" and flow.STAGES["design"]["redo"] == "ta.design"  # feedback: straight to work


def test_atlas_talks_to_the_data_analyst_then_maps(client, captured, monkeypatch):
    c, pid = _project("talker")
    calls = _model(monkeypatch, [
        ("I read the requirement: orders go from the shop to the warehouse.\n\n1. Does the data change on the way?",
         {"answers": [{"topic_id": "transform.needed", "value": "to confirm"}], "quick_replies": ["It passes through", "It changes"], "ready": True}),
        ("Got it: XML to JSON. Here's what I'll map: … Shall I start?",
         {"answers": [{"topic_id": "transform.needed", "value": "XML in, JSON out"}, {"topic_id": "nope.bad", "value": "x"}],
          "quick_replies": ["Yes, start"]}),
        ("Starting the mapping now.", {"answers": [], "quick_replies": [], "ready": True}),
    ])
    asyncio.run(talk.begin(_ctx(pid, {"approved_comment": "go"}), "ba"))
    st = asyncio.run(talk.load(pid, "ba"))
    assert st.status == "talking" and st.chat[-1]["role"] == "agent" and st.chat[-1]["quick_replies"] == ["It passes through", "It changes"]
    assert captured == []  # "ready" on the opening message is ignored: nobody has answered yet
    assert "data analyst" in calls[0]["messages"][0]["content"][0]["text"]  # Orion's opening brief names the person

    r = c.get(f"/api/projects/{pid}/talks").json()
    assert r["ba"]["waiting"] and r["ta"]["status"] == "none"
    assert c.post(f"/api/projects/{pid}/talks/ba/message", json={"message": "XML in, JSON out; sample attached"}).status_code == 202
    assert captured[-1] == ("ba.talk_reply", {})
    assert c.post(f"/api/projects/{pid}/talks/ba/message", json={"message": "again"}).status_code == 409  # still thinking
    asyncio.run(talk.turn(_ctx(pid, {"agent": "ba"}), "ba"))
    st = asyncio.run(talk.load(pid, "ba"))
    assert st.answers == {"transform.needed": "XML in, JSON out"} and st.busy is None  # unknown topic ids are dropped

    c.post(f"/api/projects/{pid}/talks/ba/message", json={"message": "yes, start"})
    asyncio.run(talk.turn(_ctx(pid, {"agent": "ba"}), "ba"))
    st = asyncio.run(talk.load(pid, "ba"))
    assert st.status == "done" and captured[-1] == ("ba.map", {"approved_comment": "go"})  # the work, with the phase's payload
    doc = ProjectStore(pid).read("talks/ba.md").decode()
    assert "XML in, JSON out" in doc and "## The conversation" in doc
    notes = talk_notes(ProjectStore(pid))
    assert "XML in, JSON out" in notes and "## The conversation" not in notes  # later agents see what was agreed
    assert "## The conversation" in talk_notes(ProjectStore(pid), own="ba")  # Atlas sees his whole conversation


def test_start_now_uses_the_recommendations(client, captured, monkeypatch):
    c, pid = _project("starter")
    _model(monkeypatch, [("1. Which services? I recommend S3 → Lambda → S3.", {"answers": [], "quick_replies": ["Use your suggestion"]})])
    asyncio.run(talk.begin(_ctx(pid), "ta"))
    assert c.post(f"/api/projects/{pid}/talks/ta/start").status_code == 202 and captured[-1] == ("ta.talk_finish", {})
    asyncio.run(talk.finish(_ctx(pid), "ta", by_button=True))
    doc = ProjectStore(pid).read("talks/ta.md").decode()
    assert "Start now" in doc and "Still open" in doc and "Language" in doc  # required topics left open become assumptions
    assert captured[-1] == ("ta.design", {})


def test_older_projects_and_reruns_go_straight_to_the_work(client, captured, monkeypatch):
    c, pid = _project("oldflow")

    async def no_talks():
        async with SessionLocal() as db:
            p = await db.get(Project, pid)
            p.settings = {}
            await db.commit()
    asyncio.run(no_talks())
    asyncio.run(talk.begin(_ctx(pid, {"approved_comment": ""}), "ba"))
    assert captured[-1] == ("ba.map", {"approved_comment": ""}) and asyncio.run(talk.load(pid, "ba")) is None

    c2, pid2 = _project("rerunner")
    _model(monkeypatch, [("Questions…", {"answers": [], "quick_replies": []})])
    asyncio.run(talk.begin(_ctx(pid2), "de"))
    asyncio.run(talk.finish(_ctx(pid2), "de", by_button=True))
    asyncio.run(talk.begin(_ctx(pid2, {"feedback": "x"}), "de"))  # a re-run after the kickoff: no second interview
    assert captured[-1] == ("de.code", {"feedback": "x"})
