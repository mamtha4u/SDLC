"""The crew watch: Orion and Archie keep an eye on everyone who works (user, 10-05: "our CTO (for all) and TA (for Terra
and Dev) need to monitor all agents and ask them if all is OK; if no response, restart the agents to come out of the
error situation… self-healing, agents helping agents"; seen that morning: Dev's first model call hung for 15 minutes).

Every settings.crew_watch_seconds, for every running agent job:
- **Signs of life** come from heartbeat.beat: every streamed model event and every tool start/end. A healthy model call
  beats every second; a hung one never does, so silence is exact, not guessed.
- **CHECKIN_S of silence**: the watcher asks in the crew room ("Dev, no progress for 3 min: everything OK?"); when the
  agent beats again, it answers that it's back on track.
- **RESTART_S of silence** (TOOL_GRACE_S while a tool such as the test sandbox runs): the watcher restarts the step.
  The agent resumes from its saved work (Dev keeps his files; Echo re-reads the chat). At most MAX_RESTARTS per step;
  Terraform runs (plan/apply/destroy/restore/drift) are never killed mid-run (the state lock, half-changed AWS): the
  watcher tells the user instead.
- **Longer than usual but alive** (Terra writing Terraform, Dev writing code): Archie looks over the shoulder once
  (`ta.advise`) and his hint goes into the agent's next step (heartbeat.advise). The agent loop also calls him when the
  platform rejects the same submission 3 times (base._ask_archie).
Who watches whom: Archie watches Terra and Dev (and Orion); Orion watches Echo, Atlas, Archie and Quinn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timezone

from sqlalchemy import select

from app.agents import llm
from app.agents.base import Tool, obj, run_loop
from app.db.base import SessionLocal
from app.db.models import AgentState, Job, Project
from app.orchestrator import crewchat, heartbeat, runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import progress
from app.services.storage import ProjectStore, StorageError

log = logging.getLogger("orkestra.watch")

WATCHER = {"tp": "ta", "de": "ta", "cto": "ta", "intake": "cto", "ba": "cto", "ta": "cto", "qa": "cto"}
JOB_AGENTS = {"cto.unblock": ("cto", "ta")}  # jobs where another agent does part of the work (Archie diagnoses)
NO_RESTART = {"tp.deploy", "tp.apply", "tp.destroy", "tp.restore", "tp.drift"}
TOOLISH = NO_RESTART | {"de.deploy"}  # long runs without model streaming (terraform, docker builds): longer thresholds
CHECKIN_S, RESTART_S = 180, 420  # while a model call should be streaming
TOOL_CHECKIN_S, TOOL_GRACE_S = 600, 1500  # while a tool runs (the test sandbox, terraform; an RDS apply takes ~15 min)
MAX_RESTARTS = 2
LONG_FACTOR, LONG_MIN_S = 2.5, 12 * 60
_seen: dict[str, dict] = {}
_advised_at: dict[tuple[str, str], float] = {}


def _name(agent: str) -> str:
    return crewchat.NAMES.get(agent, agent)


def _mins(s: float) -> str:
    return f"{max(1, round(s / 60))} min"


async def watch() -> None:
    from app.core.config import get_settings

    every = get_settings().crew_watch_seconds
    if every <= 0:
        return
    await asyncio.sleep(30)
    while True:
        try:
            await sweep()
        except Exception as exc:  # noqa: BLE001 (the watch never stops)
            log.warning("crew watch: %s", exc)
        await asyncio.sleep(every)


async def sweep(now: float | None = None) -> list[tuple[str, str]]:
    """One look at every running agent job. Returns what it did [(action, job id)] (for the tests)."""
    now = now or time.time()
    actions: list[tuple[str, str]] = []
    async with SessionLocal() as db:
        rows = (await db.execute(select(Job).join(Project, Job.project_id == Project.id)
                                 .where(Job.status == "running", Project.paused.is_(False)))).scalars().all()
        jobs = [(j.id, j.kind, j.project_id, dict(j.payload or {}), j.started_at) for j in rows]
    running = {j[0] for j in jobs}
    for jid in [k for k in _seen if k not in running]:
        _seen.pop(jid, None)
    actions += await _revive_dead_steps(now)
    for jid, kind, pid, payload, started_at in jobs:
        agent = kind.split(".", 1)[0]
        if agent not in WATCHER or not started_at:
            continue
        started = (started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)).timestamp()
        if now - started < 60:
            continue
        beats = [b for b in (heartbeat.last(pid, a) for a in JOB_AGENTS.get(kind, (agent,))) if b]
        hb = max(beats, key=lambda b: b["at"]) if beats else None
        last = max(started, hb["at"] if hb else 0)
        silence = now - last
        what = (hb or {}).get("what", "starting")
        in_tool = what.startswith("tool:") or kind in TOOLISH
        limit, checkin = (TOOL_GRACE_S, TOOL_CHECKIN_S) if in_tool else (RESTART_S, CHECKIN_S)
        s = _seen.setdefault(jid, {"asked": False, "answered": False, "escalated": False, "advised": False})
        w = WATCHER[agent]
        doing = (what.removeprefix("tool:") if what.startswith("tool:") else "waiting for Claude's reply" if what == "model"
                 else kind if kind in TOOLISH else what)
        if silence > checkin and not s["asked"]:
            await crewchat.say(pid, w, agent, f"{_name(agent)}, I haven't seen any progress from you for {_mins(silence)} ({doing}). "
                               "Everything OK? If you don't respond, I'll restart your step"
                               + (": you'll pick up your saved work." if kind not in NO_RESTART else "."), "question")
            s["asked"] = True
            actions.append(("ask", jid))
        elif s["asked"] and not s["answered"] and silence < 60:
            await crewchat.say(pid, agent, w, f"All good, {_name(w)}: I'm moving again ({doing}).", "answer")
            s["answered"] = True
            actions.append(("answered", jid))
        if silence > limit:
            n = int(payload.get("watch_restart") or 0)
            if kind in NO_RESTART or n >= MAX_RESTARTS:
                if not s["escalated"]:
                    await crewchat.say(pid, w, "user", f"{_name(agent)} hasn't responded for {_mins(silence)} ({doing}). "
                                       + ("I don't restart Terraform mid-run (it could leave AWS half-changed): " if kind in NO_RESTART
                                          else f"I already restarted this step {n} times: ")
                                       + "press **Try again** on the card, or the kill switch to stop.", "issue")
                    s["escalated"] = True
                    actions.append(("escalate", jid))
                continue
            if runner_mod.runner and runner_mod.runner.abort(jid, f"no sign of life for {_mins(silence)} ({doing})"):
                await runner_mod.runner.enqueue(kind, project_id=pid, **{**payload, "watch_restart": n + 1})
                await crewchat.say(pid, w, "crew", f"{_name(agent)} didn't respond for {_mins(silence)} ({doing}), so I restarted the "
                                   f"step (restart {n + 1} of {MAX_RESTARTS}); it picks up its saved work.", "fix")
                await _set_activity(pid, agent, f"Restarted by {_name(w)} after {_mins(silence)} without a response")
                for a in JOB_AGENTS.get(kind, (agent,)):
                    heartbeat.forget(pid, a)
                _seen.pop(jid, None)
                actions.append(("restart", jid))
            continue
        if kind in ("de.code", "tp.iac") and not s["advised"]:
            async with SessionLocal() as db:
                typical = await progress.typical_seconds(db, kind)
            if now - started > max(LONG_FACTOR * typical, LONG_MIN_S):
                s["advised"] = True
                await request_advice(pid, agent, f"taking longer than usual ({_mins(now - started)}; usually about {_mins(typical)})", "")
                actions.append(("advise", jid))
    return actions


NO_RERUN = {"tp.apply", "tp.destroy", "tp.restore"}  # changing AWS: after an interruption, the user decides (Try again)


async def _revive_dead_steps(now: float) -> list[tuple[str, str]]:
    """A step a server restart cut off (its job "interrupted", the card still "working" or "Interrupted… Try again") runs
    again by itself (user, 10-05: Terra's plan died at terraform init and nobody noticed for 8 minutes). Not the steps
    that change AWS: those the user restarts, after a look."""
    out: list[tuple[str, str]] = []
    async with SessionLocal() as db:
        cards = (await db.execute(select(AgentState).join(Project, AgentState.project_id == Project.id)
                                  .where(Project.paused.is_(False), Project.archived.is_(False)))).scalars().all()
        stale = [(c.project_id, c.agent) for c in cards
                 if c.status == "working" or (c.status == "failed" and c.activity.startswith("Interrupted by a platform update"))]
        for pid, agent in stale:
            live = (await db.execute(select(Job.id).where(Job.project_id == pid, Job.status.in_(["queued", "running"]),
                                                          Job.kind.like(f"{agent}.%")))).first()
            if live:
                continue
            last = (await db.execute(select(Job).where(Job.project_id == pid, Job.kind.like(f"{agent}.%"))
                                     .order_by(Job.created_at.desc()).limit(1))).scalars().first()
            if not last or last.status != "interrupted" or last.kind in NO_RERUN:
                continue
            when = last.started_at or last.created_at
            if when and now - (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp() < 60:
                continue
            n = int((last.payload or {}).get("watch_restart") or 0)
            if n >= MAX_RESTARTS:
                continue
            last.status = "cancelled"  # handled: never revived twice
            await db.commit()
            if runner_mod.runner:
                await runner_mod.runner.enqueue(last.kind, project_id=pid, **{**(last.payload or {}), "watch_restart": n + 1})
                await crewchat.say(pid, WATCHER.get(agent, "cto"), "crew", f"{_name(agent)}'s step ({last.kind}) was cut off by a server "
                                   "restart before it finished, so I started it again.", "fix")
                out.append(("revive", last.id))
    return out


async def _set_activity(pid: str, agent: str, activity: str) -> None:
    async with SessionLocal() as db:
        row = (await db.execute(select(AgentState).where(AgentState.project_id == pid, AgentState.agent == agent))).scalar_one_or_none()
        if row:
            row.activity = activity[:255]
            await db.commit()


async def request_advice(pid: str, agent: str, why: str, detail: str) -> bool:
    """At most one look from Archie per agent per 10 minutes."""
    key = (pid, agent)
    if time.time() - _advised_at.get(key, 0) < 600 or not runner_mod.runner:
        return False
    _advised_at[key] = time.time()
    await runner_mod.runner.enqueue("ta.advise", project_id=pid, agent=agent, why=why, detail=detail[-4000:])
    return True


ADVISE = Tool(
    name="advise", terminal=True,
    description="Your hint for the agent you're watching. Call it exactly once.",
    schema=obj({
        "advice": {"type": "string", "description": "Concrete guidance the agent can act on in its next step: what's wrong or what "
                                                    "to try, which file and change. Empty if all is fine."},
        "all_fine": {"type": "boolean", "description": "True if the agent is on a reasonable path and needs nothing"},
    }),
)


def _trail(pid: str, agent: str, n: int = 30) -> str:
    """The agent's latest steps from its work log (calls, results, what it said), compact."""
    f = ProjectStore(pid).root / "logs" / "work.jsonl"
    try:
        lines = f.read_text(encoding="utf-8").splitlines()[-400:]
    except OSError:
        return ""
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("agent") != agent:
            continue
        if r.get("phase") == "call":
            out.append(f"→ {r.get('tool')} {json.dumps(r.get('input'), default=str)[:300]}")
        elif r.get("phase") == "result":
            out.append(f"← {r.get('tool')}{' ERROR' if r.get('error') else ''}: {str(r.get('output'))[:700]}")
        elif r.get("phase") == "say":
            out.append(f"says: {str(r.get('text'))[:400]}")
    return "\n".join(out[-n:])


@handler("ta.advise", resumable=False)
async def advise(ctx: JobContext) -> None:
    """Archie looks at what Terra or Dev is doing right now and sends a hint into their next step (no stopping them)."""
    pid, agent = ctx.project_id, ctx.payload["agent"]
    why, detail = ctx.payload.get("why", ""), ctx.payload.get("detail", "")
    store = ProjectStore(pid)
    try:
        lld = store.read("03_lld.md").decode("utf-8", errors="replace")[:12000]
    except StorageError:
        lld = ""
    ask = (f"You're watching {_name(agent)} while they work ({'writing the Terraform' if agent == 'tp' else 'writing the code and tests'}). "
           f"Why you're looking now: {why}.\n\n" + (f"# The latest problem\n```\n{detail[-3000:]}\n```\n\n" if detail else "")
           + f"# Their latest steps\n{_trail(pid, agent) or '(nothing logged yet)'}\n\n" + (f"# Your LLD (first part)\n{lld}\n\n" if lld else "")
           + "If they're on a reasonable path, say so (all_fine). Otherwise give one concrete hint they can act on in their next "
             "step. Call `advise`.")
    async with SessionLocal() as db:
        row = (await db.execute(select(AgentState).where(AgentState.project_id == pid, AgentState.agent == "ta"))).scalar_one_or_none()
        before = (row.status, row.activity) if row else ("done", "")
    if before[0] != "working":
        await ctx.set_agent("ta", "working", f"👀 Looking over {_name(agent)}'s shoulder: {why}")
    try:
        res = await run_loop(project_id=pid, agent="ta",
                             system=[{"type": "text", "text": llm.prompt("ta_advise.md"), "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}], tools=[ADVISE], max_turns=2, purpose="advise",
                             effort="low", narrate=False)
        a = res.terminal.get("advise") or {"advice": "", "all_fine": True}
        if a["advice"].strip() and not a["all_fine"]:
            heartbeat.advise(pid, agent, "Archie (TA)", a["advice"].strip())
            await crewchat.say(pid, "ta", agent, f"{_name(agent)}, a hint while you work ({why}): {a['advice'].strip()}", "answer")
        else:
            await crewchat.say(pid, "ta", agent, f"Checked on {_name(agent)} ({why}): on a good path, carry on.", "update")
    finally:
        if before[0] != "working":
            await ctx.set_agent("ta", before[0], before[1])
