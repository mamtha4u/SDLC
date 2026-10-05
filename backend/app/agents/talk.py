"""Kickoff conversations: every agent interviews the person who owns its phase before it works (user, 10-05: "each area
is handled by a different person… in each phase we interact with the user to get the details").

Echo talks to the business analyst (agents/intake.py). After her, each agent opens its own conversation when its phase
starts: Atlas with the data analyst (is anything transformed, and how), Archie with the technical lead (the architecture
and the tech stack: decided here), Terra with the platform engineer, Dev with the developer, Quinn with the tester. The
agent asks role questions in groups until it understands, plays back what it will do, and starts its real work when the
person confirms (or presses "Start now"). What was agreed is written to talks/<agent>.md, and every later agent's context
carries it (buildkit.talk_notes).

Jobs:
  <agent>.talk   the phase starts: open the conversation (first message), or go straight to the work when the talk is
                 already done, or the project predates talks (settings.talks), or it's a re-run.
  <agent>.talk_reply   one turn after the person's message (streamed → typewriter)
  <agent>.talk_finish  the person pressed "Start now": close the talk and start the work
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import lru_cache

import yaml

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.buildkit import context
from app.db.base import SessionLocal, utcnow
from app.db.models import Intake, PhaseTalk, Project
from app.orchestrator import crewchat
from app.orchestrator import runner as runner_mod
from app.orchestrator.bus import bus
from app.orchestrator.runner import JobContext, handler
from app.services.storage import ProjectStore

AGENTS = ("ba", "ta", "tp", "de", "qa")
WORK = {"ba": "ba.map", "ta": "ta.design", "tp": "tp.iac", "de": "de.code", "qa": "qa.plan"}
NAME = {"ba": "Atlas", "ta": "Archie", "tp": "Terra", "de": "Dev", "qa": "Quinn"}
DOING = {"ba": "the data mapping", "ta": "the HLD, LLD and architecture diagram", "tp": "the Terraform",
         "de": "the code and its unit tests", "qa": "the test plan"}
# what each agent reads before it talks (the settled earlier phases)
SEES = {"ba": {"lld": False, "mapping": False}, "ta": {"lld": False, "mapping": True}, "tp": {"lld": True, "mapping": False},
        "de": {"lld": True, "mapping": True}, "qa": {"lld": True, "mapping": True}}


@lru_cache
def topic_map() -> dict:
    return yaml.safe_load(llm.prompts_path("phase_topics.yaml").read_text(encoding="utf-8"))


def topics(agent: str) -> dict[str, dict]:
    return {q["id"]: {**q, "section": s["title"]} for s in topic_map()[agent]["sections"] for q in s["questions"]}


def person(agent: str) -> str:
    return topic_map()[agent]["person"]


def enabled(project: Project | None) -> bool:
    return bool(project and (project.settings or {}).get("talks"))


async def load(project_id: str, agent: str) -> PhaseTalk | None:
    async with SessionLocal() as db:
        return await db.get(PhaseTalk, (project_id, agent))


async def update(project_id: str, agent: str, **fields) -> None:
    async with SessionLocal() as db:
        row = await db.get(PhaseTalk, (project_id, agent))
        for k, v in fields.items():
            setattr(row, k, v)
        await db.commit()


async def emit(project_id: str, agent: str, message: str) -> None:
    await bus.publish("talk.updated", project_id=project_id, agent=agent, message=message, data={"agent": agent})


# ── the conversation ────────────────────────────────────────────────────────────────────────────────────────────────
CAPTURE = Tool(
    name="capture",
    terminal=True,
    description=("AFTER writing your reply as normal text, call this exactly once: the facts you learned this turn, the "
                 "tap-to-answer options, and `ready` only once your person confirmed you can start."),
    schema=obj({
        "answers": {"type": "array", "description": "Facts learned this turn (or from the earlier documents, in your first message)",
                    "items": obj({"topic_id": {"type": "string", "description": "Exact id from your topic map, e.g. transform.needed"},
                                  "value": {"type": "string", "description": "The answer, in their words where possible; "
                                            "\"you decide: <your recommendation>\" when they left it to you"}})},
        "quick_replies": {"type": "array", "items": {"type": "string"},
                          "description": "2-5 tappable answers for the whole group (under 6 words each)"},
        "ready": {"type": "boolean", "description": "True only when your person confirmed you can start your work"},
    }, required=["answers", "quick_replies"]),
)


def system_blocks(agent: str, ctx_text: str) -> list[dict]:
    ids = "\n".join(f"- {q['id']}: {q['label']}{' (required)' if q.get('required') else ''}" for q in topics(agent).values())
    role = (llm.prompt(f"talk_{agent}.md") + "\n\n" + llm.prompt("talk_common.md") + "\n\n" + llm.prompt("org_context.md")
            + f"\n\n# Your topic map (ids for `answers`)\n{ids}")
    return [{"type": "text", "text": role, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "# What the crew has settled so far (read it; never ask what it answers)\n\n" + ctx_text,
             "cache_control": {"type": "ephemeral"}}]


def answers_block(agent: str, talk: PhaseTalk) -> str:
    known = topics(agent)
    lines = ["# What you've captured so far"]
    for tid, q in known.items():
        v = str((talk.answers or {}).get(tid, "")).strip()
        lines.append(f"- [{tid}] {q['label']}{' *required*' if q.get('required') else ''} → {v or '(open)'}")
    files = [u["name"] for u in talk.uploads or []]
    if files:
        lines.append("\nFiles given in this conversation (their text is in inputs/ above): " + ", ".join(files))
    return "\n".join(lines)


def _messages(agent: str, talk: PhaseTalk, opening: str) -> list[dict]:
    """The conversation as real turns (a cacheable prefix); the current state rides on the last user turn."""
    turns: list[dict] = [{"role": "user", "content": opening}]
    for m in talk.chat or []:
        if m["role"] == "note" or not m.get("text"):
            continue
        role = "assistant" if m["role"] == "agent" else "user"
        if turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + m["text"]
        else:
            turns.append({"role": role, "content": m["text"]})
    if turns[-1]["role"] != "user":
        turns.append({"role": "user", "content": "(Continue: the person hasn't replied yet.)"})
    for i in range(len(turns) - 2, -1, -1):  # cache breakpoint on the last assistant turn
        if turns[i]["role"] == "assistant":
            turns[i] = {"role": "assistant", "content": [{"type": "text", "text": turns[i]["content"], "cache_control": {"type": "ephemeral"}}]}
            break
    last = turns[-1]
    turns[-1] = {"role": "user", "content": [{"type": "text", "text": last["content"]},
                                              {"type": "text", "text": "\n\n---\n" + answers_block(agent, talk)}]}
    return turns


async def _context(pid: str, agent: str) -> str:
    async with SessionLocal() as db:
        intake = await db.get(Intake, pid)
    store = ProjectStore(pid)
    text = context(store, (intake.requirement_md if intake else "") or "(none)", limit=30000, **SEES[agent])
    plan_step = next((s for s in ((intake.plan if intake else None) or {}).get("steps", []) if s.get("agent") == agent), None)
    if plan_step:
        text += f"\n\n# Orion's plan for you\n{plan_step.get('task', '')}"
    if agent in ("de", "qa"):
        import json

        from app.services import aws_access

        dep = aws_access.load(pid, "deploy") or {}
        cd = (dep.get("outputs") or {}).get("code_deploy")
        if cd:
            text += "\n\n# Terra's live infrastructure: where the code goes (output code_deploy)\n```json\n" + json.dumps(cd, indent=2)[:6000] + "\n```"
        sanity = ((aws_access.load(pid, "code") or {}).get("sanity") or {}).get("summary")
        if agent == "qa" and sanity:
            text += f"\n\n# Dev's sanity check of the live flow\n{sanity}"
    return text


async def turn(ctx: JobContext, agent: str, first: bool = False) -> None:
    pid = ctx.project_id
    talk = await load(pid, agent)
    if talk is None or talk.status != "talking":
        return
    typed: list[str] = []
    last = {"t": 0.0, "text": ""}

    async def on_text(t: str) -> None:  # the reply as it's written (polled by the UI, like Echo's typewriter)
        typed.append(t)
        text = "".join(typed)
        if time.monotonic() - last["t"] >= 0.25:
            last["t"], last["text"] = time.monotonic(), text
            await update(pid, agent, draft=text)
            await bus.publish("talk.delta", project_id=pid, agent=agent, data={"agent": agent, "text": text}, persist=False)

    version = ProjectStore(pid).manifest()["current_version"]
    opening = (f"[Orion (CTO) → {NAME[agent]}] Your phase starts now ({version}). Before you write {DOING[agent]}, talk to "
               f"{person(agent)} on the user's team. Open the conversation: what you took from the earlier phases, then your "
               "first group of questions.")
    await update(pid, agent, busy="thinking", draft="", last_error=None)
    await ctx.set_agent(agent, "working", f"Talking to {person(agent)}: reading the earlier phases" if first else f"Replying to {person(agent)}")
    try:
        res = await run_loop(project_id=pid, agent=agent, system=system_blocks(agent, await _context(pid, agent)),
                             messages=_messages(agent, talk, opening), tools=[CAPTURE], max_turns=3,
                             purpose="talk-open" if first else "talk", on_text=on_text, narrate=False,
                             effort=llm.agent_settings(agent).get("chat_effort", "medium"))
        cap = res.terminal.get("capture") or {"answers": [], "quick_replies": []}
        message = res.text.strip() or "Sorry, could you say that again?"
        talk = await load(pid, agent)
        known = topics(agent)
        answers, filled = dict(talk.answers or {}), []
        for a in cap.get("answers") or []:
            if a.get("topic_id") in known and str(a.get("value", "")).strip():
                answers[a["topic_id"]] = str(a["value"]).strip()[:8000]
                filled.append(a["topic_id"])
        chat = list(talk.chat or []) + [{"role": "agent", "text": message, "ts": utcnow().isoformat(), "filled": sorted(set(filled)),
                                         "quick_replies": [q for q in cap.get("quick_replies") or [] if str(q).strip()][:5]}]
        # "ready" counts only after the person has said something (never on the opening message)
        ready = bool(cap.get("ready")) and any(m["role"] == "user" for m in chat)
        await update(pid, agent, chat=chat, answers=answers, busy=None, draft=None)
        await crewchat.say(pid, agent, "user", message, "chat", captured=[known[k]["label"] for k in sorted(set(filled))])
        if ready:
            await finish(ctx, agent)
            return
        await ctx.set_agent(agent, "needs_approval", f"Waiting for your answers ({person(agent)})")
        await ctx.set_project(status="waiting", last_activity=f"{NAME[agent]} asked {person(agent)} a few questions")
        await emit(pid, agent, f"{NAME[agent]} replied" + (f" and noted {len(set(filled))} answer(s)" if filled else ""))
    except Exception as exc:
        msg = str(exc) if isinstance(exc, llm.BudgetExceeded | AgentError) else (
            "Claude on AWS had a temporary problem and didn't finish, even after retrying. Nothing was lost. Press Try again.")
        await update(pid, agent, busy=None, draft=None, last_error={"kind": f"{agent}.talk_reply", "message": msg, "at": utcnow().isoformat()})
        await ctx.set_agent(agent, "blocked" if isinstance(exc, llm.BudgetExceeded) else "failed", msg[:240])
        await ctx.set_project(status="waiting", last_activity=f"{NAME[agent]}: {msg[:200]}")
        await emit(pid, agent, f"{NAME[agent]} hit a problem: {msg[:200]}")
        raise


def record(agent: str, talk: PhaseTalk, version: str, by_button: bool) -> str:
    """talks/<agent>.md: what was agreed (every later agent reads it), then the whole conversation."""
    known = topics(agent)
    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    lines = [f"# {NAME[agent]}'s kickoff with {person(agent)} ({version})", "",
             f"Agreed on {stamp}" + (" (the person pressed Start now: anything still open follows "
                                     f"{NAME[agent]}'s recommendation and is an assumption)." if by_button else "."), "",
             f"## What {person(agent)} told {NAME[agent]}", ""]
    answered = [(q["label"], str((talk.answers or {}).get(tid, "")).strip()) for tid, q in known.items()]
    lines += [f"- **{label}**: {value}" for label, value in answered if value] or ["- (nothing beyond the earlier documents)"]
    still = [q["label"] for tid, q in known.items() if q.get("required") and not str((talk.answers or {}).get(tid, "")).strip()]
    if still:
        lines += ["", f"Still open (use your recommendation and list it as an assumption): {'; '.join(still)}"]
    if talk.uploads:
        lines += ["", "## Files given", "", *[f"- inputs/{u['name']}" for u in talk.uploads]]
    who = {"agent": NAME[agent], "user": person(agent).removeprefix("the ").capitalize()}
    lines += ["", "## The conversation", ""]
    lines += [f"**{who.get(m['role'], m['role'])}:** {m['text']}\n" for m in talk.chat or [] if m.get("text") and m["role"] in who]
    return "\n".join(lines) + "\n"


async def finish(ctx: JobContext, agent: str, by_button: bool = False) -> None:
    """Close the talk, write talks/<agent>.md, start the agent's work with the payload its phase started with."""
    pid = ctx.project_id
    talk = await load(pid, agent)
    if talk is None or talk.status != "talking":
        return
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    store.write(f"talks/{agent}.md", record(agent, talk, version, by_button))
    note = ("You pressed Start now: I'll use my recommendations for anything still open." if by_button else "Thanks, that's clear.")
    chat = list(talk.chat or []) + [{"role": "note", "text": f"{NAME[agent]} is starting {DOING[agent]}", "ts": utcnow().isoformat()}]
    await update(pid, agent, status="done", done_at=utcnow(), chat=chat, busy=None, draft=None)
    work = talk.work or {"job": WORK[agent], "payload": {}}
    await crewchat.say(pid, agent, "cto", f"Kickoff with {person(agent)} done ({note}) What we agreed is in talks/{agent}.md. "
                       f"Starting {DOING[agent]} now.", "ack", files=[f"talks/{agent}.md"])
    await ctx.set_agent(agent, "working", f"Starting {DOING[agent]}")
    await ctx.set_project(status="running", last_activity=f"{NAME[agent]} is starting {DOING[agent]}")
    await emit(pid, agent, f"{NAME[agent]} finished the kickoff and is starting {DOING[agent]}")
    await runner_mod.runner.enqueue(work["job"], project_id=pid, **(work.get("payload") or {}))


async def begin(ctx: JobContext, agent: str) -> None:
    """The phase starts: talk first (new projects), or go straight to the work."""
    pid = ctx.project_id
    payload = {k: v for k, v in ctx.payload.items()}
    async with SessionLocal() as db:
        project = await db.get(Project, pid)
        talk = await db.get(PhaseTalk, (pid, agent))
        if enabled(project) and talk is None:
            db.add(PhaseTalk(project_id=pid, agent=agent, status="talking", work={"job": WORK[agent], "payload": payload}))
            await db.commit()
            talk = "new"
    if talk == "new":
        await crewchat.say(pid, agent, "user", f"Before I start {DOING[agent]}, a few questions for {person(agent)}: "
                           f"they're on my tab.", "question")
        await turn(ctx, agent, first=True)
        return
    if isinstance(talk, PhaseTalk) and talk.status == "talking":  # the phase was handed over again while still talking
        await update(pid, agent, work={"job": WORK[agent], "payload": payload})
        if not talk.chat:
            await turn(ctx, agent, first=True)
        return
    await runner_mod.runner.enqueue(WORK[agent], project_id=pid, **payload)


def _register(agent: str) -> None:
    """The jobs carry the agent's prefix, so the crew watch, the startup check and "Try again" treat them like any of
    the agent's steps (a reply cut off by a restart is run again)."""
    @handler(f"{agent}.talk", resumable=False)
    async def _open(ctx: JobContext) -> None:
        await begin(ctx, agent)

    @handler(f"{agent}.talk_reply", resumable=False)
    async def _reply(ctx: JobContext) -> None:
        await turn(ctx, agent)

    @handler(f"{agent}.talk_finish", resumable=False)
    async def _start_now(ctx: JobContext) -> None:
        await finish(ctx, agent, by_button=True)


for _a in AGENTS:
    _register(_a)


def state(talk: PhaseTalk | None, agent: str) -> dict:
    t = topic_map()[agent]
    return {"agent": agent, "name": NAME[agent], "person": t["person"], "doing": DOING[agent], "sections": t["sections"],
            "status": talk.status if talk else "none", "chat": (talk.chat if talk else None) or [],
            "answers": (talk.answers if talk else None) or {}, "busy": talk.busy if talk else None,
            "draft": talk.draft if talk else None, "last_error": talk.last_error if talk else None,
            "uploads": [{"name": u["name"], "kind": u.get("kind", "document"), "chars": u.get("chars", 0)} for u in (talk.uploads if talk else None) or []],
            "done_at": talk.done_at if talk else None}
