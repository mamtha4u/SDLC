"""Claude on Amazon Bedrock via the official Anthropic SDK (AsyncAnthropicBedrock → bedrock-runtime InvokeModel).

Credentials come from the default AWS chain: on EC2 that's the instance role `orkestra-host-role`, whose policy
allows invoking the eu.anthropic.* inference profiles. Every call is metered (tokens + USD), budget-guarded,
written to the project's audit log and reflected on the agent's live state.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic
import yaml
from anthropic import AsyncAnthropicBedrock

from app.core.config import BACKEND_DIR, get_settings
from app.db.base import SessionLocal
from app.db.models import AgentState, LlmCall, Project
from app.services.storage import ProjectStore

from sqlalchemy import select


class BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    key: str
    id: str
    label: str
    price: dict[str, float]  # USD per MTok: input, output, cache_read, cache_write


@lru_cache
def _config() -> dict:
    return yaml.safe_load((BACKEND_DIR / "config" / "agents.yaml").read_text(encoding="utf-8"))


def agent_settings(agent: str) -> dict:
    return _config()["agents"][agent]


def model_for(agent: str) -> ModelSpec:
    return _spec(agent_settings(agent)["model"])


def _spec(key: str) -> ModelSpec:
    m = _config()["models"][key]
    return ModelSpec(key=key, id=m["id"], label=m["label"], price=m["price"])


def fallbacks_for(spec: ModelSpec) -> list[ModelSpec]:
    """The models a step switches to, in order, when Bedrock can't serve `spec` (10-05: Opus 5.5 answered 503 for a while;
    Archie's diagnosis failed while Sonnet was fine). Only the first model's list is followed: no loops."""
    keys = _config()["models"][spec.key].get("fallback") or []
    return [_spec(k) for k in ([keys] if isinstance(keys, str) else keys)]


def transient(exc: BaseException) -> bool:
    """A model error that goes away by itself (busy, overloaded, 5xx, dropped connection, a stuck call): retry, switch
    model, or re-run the step later; never ask the user or another agent about it."""
    import asyncio as _asyncio

    return isinstance(exc, _asyncio.TimeoutError) or (_retryable(exc) if isinstance(exc, Exception) else False)


def prompt(name: str) -> str:
    return (get_settings().prompts_dir / name).read_text(encoding="utf-8")


def prompts_path(name: str) -> Path:
    return get_settings().prompts_dir / name


# A call that sends nothing for READ_SILENCE_S is stuck, not thinking: a streamed turn sends thinking and text deltas
# all along. 10-05: Dev's first call hung for 15+ minutes with no byte (his longest call ever took 7 min, median 26 s)
# while the old 900 s timeout and 4 SDK retries could have kept him waiting for an hour. CALL_CAP_S caps one attempt.
READ_SILENCE_S = 240
CALL_CAP_S = 20 * 60
BACKOFF_S = (3, 10, 25)  # between attempts on a busy/5xx model (10-05: 1.5 s and 3 s were too short for a Bedrock 503)
DOWN_FOR_S = 300  # a model that just failed every attempt is skipped this long (10-05: each of Archie's turns re-tried a
_down: dict[str, float] = {}  # down Opus 5.5 for ~40 s before falling back); model id → skip until (time.time())


@lru_cache
def client() -> AsyncAnthropicBedrock:
    # a plain number: the SDK ships its own HTTP library and refuses an httpx.Timeout (10-05: that broke every model call
    # for an hour; test_llm_client_builds now builds the real client)
    return AsyncAnthropicBedrock(aws_region=get_settings().aws_region, max_retries=1, timeout=float(READ_SILENCE_S))


def usage_breakdown(spec: ModelSpec, usage: Any) -> dict:
    """Token counts by kind + USD, each kind priced at its own rate."""
    b = {
        "input_tokens": usage.input_tokens or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "output_tokens": usage.output_tokens or 0,
    }
    p = spec.price
    b["cost_usd"] = (b["input_tokens"] * p["input"] + b["cache_read_tokens"] * p["cache_read"]
                     + b["cache_write_tokens"] * p["cache_write"] + b["output_tokens"] * p["output"]) / 1_000_000
    return b


async def _check_budget(project_id: str) -> None:
    async with SessionLocal() as db:
        p = await db.get(Project, project_id)
        if p and p.cost_usd >= p.budget_usd:
            raise BudgetExceeded(f"Project budget ${p.budget_usd:.2f} reached (spent ${p.cost_usd:.2f}). "
                                 "Raise the budget in project settings to continue.")


async def _record(project_id: str, agent: str, spec: ModelSpec, b: dict, purpose: str, ms: int,
                  stop_reason: str | None) -> dict:
    tok_in = b["input_tokens"] + b["cache_read_tokens"] + b["cache_write_tokens"]
    tok_out, usd = b["output_tokens"], b["cost_usd"]
    async with SessionLocal() as db:
        project = await db.get(Project, project_id)
        db.add(LlmCall(project_id=project_id, owner_id=project.owner_id, agent=agent, model_key=spec.key,
                       model_id=spec.id, purpose=purpose[:64], input_tokens=b["input_tokens"],
                       output_tokens=b["output_tokens"], cache_read_tokens=b["cache_read_tokens"],
                       cache_write_tokens=b["cache_write_tokens"], cost_usd=round(usd, 6), price=dict(spec.price),
                       duration_ms=ms, stop_reason=stop_reason))
        row = (await db.execute(select(AgentState).where(
            AgentState.project_id == project_id, AgentState.agent == agent))).scalar_one_or_none()
        if row:
            row.tokens_in += tok_in
            row.tokens_out += tok_out
            row.cost_usd = round(row.cost_usd + usd, 6)
        project.cost_usd = round(project.cost_usd + usd, 6)
        await db.commit()
        return {"tokens_in": row.tokens_in if row else tok_in, "tokens_out": row.tokens_out if row else tok_out,
                "agent_cost_usd": row.cost_usd if row else usd, "project_cost_usd": project.cost_usd}


NO_RETRY_STATUS = {400, 401, 403, 404, 413, 422}  # our request is wrong; retrying won't help


def _retryable(exc: Exception) -> bool:
    """Transient Bedrock/Anthropic failures: 429/5xx, dropped connections, and mid-stream error events
    (e.g. internalServerException, throttling, modelStreamErrorException) which carry no HTTP status."""
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code not in NO_RETRY_STATUS
    return isinstance(exc, anthropic.APIError)


async def call(
    *,
    project_id: str,
    agent: str,
    system: list[dict] | str,
    messages: list[dict],
    tools: list[dict] | None = None,
    on_text: Callable[[str], Awaitable[None]] | None = None,
    on_json: Callable[[Any], Awaitable[None]] | None = None,
    effort: str | None = None,
    purpose: str = "",
    attempts: int = 4,
):
    """One streamed model turn with retry on transient errors. Returns (final_message, metrics).
    on_text gets text deltas; on_json gets the partially-parsed tool input as it streams (used for the
    typewriter chat and live review progress)."""
    await _check_budget(project_id)
    spec, cfg = model_for(agent), agent_settings(agent)
    kwargs: dict[str, Any] = {
        "model": spec.id,
        "max_tokens": cfg["max_tokens"],
        "system": system,
        "messages": messages,
        # thinking omitted → adaptive on Opus 5.5 / Sonnet 5; effort is the depth control
        "output_config": {"effort": effort or cfg.get("effort", "high")},
    }
    if tools:
        kwargs["tools"] = tools  # tool_choice stays "auto" (forced choice is rejected by Opus 5.5)

    from app.orchestrator import heartbeat

    async def one_turn():
        heartbeat.beat(project_id, agent, "model")
        async with client().messages.stream(**kwargs) as stream:
            beat_at = 0.0
            async for event in stream:  # every streamed event is a sign of life for the crew watch (agents/watch.py)
                now = time.perf_counter()
                if now - beat_at > 2:
                    heartbeat.beat(project_id, agent, "model")
                    beat_at = now
                if event.type == "text" and on_text:
                    await on_text(event.text)
                elif event.type == "input_json" and on_json:
                    await on_json(event.snapshot)
            msg = await stream.get_final_message()
        heartbeat.beat(project_id, agent, "model")
        return msg

    t0 = time.perf_counter()
    chain = [spec, *fallbacks_for(spec)]
    up = [m for m in chain if _down.get(m.id, 0) < time.time()]
    models = up or chain  # all marked down: try them all anyway
    msg = None
    for m_i, m_spec in enumerate(models):
        kwargs["model"] = m_spec.id
        try:
            for attempt in range(1, attempts + 1):
                try:
                    msg = await asyncio.wait_for(one_turn(), CALL_CAP_S)
                    break
                except Exception as exc:  # noqa: BLE001
                    # asyncio.TimeoutError: an attempt over CALL_CAP_S (a trickle that never ends), retried like a dropped connection
                    if attempt == attempts or not transient(exc):
                        raise
                    ProjectStore(project_id).append_log("llm", {"agent": agent, "purpose": purpose, "model": m_spec.id, "retry": attempt,
                                                                "error": str(exc)[:300]})
                    await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1])
            spec = m_spec
            _down.pop(m_spec.id, None)
            break
        except Exception as exc:  # noqa: BLE001
            if not transient(exc):
                raise
            _down[m_spec.id] = time.time() + DOWN_FOR_S  # skip it for a while: the next turns go straight to the next model
            if m_i == len(models) - 1:
                raise
            # Bedrock can't serve this model right now: the step carries on with the next one, rather than stopping
            ProjectStore(project_id).append_log("llm", {"agent": agent, "purpose": purpose, "fallback": models[m_i + 1].id,
                                                        "error": str(exc)[:300]})
            try:
                from app.orchestrator import crewchat

                await crewchat.say(project_id, agent, "crew", f"{m_spec.label} isn't available on AWS right now ({str(exc)[:80]}): "
                                   f"carrying on with {models[m_i + 1].label} (I'll try {m_spec.label} again in "
                                   f"{DOWN_FOR_S // 60} min).", "update")
            except Exception:  # noqa: BLE001 (a note, never a reason to fail)
                pass
    ms = int((time.perf_counter() - t0) * 1000)
    b = usage_breakdown(spec, msg.usage)
    usd = b["cost_usd"]
    metrics = await _record(project_id, agent, spec, b, purpose, ms, msg.stop_reason)

    ProjectStore(project_id).append_log("llm", {
        "agent": agent, "purpose": purpose, "model": spec.id, "stop_reason": msg.stop_reason, "ms": ms,
        **{k: v for k, v in b.items() if k != "cost_usd"}, "cost_usd": round(usd, 6),
        "tool_calls": [b.name for b in msg.content if b.type == "tool_use"],
        "text_preview": "".join(b.text for b in msg.content if b.type == "text")[:1500],
    })
    return msg, {**metrics, "call_cost_usd": round(usd, 6), "model": spec.label}
