"""Sage — the project's Q&A guide (the bubble inside each project).

guide.answer: answers one question about this project from its real records, read-only: every file in any version,
the crew room, approvals, change requests, bugs and agent states. Cites where it found things. Never changes anything.
Sonnet 5 at medium effort: fast and cheap; billed to the project like any agent.
"""
from __future__ import annotations

import json
import re
import time

from sqlalchemy import select

from app.agents import llm
from app.agents.base import Tool, obj, run_loop
from app.agents.buildkit import TEXT_FILE
from app.db.base import SessionLocal
from app.db.models import AgentState, Approval, AssistantMessage, ChangeRequest, Project
from app.orchestrator import crewchat
from app.orchestrator.runner import JobContext, handler
from app.services.storage import ProjectStore, StorageError

SECRET = re.compile(r"(?i)(aws_secret_access_key|aws_session_token|password|secret)\s*[=:]\s*\S+")


def redact(line: str) -> str:
    return SECRET.sub(lambda m: m.group(1) + "=[redacted]", line)


async def history(project_id: str, limit: int = 60) -> list[AssistantMessage]:
    async with SessionLocal() as db:
        rows = (await db.execute(select(AssistantMessage).where(AssistantMessage.project_id == project_id)
                                 .order_by(AssistantMessage.id.desc()).limit(limit))).scalars().all()
        return list(reversed(rows))


async def _update(msg_id: int, **fields) -> None:
    async with SessionLocal() as db:
        m = await db.get(AssistantMessage, msg_id)
        for k, v in fields.items():
            setattr(m, k, v)
        await db.commit()


def tools(project_id: str, refs: list[str]) -> list[Tool]:
    store = ProjectStore(project_id)

    def ver(v: str) -> str | None:
        return v.strip() or None

    async def list_files(a: dict):
        try:
            tree = store.tree(ver(a["version"]))
        except (StorageError, FileNotFoundError):
            return "No such version."
        rows = [f"{f['path']} ({f['size']} B)" for f in tree if f["path"].startswith(a["prefix"])]
        return {"version": a["version"] or store.manifest()["current_version"], "versions": store.versions(), "files": rows[:400]}

    async def read_file(a: dict):
        try:
            text = store.read(a["path"], ver(a["version"])).decode("utf-8", errors="replace")
        except StorageError:
            return f"No file {a['path']} in {a['version'] or 'the current version'}. Use list_files."
        refs.append(a["path"] + (f"@{a['version']}" if a["version"] else ""))
        lines = text.splitlines()
        start = max(1, a["start_line"])
        chunk = lines[start - 1:start - 1 + 400]
        body = "\n".join(f"{i}: {redact(ln)}" for i, ln in enumerate(chunk, start))
        more = f"\n… {len(lines) - (start - 1 + len(chunk))} more lines (read again with start_line)" if start - 1 + len(chunk) < len(lines) else ""
        return f"{a['path']} ({len(lines)} lines)\n{body}{more}"

    async def search_project(a: dict):
        try:
            tree = store.tree(ver(a["version"]))
        except (StorageError, FileNotFoundError):
            return "No such version."
        try:
            rx = re.compile(a["query"], re.I)
        except re.error:
            rx = re.compile(re.escape(a["query"]), re.I)
        hits = []
        for f in tree:
            if not TEXT_FILE.search(f["path"]) and not f["path"].endswith(".drawio"):
                continue
            try:
                text = store.read(f["path"], ver(a["version"])).decode("utf-8", errors="replace")
            except StorageError:
                continue
            for i, ln in enumerate(text.splitlines(), 1):
                if rx.search(ln):
                    hits.append(f"{f['path']}:{i}: {redact(ln.strip())[:220]}")
                    if len(hits) >= 60:
                        return "\n".join(hits) + "\n… (more; narrow the search)"
        return "\n".join(hits) or "No matches."

    async def project_status(_: dict):
        async with SessionLocal() as db:
            p = await db.get(Project, project_id)
            agents = (await db.execute(select(AgentState).where(AgentState.project_id == project_id))).scalars().all()
            aps = (await db.execute(select(Approval).where(Approval.project_id == project_id).order_by(Approval.created_at))).scalars().all()
            crs = (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id).order_by(ChangeRequest.number))).scalars().all()
        try:
            qa = json.loads(store.read("reports/qa.json"))
        except (StorageError, ValueError):
            qa = {}
        return {
            "project": {"name": p.name, "status": p.status, "current_version": p.current_version, "versions": store.versions(),
                        "cost_usd": round(p.cost_usd, 2), "budget_usd": p.budget_usd, "last_activity": p.last_activity},
            "agents": [{"agent": a.agent, "status": a.status, "activity": a.activity, "cost_usd": round(a.cost_usd, 3)} for a in agents],
            "approvals": [{"title": a.title, "stage": a.stage, "status": a.status, "comment": a.comment} for a in aps if a.status != "superseded"],
            "change_requests": [{"label": f"CR-{c.number:03d}", "status": c.status, "route": c.route, "text": c.text[:400],
                                 "version": f"{c.version_from}→{c.version_to}" if c.version_to else c.version_from} for c in crs],
            "bugs": [{k: b.get(k) for k in ("id", "status", "severity", "title", "fixed_in")} for b in qa.get("bugs", [])],
        }

    async def crew_room(a: dict):
        rows = await crewchat.history(project_id)
        if a["agent"]:
            rows = [m for m in rows if a["agent"] in (m["sender"], m["to"])]
        if a["contains"]:
            rows = [m for m in rows if a["contains"].lower() in m["text"].lower()]
        return [f"[{m['created_at'][:16]}] {m['sender']} → {m['to']} ({m['kind']}): {m['text'][:500]}" for m in rows[-a["limit"]:]]

    v = {"type": "string", "description": "a version like v1.1, or empty for the current one"}
    return [
        Tool("project_status", "Where the project stands: agents, approvals, change requests, bugs, versions, cost.", obj({}), project_status),
        Tool("search_project", "Search every text file (docs, Terraform, code, tests, reports) for a word or regex.",
             obj({"query": {"type": "string"}, "version": v}), search_project),
        Tool("read_file", "Read a project file (with line numbers).",
             obj({"path": {"type": "string"}, "version": v, "start_line": {"type": "integer"}}), read_file),
        Tool("list_files", "List the project's files (optionally under a folder prefix) and its versions.",
             obj({"prefix": {"type": "string", "description": "e.g. infra/ or empty for all"}, "version": v}), list_files),
        Tool("crew_room", "What the agents told each other (hand-offs, decisions, issues, fixes).",
             obj({"agent": {"type": "string", "description": "cto, intake, ba, ta, tp, de, qa, or empty"},
                  "contains": {"type": "string", "description": "text filter, or empty"}, "limit": {"type": "integer"}}), crew_room),
    ]


@handler("guide.answer", resumable=False)
async def answer(ctx: JobContext) -> None:
    pid, msg_id = ctx.project_id, ctx.payload["message_id"]
    past = [m for m in await history(pid, 24) if m.id != msg_id and m.status == "done" and m.text]
    messages: list[dict] = []
    for m in past:
        role = "user" if m.role == "user" else "assistant"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + m.text
        else:
            messages.append({"role": role, "content": m.text})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    if not messages or messages[-1]["role"] != "user":
        await _update(msg_id, text="I didn't get a question.", status="error")
        return
    refs: list[str] = []
    typed: list[str] = []
    last = {"t": 0.0}

    async def on_text(t: str) -> None:
        typed.append(t)
        if time.monotonic() - last["t"] > 0.4:
            last["t"] = time.monotonic()
            await _update(msg_id, text="".join(typed))

    async def on_metrics(_: dict) -> None:  # a turn ended: the next turn's text replaces it ("let me check…" → the answer)
        typed.clear()

    try:
        res = await run_loop(project_id=pid, agent="guide",
                             system=[{"type": "text", "text": llm.prompt("guide.md"), "cache_control": {"type": "ephemeral"}}],
                             messages=messages, tools=tools(pid, refs), max_turns=10, purpose="guide", on_text=on_text,
                             on_metrics=on_metrics, narrate=False)
        text = res.last_text.strip() or res.text.strip() or "I couldn't find an answer in the project."
        await _update(msg_id, text=text, refs=sorted(set(refs)), status="done")
    except Exception as exc:  # noqa: BLE001 — shown to the user in the bubble
        msg = str(exc) if isinstance(exc, llm.BudgetExceeded) else f"Sorry, I couldn't answer that: {str(exc)[:200]}"
        await _update(msg_id, text=msg, status="error")
    from app.orchestrator.bus import bus

    await bus.publish("assistant.updated", project_id=pid, data={"message_id": msg_id}, persist=False)  # live only, not the feed
