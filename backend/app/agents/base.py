"""AgentLoop: the shared tool-use loop every agent runs on.

The model decides which tools to call; we execute them, feed results back, and stop when it ends its turn,
when a `terminal` tool has been called (its input *is* the agent's structured output), or at max_turns.
Bedrock's InvokeModel path rejects `strict` on tools, so every tool input is validated here against its
schema; invalid input goes back to the model as an error so it corrects itself. The full assistant content
(incl. thinking blocks) is appended back unchanged, as the API requires.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents import llm
from app.orchestrator import heartbeat
from app.services.storage import ProjectStore

ToolHandler = Callable[[dict], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: ToolHandler | None = None
    terminal: bool = False  # calling it ends the loop; its input is captured as the result

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    """Closed object schema with every property required."""
    return {"type": "object", "properties": properties, "required": required or list(properties),
            "additionalProperties": False}


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "number": (int, float)}


def validate(schema: dict, value: Any, path: str = "input") -> list[str]:
    """Minimal JSON-schema check for the subset our tools use (type, properties, required, items, enum)."""
    t = schema.get("type")
    if t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif t in _TYPES:
        ok = isinstance(value, _TYPES[t]) and not (t == "number" and isinstance(value, bool))
    else:
        ok = True
    if not ok:
        return [f"{path} should be {t}"]
    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}")
    if t == "object":
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key} is missing")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += validate(sub, value[key], f"{path}.{key}")
    if t == "array" and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(schema["items"], item, f"{path}[{i}]")
    return errors


@dataclass
class LoopResult:
    text: str                       # all the model's text, every turn
    terminal: dict[str, dict] = field(default_factory=dict)  # terminal tool name -> its input
    stop_reason: str | None = None
    turns: int = 0
    last_text: str = ""             # the final turn's text only (the answer, without "let me check…" preambles)


class AgentError(Exception):
    pass


async def hold_if_paused(project_id: str, agent: str) -> None:
    """The kill switch: before every model call, wait while the project is paused (no AI call, no spend). Resume
    continues exactly here."""
    import asyncio

    from app.db.base import SessionLocal
    from app.db.models import Project
    from app.orchestrator import crewchat

    told = False
    while True:
        async with SessionLocal() as db:
            p = await db.get(Project, project_id)
            if not p or not p.paused:
                if told:
                    await crewchat.say(project_id, agent, "crew", "▶️ Resumed: carrying on where I stopped.", "update")
                return
        if not told:
            await crewchat.say(project_id, agent, "crew", "⏸ Kill switch on: I've stopped before my next step and wait for Resume.", "update")
            told = True
        await asyncio.sleep(2)


async def _ask_archie(project_id: str, agent: str, why: str, detail: str) -> None:
    """Something unexpected in Terra's or Dev's work: Archie looks at it while they carry on (agents/watch.advise)."""
    from app.agents import watch

    await watch.request_advice(project_id, agent, why, detail)


def _cache_breakpoints(messages: list[dict]) -> None:
    """Prompt caching for the agent loops: the first user message (the big context: requirement, mapping, LLD, files)
    and the latest message are cache breakpoints, so every step reads the conversation so far from the cache instead of
    paying for (and waiting on) it again. Measured 10-02: Dev re-sent 45-113K uncached tokens per step."""
    def blocks(m: dict) -> list[dict]:
        if isinstance(m["content"], str):
            m["content"] = [{"type": "text", "text": m["content"]}]
        return m["content"]
    for m in messages:  # drop old markers (max 4 per request: system + first + last)
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    if messages:
        blocks(messages[0])[-1]["cache_control"] = {"type": "ephemeral"}
        last = blocks(messages[-1])
        if last and isinstance(last[-1], dict) and len(messages) > 1:
            last[-1]["cache_control"] = {"type": "ephemeral"}


async def run_loop(*, project_id: str, agent: str, system: list[dict] | str, messages: list[dict],
                   tools: list[Tool] = (), max_turns: int = 8, purpose: str = "",
                   on_text: Callable[[str], Awaitable[None]] | None = None,
                   on_json: Callable[[Any], Awaitable[None]] | None = None,
                   effort: str | None = None,
                   on_metrics: Callable[[dict], Awaitable[None]] | None = None,
                   narrate: bool = True, log_first: list[dict] | None = None) -> LoopResult:
    """`narrate`: post what the agent says between tool calls, and every tool call with its result, to the crew room
    (turn it off where the text is the deliverable itself, e.g. Echo's chat reply or a whole document)."""
    from app.orchestrator import crewchat

    by_name = {t.name: t for t in tools}
    store = ProjectStore(project_id)
    # callers that place their own cache markers in the messages (Echo's anchored chat window) keep them
    auto_cache = not any(isinstance(b, dict) and "cache_control" in b for m in messages if isinstance(m.get("content"), list) for b in m["content"])
    result = LoopResult(text="")
    rejected: dict[str, int] = {}
    for turn in range(1, max_turns + 1):
        await hold_if_paused(project_id, agent)
        store.append_log("work", {"agent": agent, "phase": "turn", "turn": turn, "purpose": purpose})  # the workbench timeline
        if turn == 1:
            for rec in log_first or []:  # e.g. the files a resumed attempt starts from
                store.append_log("work", {"agent": agent, "turn": 1, **rec})
        if auto_cache:
            _cache_breakpoints(messages)
        msg, metrics = await llm.call(project_id=project_id, agent=agent, system=system, messages=messages,
                                      tools=[t.spec() for t in tools] or None, on_text=on_text, on_json=on_json,
                                      effort=effort, purpose=purpose)
        result.turns, result.stop_reason = turn, msg.stop_reason
        if on_metrics:
            await on_metrics(metrics)
        if msg.stop_reason == "refusal":
            raise AgentError("The model declined this request. Rephrase the input and try again.")
        said = "".join(b.text for b in msg.content if b.type == "text")
        result.text += said
        result.last_text = said
        if said.strip():
            store.append_log("work", {"agent": agent, "phase": "say", "turn": turn, "text": said.strip()[:4000]})
        if narrate and said.strip():
            await crewchat.say(project_id, agent, "crew", said.strip()[:1800], "think")
        uses = [b for b in msg.content if b.type == "tool_use"]
        if not uses:
            if msg.stop_reason == "max_tokens":
                raise AgentError("The response hit the output limit before finishing.")
            return result

        messages.append({"role": "assistant", "content": msg.content})
        results = []
        for u in uses:
            tool = by_name.get(u.name)
            args = u.input if isinstance(u.input, dict) else json.loads(u.input)
            store.append_log("tools", {"agent": agent, "tool": u.name, "input": args})
            store.append_log("work", {"agent": agent, "phase": "call", "turn": turn, "id": u.id, "tool": u.name, "input": args})

            def done(content: str, error: bool = False, _id=u.id, _name=u.name) -> None:
                store.append_log("work", {"agent": agent, "phase": "result", "turn": turn, "id": _id, "tool": _name, "error": error,
                                          "output": content[:6000]})
            if tool is None:
                results.append({"type": "tool_result", "tool_use_id": u.id, "is_error": True,
                                "content": f"Unknown tool {u.name}"})
                continue
            problems = validate(tool.schema, args)
            if problems:
                results.append({"type": "tool_result", "tool_use_id": u.id, "is_error": True,
                                "content": "Invalid input, fix and call again: " + "; ".join(problems[:12])})
                done("Invalid input: " + "; ".join(problems[:12]), True)
                if narrate:
                    await crewchat.say(project_id, agent, "crew", f"⛔ My {u.name} call was malformed, fixing it: "
                                       + "; ".join(problems[:6]), "issue")
                continue
            if tool.terminal:
                # A terminal tool may have a verifier: if it raises, the model gets the error and must fix + resubmit.
                line = crewchat.describe(tool.name, args) if narrate else None
                if tool.handler:
                    try:
                        heartbeat.beat(project_id, agent, f"tool:{u.name}")
                        await tool.handler(args)
                        heartbeat.beat(project_id, agent, "model")
                    except Exception as exc:  # noqa: BLE001
                        heartbeat.beat(project_id, agent, "model")
                        results.append({"type": "tool_result", "tool_use_id": u.id, "is_error": True,
                                        "content": f"Not accepted: {exc}"})
                        done(f"Not accepted: {exc}", True)
                        rejected[tool.name] = rejected.get(tool.name, 0) + 1
                        if rejected[tool.name] == 3 and agent in ("tp", "de"):  # unexpected: Archie looks over the shoulder
                            await _ask_archie(project_id, agent, f"the platform rejected {tool.name} 3 times", str(exc))
                        if narrate:
                            await crewchat.say(project_id, agent, "crew", (line + "\n\n" if line else "")
                                               + f"⛔ The platform check rejected it: {str(exc)[:1200]}", "issue")
                        continue
                result.terminal[tool.name] = args
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": "Received."})
                done("Accepted")
                if line:
                    await crewchat.say(project_id, agent, "crew", line + (f". ✅ Accepted after {rejected[tool.name]} fix(es)."
                                                                         if rejected.get(tool.name) else ""),
                                       "fix" if rejected.get(tool.name) else "work")
                continue
            try:
                heartbeat.beat(project_id, agent, f"tool:{u.name}")  # a long tool run (tests, terraform) is alive, not silent
                out = await tool.handler(args) if tool.handler else "ok"
                heartbeat.beat(project_id, agent, "model")
                results.append({"type": "tool_result", "tool_use_id": u.id,
                                "content": out if isinstance(out, str) else json.dumps(out, default=str)})
                done(out if isinstance(out, str) else json.dumps(out, default=str))
                line = crewchat.describe(tool.name, args, out) if narrate else None
                if line:
                    await crewchat.say(project_id, agent, "crew", line, "work")
            except Exception as exc:  # noqa: BLE001 — errors go back to the model, it can recover
                results.append({"type": "tool_result", "tool_use_id": u.id, "is_error": True, "content": str(exc)})
                done(str(exc), True)
                if narrate:
                    await crewchat.say(project_id, agent, "crew", f"⚠️ {u.name} failed: {str(exc)[:600]}. Trying another way.", "issue")
        if result.terminal:
            return result
        finish = [t.name for t in tools if t.terminal]
        if finish and turn == max_turns - 2:  # warn before the hard stop (Quinn ran out mid-test with every check done, 10-02)
            results.append({"type": "text", "text": f"⏳ Two turns left. Call {' or '.join(finish)} now with what you have "
                            "verified so far; say plainly what you couldn't check."})
        for note in heartbeat.take_advice(project_id, agent):  # the watcher's help, delivered into this step (agents/watch.py)
            results.append({"type": "text", "text": f"[{note['from']}, who is watching your work] {note['text']}"})
            store.append_log("work", {"agent": agent, "phase": "say", "turn": turn, "text": f"Advice from {note['from']}: {note['text'][:3000]}"})
        messages.append({"role": "user", "content": results})  # all results in ONE user message
    raise AgentError(f"Stopped after {max_turns} turns without finishing.")
