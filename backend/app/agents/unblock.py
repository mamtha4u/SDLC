"""Agents fix their own problems (user, 10-04: "what if Terra or Dev get stuck? … Terra can go to TA and CTO, discuss
the problem, and they need to bring a solution and solve it themselves. The user can't keep clicking Try again… if a
decision is needed they can ask the user; if the user isn't sure, the agents suggest and solve it themselves").

When Terra or Dev (Sonnet) fail at a step they couldn't solve themselves, they escalate (`escalate`) instead of stopping
at a red "Try again":
1. **Archie** (TA, Opus) reads the error, the Terraform or code, his LLD and the crew room, and diagnoses it: the cause
   in plain words, a category and a concrete fix (`cto.unblock`, step 1, read-only tools).
2. **Orion** (CTO, Opus) weighs Archie's proposal and decides the route: Terra or Dev applies the fix (a fixed plan
   still waits for the user's approval as always), a short wait and retry for a passing AWS hiccup, a question for the
   user with a suggested answer, or a plain explanation when it's outside the crew's reach (e.g. the account's
   permission ceiling).
At most MAX_AUTO automatic rounds per agent until it succeeds again (`clear`); after that it comes to the user with the
diagnosis and Try again. Everything is said in the crew room, so the user sees them work it out.
"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from app.agents import llm
from app.agents.base import Tool, obj, run_loop
from app.db.base import SessionLocal
from app.db.models import AgentState
from app.orchestrator import crewchat, runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import aws_access
from app.services.storage import ProjectStore, StorageError

MAX_AUTO = 2
STORE = "unblock"  # deploy/unblock.json: {agent: {"attempts": n, "history": [{at, kind, what, error, diagnosis, route}]}}
NAMES = {"tp": "Terra", "de": "Dev"}
JOB_LABEL = {"tp.iac": "writing the Terraform", "tp.deploy": "terraform plan", "tp.apply": "terraform apply", "tp.destroy": "tear down",
             "de.code": "writing the code and tests", "de.deploy": "the deploy and live check"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load(pid: str) -> dict:
    return aws_access.load(pid, STORE) or {}


def clear(pid: str, agent: str) -> None:
    """The agent succeeded: a new problem later gets fresh automatic rounds."""
    rec = load(pid)
    if rec.pop(agent, None) is not None:
        aws_access.save(pid, rec, STORE)


async def escalate(ctx: JobContext, agent: str, kind: str, what: str, exc: Exception) -> bool:
    """Terra or Dev couldn't solve `exc` in step `kind`: hand it to Archie and Orion. True when they took it (the job
    still ends as failed; the fix arrives as new jobs). False: budget, kill switch, or the automatic rounds are used up."""
    if isinstance(exc, llm.BudgetExceeded) or not ctx.project_id or llm.transient(exc):
        return False  # a busy Claude isn't a problem to diagnose: the runner re-runs the step by itself
    pid = ctx.project_id
    rec = load(pid)
    a = rec.get(agent) or {"attempts": 0, "history": []}
    if a["attempts"] >= MAX_AUTO:
        last = (a["history"] or [{}])[-1]
        await crewchat.say(pid, "cto", "user", f"{NAMES.get(agent, agent)} is still stuck after {MAX_AUTO} rounds of fixes from Archie and me. "
                           f"What we know: {last.get('diagnosis') or str(exc)[:300]} Press **Try again** after a change, or tell us what you "
                           "want with a **Change request**.", "question")
        return False
    a["attempts"] += 1
    a["history"] = (a["history"] + [{"at": _now(), "kind": kind, "what": what, "error": str(exc)[-6000:]}])[-6:]
    rec[agent] = a
    aws_access.save(pid, rec, STORE)
    name = NAMES.get(agent, agent)
    await ctx.set_agent(agent, "blocked", f"Stuck on {JOB_LABEL.get(kind, kind)}: Archie and Orion are working out a fix")
    await ctx.set_project(status="running", last_activity=f"{name} asked Archie and Orion for help ({JOB_LABEL.get(kind, kind)})")
    await crewchat.say(pid, agent, "ta", f"Archie, Orion: I couldn't solve this one myself ({JOB_LABEL.get(kind, kind)}). {what}: "
                       f"{_tail(str(exc), 700)}", "question")
    await runner_mod.runner.enqueue("cto.unblock", project_id=pid, agent=agent, step=kind, what=what, attempt=a["attempts"],
                                    retry=dict(ctx.payload or {}))
    return True


def _tail(text: str, n: int) -> str:
    """Terraform puts the useful part ("Error: …") at the end, after pages of progress lines."""
    i = text.find("Error:")
    return (text[i:] if i >= 0 else text)[:n]


DIAGNOSE = Tool(
    name="diagnose", terminal=True,
    description="Your diagnosis for the stuck agent and Orion. Call it exactly once.",
    schema=obj({
        "cause": {"type": "string", "description": "What went wrong and why, in plain English for the user, 1-3 sentences"},
        "category": {"type": "string", "enum": ["terraform", "code", "transient", "permissions", "platform", "decision"],
                     "description": "terraform/code: the agent's files must change; transient: an AWS hiccup a retry fixes; "
                                    "permissions/platform: the crew's AWS access or the account blocks it (the agents can't change "
                                    "that); decision: only the user can choose"},
        "fix": {"type": "string", "description": "For terraform/code: exact instructions (which file, what to change, why). "
                                                 "For decision: the question and your recommended answer. Else: what would fix it"},
        "design_change": {"type": "boolean", "description": "True if the fix changes what the LLD says (resource, setting, service)"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    }),
)

DECIDE = Tool(
    name="decide", terminal=True,
    description="Your decision as CTO. Call it exactly once.",
    schema=obj({
        "route": {"type": "string", "enum": ["fix_terraform", "fix_code", "retry", "ask_user", "platform"]},
        "brief": {"type": "string", "description": "For fix_terraform/fix_code: the exact brief for Terra or Dev (Archie's fix, refined). Else empty"},
        "user_message": {"type": "string", "description": "1-2 plain sentences for the user: what happened and what the crew does now"},
        "question": {"type": "string", "description": "For ask_user: the decision the user must make, else empty"},
        "suggested_answer": {"type": "string", "description": "For ask_user: your recommendation, else empty"},
        "retry_after_s": {"type": "integer", "description": "For retry: seconds to wait (10-180), else 0"},
    }),
)


async def _status(pid: str, agent: str) -> tuple[str, str]:
    async with SessionLocal() as db:
        row = (await db.execute(select(AgentState).where(AgentState.project_id == pid, AgentState.agent == agent))).scalar_one_or_none()
        return (row.status, row.activity) if row else ("waiting", "")


def _context(pid: str, agent: str, kind: str, what: str, history: list[dict]) -> str:
    store = ProjectStore(pid)

    def read(path: str, limit: int) -> str:
        try:
            return store.read(path).decode("utf-8", errors="replace")[:limit]
        except StorageError:
            return ""
    files = store.tree()
    infra = [f["path"] for f in files if f["path"].startswith("infra/") and f["path"].endswith((".tf", ".json"))]
    code = [f["path"] for f in files if f["path"].startswith(("src/", "layers/", "tests/"))]
    acc = aws_access.load(pid) or {}
    dep = aws_access.load(pid, "deploy") or {}
    parts = [f"# The stuck agent: {NAMES.get(agent, agent)}, step `{kind}` ({JOB_LABEL.get(kind, kind)})",
             f"# What failed\n{what}\n\n# The error (latest)\n```\n{_tail(history[-1]['error'], 5000) if history else ''}\n```"]
    if len(history) > 1:
        parts.append("# Earlier rounds on this problem (already tried; don't propose the same again)\n" + "\n".join(
            f"- {h['at']} {h['kind']}: {h.get('diagnosis') or _tail(h['error'], 300)} → {h.get('route', '?')}" for h in history[:-1]))
    parts.append(f"# The crew's AWS access\nprefix `{acc.get('prefix')}`, roles {[r.get('role') for r in acc.get('roles') or []]}, "
                 f"status {acc.get('status')}; every role is capped by the account's crew boundary "
                 "(orkestra-* names or the project's tags, eu-west-1 only). The deploy state: "
                 f"{dep.get('status')}, last error kept: {_tail(str(dep.get('error') or ''), 600)}")
    if agent == "tp":
        parts.append("# Terra's Terraform files\n" + "\n\n".join(f"## {p}\n```hcl\n{read(p, 12000)}\n```" for p in infra[:14]))
    else:
        parts.append("# Dev's files (read the ones you need with read_file)\n" + "\n".join(code[:200]))
    lld = read("03_lld.md", 16000)
    if lld:
        parts.append(f"# Your LLD (03_lld.md, first part)\n{lld}")
    return "\n\n".join(parts)


@handler("cto.unblock", resumable=False)
async def unblock(ctx: JobContext) -> None:
    from app.agents import guide

    p, pid = ctx.payload, ctx.project_id
    agent, kind, what = p["agent"], p["step"], p.get("what", "")
    name = NAMES.get(agent, agent)
    rec = load(pid)
    history = (rec.get(agent) or {}).get("history") or []
    try:
        # 1. Archie diagnoses (Opus, read-only)
        ta_before = await _status(pid, "ta")
        await ctx.set_agent("ta", "working", f"🩺 Helping {name}: why did {JOB_LABEL.get(kind, kind)} fail?")
        refs: list[str] = []
        res = await run_loop(project_id=pid, agent="ta",
                             system=[{"type": "text", "text": llm.prompt("ta_unblock.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": _context(pid, agent, kind, what, history)
                                        + "\n\nDiagnose it and call `diagnose`."}],
                             tools=[*guide.tools(pid, refs), DIAGNOSE], max_turns=8, purpose="unblock-diagnose", effort="medium")
        dx = res.terminal.get("diagnose") or {"cause": res.last_text[:600] or "Unclear.", "category": "decision", "fix": "",
                                              "design_change": False, "confidence": "low"}
        await ctx.set_agent("ta", ta_before[0] if ta_before[0] != "working" else "done", ta_before[1])
        await crewchat.say(pid, "ta", agent, f"{name}, here's what I see: {dx['cause']}" + (f"\n\nFix: {dx['fix']}" if dx["fix"] else "")
                           + f"\n\n({dx['category']}, confidence {dx['confidence']}" + (", this changes the LLD" if dx["design_change"] else "") + ")",
                           "answer")
        # 2. Orion decides
        await ctx.set_agent("cto", "working", f"Deciding how to unblock {name}")
        ask = (f"{name} is stuck on {JOB_LABEL.get(kind, kind)} (automatic round {p.get('attempt', 1)} of {MAX_AUTO}).\n\n"
               f"# The error\n```\n{_tail(history[-1]['error'], 2500) if history else what}\n```\n\n"
               f"# Archie's diagnosis\nCause: {dx['cause']}\nCategory: {dx['category']} (confidence {dx['confidence']})\n"
               f"Fix: {dx['fix']}\nChanges the LLD: {dx['design_change']}\n\n"
               + ("# Already tried\n" + "\n".join(f"- {h.get('diagnosis', '')} → {h.get('route', '')}" for h in history[:-1]) + "\n\n" if len(history) > 1 else "")
               + "Decide with the `decide` tool.")
        res = await run_loop(project_id=pid, agent="cto",
                             system=[{"type": "text", "text": llm.prompt("cto_unblock.md"), "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}], tools=[DECIDE], max_turns=2, purpose="unblock-decide", effort="low")
        d = res.terminal.get("decide") or {"route": "ask_user", "brief": "", "user_message": dx["cause"], "question": "", "suggested_answer": "",
                                           "retry_after_s": 0}
        if d["route"] == "fix_code" and agent == "tp" and dx["category"] == "terraform":
            d["route"] = "fix_terraform"
        await ctx.set_agent("cto", "done", f"Unblocked {name}: {d['route'].replace('_', ' ')}")
        rec = load(pid)
        if rec.get(agent, {}).get("history"):
            rec[agent]["history"][-1].update(diagnosis=dx["cause"], route=d["route"])
            aws_access.save(pid, rec, STORE)
        await _act(ctx, agent, kind, d, dx, p.get("retry") or {})
    except Exception as exc:  # the helpers failed too: the user gets the original problem and Try again
        await ctx.set_agent("cto", "done", "")
        ta_now = await _status(pid, "ta")
        if ta_now[0] == "working" and "Helping" in ta_now[1]:  # never leave Archie "working" on a dead step (10-05)
            await ctx.set_agent("ta", "done", f"Couldn't finish helping {name}")
        if llm.transient(exc):  # Claude was busy: the runner runs this help again by itself in a minute
            raise
        await ctx.set_agent(agent, "failed", f"{what}: Archie and Orion couldn't work it out ({str(exc)[:120]}). Try again")
        await ctx.set_project(status="failed", last_activity=f"{name}: {what}")
        await crewchat.say(pid, "cto", "user", f"I tried to unblock {name} with Archie but hit a problem myself: {str(exc)[:300]}. "
                           "Press Try again.", "issue")
        raise


async def _act(ctx: JobContext, agent: str, kind: str, d: dict, dx: dict, retry: dict) -> None:
    pid, name = ctx.project_id, NAMES.get(agent, agent)
    route, brief = d["route"], (d.get("brief") or dx.get("fix") or "").strip()
    if d.get("user_message"):
        await crewchat.say(pid, "cto", "user", d["user_message"], "update")
    if route == "fix_terraform":
        # once Terra's fix is applied, the stuck step continues from where it stopped (user, 10-05: "solve the issue Dev
        # faced and continue from there"); tp.apply picks this up instead of asking the user to check AWS again
        rec = load(pid)
        rec["resume"] = {"kind": kind, "payload": retry, "agent": agent, "at": _now()}
        aws_access.save(pid, rec, STORE)
        await crewchat.say(pid, "cto", "tp", f"Terra, apply this fix: {brief}\nThen plan again: the user approves the plan as always. "
                           f"Once it's applied, {name} carries on from where he stopped.", "assign")
        await runner_mod.runner.enqueue("tp.iac", project_id=pid, unblock=True,
                                        feedback=f"Fix from Archie and Orion ({JOB_LABEL.get(kind, kind)} failed):\n{brief}\n\nArchie's diagnosis: {dx['cause']}")
    elif route == "fix_code":
        await crewchat.say(pid, "cto", "de", f"Dev, apply this fix: {brief}", "assign")
        await runner_mod.runner.enqueue("de.code", project_id=pid, unblock=True, feedback=f"Fix from Archie and Orion ({JOB_LABEL.get(kind, kind)} "
                                        f"failed):\n{brief}\n\nArchie's diagnosis: {dx['cause']}")
    elif route == "retry":
        wait = min(max(int(d.get("retry_after_s") or 30), 10), 180)
        await crewchat.say(pid, "cto", agent, f"{name}, nothing to change: AWS needs a moment. Retrying {JOB_LABEL.get(kind, kind)} in {wait} s.", "assign")
        await ctx.set_agent(agent, "blocked", f"Retrying {JOB_LABEL.get(kind, kind)} in {wait} s (Orion: an AWS hiccup)")
        from app.orchestrator import heartbeat

        heartbeat.beat(pid, "cto", "tool:waiting to retry")  # a planned wait, not silence (agents/watch.py)
        await asyncio.sleep(wait)
        await runner_mod.runner.enqueue(kind, project_id=pid, **retry)
    elif route == "ask_user":
        q = d.get("question") or dx.get("fix") or dx["cause"]
        await crewchat.say(pid, "cto", "user", f"I need your decision: {q}" + (f"\n\nMy suggestion: {d['suggested_answer']}" if d.get("suggested_answer") else "")
                           + "\n\nTell us with a **Change request** (or press **Try again** to go ahead as it is).", "question")
        await ctx.set_agent(agent, "failed", f"Needs your decision: {q[:180]}")
        await ctx.set_project(status="waiting", last_activity=f"Orion needs your decision to unblock {name}")
    else:  # platform / permissions: outside what the crew may change
        await crewchat.say(pid, "cto", "user", f"{name} can't fix this from inside the project: {dx['cause']}"
                           + (f" What would fix it: {dx['fix']}" if dx.get("fix") else "") + " It needs a change to the platform or the "
                           "account (the crew's permission ceiling), so the crew stops here; Try again once that's done.", "issue")
        await ctx.set_agent(agent, "failed", f"Outside the crew's reach: {dx['cause'][:180]}")
        await ctx.set_project(status="failed", last_activity=f"{name}: needs a platform change ({dx['cause'][:120]})")
