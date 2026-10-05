"""The real Claude client must build (10-05: an httpx.Timeout the SDK refuses broke every model call, while every other
test passed because they replace the model), and a busy model never stops a step: Opus 5.5 → Opus 5 → Sonnet 5 (user,
10-05), then the runner re-runs the step by itself. No network call is made."""
import asyncio
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.agents import llm
from tests.test_codereview import _project


def test_llm_client_builds():
    llm.client.cache_clear()
    c = llm.client()
    assert c.max_retries == 1 and float(c.timeout) == float(llm.READ_SILENCE_S)


def test_fallback_order():
    assert [s.key for s in llm.fallbacks_for(llm.model_for("ta"))] == ["opus-5", "opus-4-8", "sonnet-5"]
    assert [s.key for s in llm.fallbacks_for(llm.model_for("de"))] == ["sonnet-4-6", "opus-5"]


def _busy() -> anthropic.APIStatusError:
    req = httpx2.Request("POST", "https://bedrock-runtime.eu-west-1.amazonaws.com/x")
    return anthropic.InternalServerError("Bedrock is unable to process your request.", response=httpx2.Response(503, request=req), body=None)


class _Stream:
    def __init__(self, model: str, down: set[str], seen: list[str]):
        self.model, self.down, self.seen = model, down, seen

    async def __aenter__(self):
        self.seen.append(self.model)
        if self.model in self.down:
            raise _busy()
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self):
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn", usage=usage)


def test_a_busy_model_falls_back_in_order(client, monkeypatch):
    _, pid = _project("fallback")
    seen: list[str] = []
    down = {"eu.anthropic.claude-opus-5-5", "eu.anthropic.claude-opus-5"}
    fake = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: _Stream(kw["model"], down, seen)))
    monkeypatch.setattr(llm, "client", lambda: fake)
    monkeypatch.setattr(llm, "BACKOFF_S", (0, 0, 0))
    monkeypatch.setattr(llm, "_down", {})
    down.add("eu.anthropic.claude-opus-4-8")
    msg, metrics = asyncio.run(llm.call(project_id=pid, agent="ta", system="s", messages=[{"role": "user", "content": "hi"}], purpose="t"))
    assert msg.content[0].text == "ok" and metrics["model"] == "Claude Sonnet 5"
    assert seen.count("eu.anthropic.claude-opus-5-5") == 4 and seen.count("eu.anthropic.claude-opus-5") == 4 and seen[-1] == "eu.anthropic.claude-sonnet-5"
    seen.clear()  # the next turn skips the models that just failed: straight to Sonnet 5
    asyncio.run(llm.call(project_id=pid, agent="ta", system="s", messages=[{"role": "user", "content": "hi"}], purpose="t"))
    assert seen == ["eu.anthropic.claude-sonnet-5"]
    llm._down.clear()
    down.add("eu.anthropic.claude-sonnet-5")
    with pytest.raises(anthropic.APIStatusError):  # everything down: the step fails, and the runner re-runs it later
        asyncio.run(llm.call(project_id=pid, agent="ta", system="s", messages=[{"role": "user", "content": "hi"}], purpose="t"))
    assert llm.transient(_busy()) and not llm.transient(ValueError("bad input"))


def test_a_step_failed_by_a_busy_claude_reruns_itself(client):
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import AgentState, Job
    from app.orchestrator.runner import JobContext, JobRunner

    _, pid = _project("autoretry")

    async def go():
        r = JobRunner(1)
        r.RETRY_AFTER_S = 0
        queued: list[tuple[str, dict]] = []

        async def enqueue(kind, project_id=None, **payload):
            queued.append((kind, payload))
            return "job_x"
        r.enqueue = enqueue
        job = Job(id="job_t1", kind="cto.unblock", project_id=pid, payload={"agent": "de", "step": "de.deploy"})
        await r._retry_later(job, JobContext("job_t1", pid, job.payload, {}, None), _busy())
        await asyncio.sleep(0.05)
        assert queued == [("cto.unblock", {"agent": "de", "step": "de.deploy", "auto_retry": 1})]
        async with SessionLocal() as db:
            de = (await db.execute(select(AgentState).where(AgentState.project_id == pid, AgentState.agent == "de"))).scalar_one()
        assert de.status == "blocked" and "retrying automatically" in de.activity
        await r._retry_later(Job(kind="de.deploy", project_id=pid, payload={"auto_retry": 2}), JobContext("j", pid, {}, {}, None), _busy())
        await r._retry_later(Job(kind="de.deploy", project_id=pid, payload={}), JobContext("j", pid, {}, {}, None), ValueError("a real bug"))
        await asyncio.sleep(0.05)
        assert len(queued) == 1  # capped at 2, and only for a busy Claude, never for a real error
    asyncio.run(go())
