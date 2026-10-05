"""Everything about one agent in one project, for the agent popup: what needs you, its files, its activity and
its full conversation (with you, with Orion, and its own work: searches, sandbox runs, model turns)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import AgentState, Approval, ChangeRequest, Event, Intake, Job, User
from app.orchestrator import changes, crewchat
from app.orchestrator.bus import serialize
from app.orchestrator.crew import AGENT_KEYS
from app.services.storage import ProjectStore

router = APIRouter(prefix="/api/projects/{project_id}/agents", tags=["agents"])

FILES = {
    "intake": ["00_requirement.md", "inputs/"], "cto": ["plan.md", "changes/"],
    "ba": ["01_data_mapping.md", "mapping/"], "ta": ["02_hld.md", "03_lld.md", "diagrams/"],
    "tp": ["infra/", "reports/infra"], "de": ["src/", "tests/", "reports/pytest"], "qa": ["reports/qa", "bugs/"],
}
STAGE_OF = {"cto": "plan", "ba": "mapping", "ta": "design", "tp": "infra", "de": "code", "qa": "qa"}
NAMES = changes.AGENT_NAMES


def _jsonl(store: ProjectStore, name: str, agent: str, limit: int = 400) -> list[dict]:
    path = store.root / "logs" / f"{name}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-4000:]:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("agent") == agent:
            rows.append(r)
    return rows[-limit:]


def _tool_line(r: dict) -> tuple[str, str]:
    i = r.get("input") or {}
    t = r.get("tool")
    if t == "web_search":
        return "🔎 Searched the web", i.get("query", "")
    if t == "fetch_url":
        return "📖 Read a page", i.get("url", "")
    if t == "pypi_package":
        return "📦 Checked PyPI", f"{i.get('package')} on Python {i.get('python')}"
    if t == "run_transform":
        return "🧪 Ran the mapping in the sandbox", f"{len(i.get('cases', []))} test message(s)"
    if t == "submit_mapping":
        return "📤 Submitted the mapping", f"{len(i.get('rows', []))} fields, {len(i.get('samples', []))} worked examples"
    if t == "message_orion":
        return "📨 Messaged Orion", i.get("message", "")
    if t == "reply":
        return "💬 Answered", i.get("reply", "")
    if t == "submit_plan":
        return "📤 Submitted the plan", i.get("summary", "")[:400]
    if t == "submit_triage":
        return "🧭 Triage decision", f"{i.get('route')} change: {i.get('summary', '')}"
    if t == "capture":
        return "🗂 Filed answers", f"{len(i.get('answers', []))} topic(s)"
    if t == "submit_review":
        return "📋 Submitted a review", i.get("headline", "")
    return f"🔧 {t}", json.dumps(i)[:300]


FILE_TOOLS = {"write_files", "run_tests", "submit_code", "submit_qa"}  # their input carries the files the agent wrote


def _event_line(r: dict) -> tuple[str, str]:
    """(title, detail) of one workbench record."""
    ph, t, i = r.get("phase"), r.get("tool"), r.get("input") or {}
    if ph == "turn":
        return f"🧠 Thinking (step {r.get('turn')})", ""
    if ph == "say":
        return "💬 Said", r.get("text", "")[:600]
    if ph == "tests":
        cov = f" · coverage {r['coverage']}%" if r.get("coverage") is not None else ""
        return (f"🧪 {'Final' if r.get('run') == 'final' else 'Test'} run {'' if r.get('run') == 'final' else r.get('run')}: "
                f"{r.get('passed')}/{r.get('total')} passed{cov}"), ""
    if ph == "deploy":
        return "🚀 Deployed", "; ".join(r.get("changed") or []) or "nothing changed"
    if ph == "result":
        return ("⛔ Rejected" if r.get("error") else "✓ Result") + f" · {t}", (r.get("output") or "")[:500]
    if t in FILE_TOOLS:
        paths = [f.get("path", "") for f in i.get("files") or [] if isinstance(f, dict)]
        if r.get("resumed"):
            return f"📂 Picked up {len(paths)} file(s) from the previous attempt", ", ".join(paths[:8])
        verb = {"write_files": "✍️ Wrote", "run_tests": "✍️ Changed", "submit_code": "📤 Submitted", "submit_qa": "📤 Submitted"}[t]
        return f"{verb} {len(paths)} file(s)" + (" and ran the tests" if t == "run_tests" else ""), ", ".join(paths[:8])
    if t == "http_request":
        return f"🌐 {i.get('method')} {i.get('url', '')[-70:]}", (i.get("body") or "")[:300]
    if t in ("sqs_send", "sqs_receive"):
        return ("📨 Sent to " if t == "sqs_send" else "📬 Read ") + str(i.get("queue_url", "")).rsplit("/", 1)[-1], (i.get("body") or "")[:300]
    if t == "invoke_lambda":
        return f"⚡ Invoked {i.get('function_name')}", (i.get("payload") or "")[:300]
    if t == "read_logs":
        return f"📜 Read logs {i.get('log_group')}", i.get("filter_pattern") or ""
    if t in ("submit_infra", "submit_live", "submit_sanity", "submit_design"):
        return f"📤 {t.replace('_', ' ').capitalize()}", (i.get("summary") or "")[:400]
    title, detail = _tool_line(r)
    return title, detail


@router.get("/{agent}/workbench")
async def workbench(project_id: str, agent: str, since: int = 0, run: int | None = None,
                    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """What an agent is doing behind the scenes, live: its steps, the files it writes (code, tests, Terraform), its test
    runs with coverage, its live calls. `run`: which run (default the latest); `since`: events already received."""
    await owned(project_id, user, db)
    store = ProjectStore(project_id)
    rows = _jsonl(store, "work", agent, limit=6000)
    starts = [i for i, r in enumerate(rows) if r.get("phase") == "turn" and r.get("turn") == 1]
    runs = [{"index": n, "started": rows[s]["ts"], "purpose": rows[s].get("purpose", "")} for n, s in enumerate(starts)]
    if not starts:
        return {"agent": agent, "runs": [], "run": None, "events": [], "files": {}, "tests": [], "total": 0}
    idx = len(starts) - 1 if run is None or not 0 <= run < len(starts) else run
    seg = rows[starts[idx]:starts[idx + 1] if idx + 1 < len(starts) else len(rows)]
    events, files, tests = [], {}, []
    for n, r in enumerate(seg):
        title, detail = _event_line(r)
        ev = {"n": n, "ts": r["ts"], "phase": r.get("phase"), "turn": r.get("turn"), "tool": r.get("tool"), "title": title, "detail": detail,
              "error": bool(r.get("error"))}
        if r.get("phase") == "call" and r.get("tool") in FILE_TOOLS:
            ev["paths"] = []
            for f in (r.get("input") or {}).get("files") or []:
                if isinstance(f, dict) and f.get("path"):
                    ev["paths"].append(f["path"])
                    if n >= since:
                        files[f["path"]] = {"content": str(f.get("content", ""))[:80000], "step": n, "ts": r["ts"]}
            for p in (r.get("input") or {}).get("delete") or []:
                if n >= since:
                    files[str(p)] = {"deleted": True, "step": n, "ts": r["ts"]}
        if r.get("phase") == "tests":
            ev["tests"] = {k: r.get(k) for k in ("run", "total", "passed", "failed", "error", "coverage")}
            if n >= since:
                tests.append({k: r.get(k) for k in ("ts", "run", "total", "passed", "failed", "error", "coverage", "files", "tests", "output")})
        events.append(ev)
    st = (await db.execute(select(AgentState).where(AgentState.project_id == project_id, AgentState.agent == agent))).scalar_one_or_none()
    return {"agent": agent, "runs": runs, "run": runs[idx], "total": len(events), "events": events[since:], "files": files, "tests": tests,
            "state": {"status": st.status, "activity": st.activity, "started_at": st.started_at} if st else None}


RETRYABLE = {"cto": "cto.", "ba": "ba.", "ta": "ta.", "tp": "tp.", "de": "de.", "qa": "qa."}  # Echo has her own retry


@router.post("/{agent}/retry", status_code=202)
async def retry_agent(project_id: str, agent: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Run an agent's last failed or blocked job again (e.g. after raising the budget)."""
    from app.orchestrator import runner as runner_mod

    await owned(project_id, user, db)
    if agent not in RETRYABLE:
        raise HTTPException(404, "This agent can't be retried from here")
    job = (await db.execute(select(Job).where(Job.project_id == project_id, Job.kind.startswith(RETRYABLE[agent]),
                                              Job.kind != "cto.inbox").order_by(Job.created_at.desc()).limit(1))).scalar_one_or_none()
    st = (await db.execute(select(AgentState).where(AgentState.project_id == project_id, AgentState.agent == agent))).scalar_one_or_none()
    if not job or (job.status not in ("failed", "interrupted") and not (st and st.status == "blocked")):
        raise HTTPException(409, "Nothing to retry: the last run didn't fail")
    await runner_mod.runner.enqueue(job.kind, project_id=project_id, **(job.payload or {}))
    await crewchat.say(project_id, "user", agent, f"Retry {NAMES[agent]}'s last step ({job.kind}).", "decision")
    return {"queued": job.kind}


@router.get("/{agent}")
async def agent_detail(project_id: str, agent: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    if agent not in AGENT_KEYS:
        raise HTTPException(404, "Unknown agent")
    store = ProjectStore(project_id)
    state = (await db.execute(select(AgentState).where(AgentState.project_id == project_id, AgentState.agent == agent))).scalar_one_or_none()
    approvals = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.agent == agent,
                                                         Approval.status != "superseded").order_by(Approval.created_at))).scalars().all()
    crs = (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id)
                            .order_by(ChangeRequest.number))).scalars().all()
    mine = [c for c in crs if agent in ("cto", "intake") or agent in [a["agent"] for a in (c.triage or {}).get("affected_agents", [])]
            or (c.route == "mapping" and agent == "ba")]
    events = (await db.execute(select(Event).where(Event.project_id == project_id, Event.agent == agent)
                               .order_by(Event.id.desc()).limit(150))).scalars().all()

    # files this agent produced (current version)
    try:
        tree = store.tree()
    except FileNotFoundError:
        tree = []
    files = [f for f in tree if any(f["path"] == p or (p.endswith("/") and f["path"].startswith(p)) for p in FILES.get(agent, []))]

    # the conversation: you ↔ agent, Orion → agent, and the agent's own work, in time order
    convo: list[dict] = []
    if agent == "intake":
        intake = await db.get(Intake, project_id)
        for m in (intake.chat if intake else []) or []:
            if m["role"] == "orion" or m.get("cr"):
                convo.append({"ts": m.get("ts"), "who": "orion", "title": f"Orion → Echo: {m.get('label', 'change request')}"
                              if m.get("kind") != "reply" else "Orion → Echo", "body": m["text"]})
            elif m["role"] == "note":
                continue  # Echo's messages to Orion come from the crew room below
            else:
                convo.append({"ts": m.get("ts"), "who": "user" if m["role"] == "user" else "agent", "title": "", "body": m["text"]})
    for a in approvals:
        convo.append({"ts": a.created_at.isoformat(), "who": "agent", "title": f"Asked for your approval: {a.title}", "body": a.summary})
        if a.decided_at:
            convo.append({"ts": a.decided_at.isoformat(), "who": "user",
                          "title": "You approved" if a.status == "approved" else "You asked for changes", "body": a.comment or ""})
    # what this agent said in the crew room, and what others said to it
    await crewchat.backfill(project_id)
    for m in await crewchat.history(project_id):
        if m["kind"] == "question" or (m["sender"] == "user" and m["kind"] == "decision"):
            continue  # approvals are listed above
        if m["sender"] == agent:
            convo.append({"ts": m["created_at"], "who": "agent", "title": f"→ {crewchat.NAMES.get(m['to'], m['to'])}", "body": m["text"]})
        elif m["to"] in (agent, "crew") and m["sender"] != agent and (m["to"] == agent or m["kind"] in ("handoff", "decision")):
            who = "user" if m["sender"] == "user" else "orion" if m["sender"] == "cto" else "peer"
            convo.append({"ts": m["created_at"], "who": who, "sender": m["sender"],
                          "title": f"{crewchat.NAMES.get(m['sender'], m['sender'])} → {crewchat.NAMES.get(m['to'], m['to'])}",
                          "body": m["text"]})
    if agent != "intake":
        for r in _jsonl(store, "tools", agent):
            title, body = _tool_line(r)
            convo.append({"ts": r["ts"], "who": "work", "title": title, "body": body})
        for r in _jsonl(store, "llm", agent):
            if r.get("retry"):
                convo.append({"ts": r["ts"], "who": "work", "title": "↻ Retried after a temporary AWS error", "body": r.get("error", "")[:200]})
            elif r.get("text_preview"):
                convo.append({"ts": r["ts"], "who": "agent", "title": "", "body": r["text_preview"]})
    convo = sorted((c for c in convo if c.get("ts")), key=lambda c: c["ts"])

    return {
        "agent": agent,
        "state": {"status": state.status, "activity": state.activity, "tokens_in": state.tokens_in, "tokens_out": state.tokens_out,
                  "cost_usd": state.cost_usd, "retries": state.retries, "started_at": state.started_at,
                  "ended_at": state.ended_at} if state else None,
        "stage": STAGE_OF.get(agent),
        "approvals": [{"id": a.id, "title": a.title, "summary": a.summary, "status": a.status, "comment": a.comment,
                       "created_at": a.created_at, "decided_at": a.decided_at, "stage": a.stage} for a in reversed(approvals)],
        "changes": [changes.out(c) for c in reversed(mine)],
        "files": files,
        "version": store.manifest()["current_version"] if store.root.exists() else "v1",
        "events": [serialize(e) for e in events],
        "conversation": convo[-300:],
    }
