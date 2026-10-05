"""Orion — CTO / orchestrator.

cto.plan: reads the signed-off requirement, researches every factual claim (web search, page fetch, PyPI),
writes plan.md with evidence-backed risks, then opens the "plan" approval gate. Feedback from
"request changes" comes back in payload.feedback and Orion revises.
"""
from __future__ import annotations

import json

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.intake import load_intake, update_intake
from app.db.base import utcnow
from app.orchestrator import crewchat, flow
from app.orchestrator.runner import JobContext, handler
from app.services.storage import ProjectStore
from app.tools import research

AGENTS = ["ba", "ta", "tp", "de", "qa"]
NAMES = {"ba": "Atlas (BA/DA)", "ta": "Archie (TA)", "tp": "Terra (TP)", "de": "Dev (DE)", "qa": "Quinn (QA)"}

SUBMIT_PLAN = Tool(
    name="submit_plan",
    terminal=True,
    description="Submit the delivery plan after your research. Call it exactly once.",
    schema=obj({
        "summary": {"type": "string", "description": "2-4 sentences: what will be built and how"},
        "services": {"type": "array", "items": obj({"service": {"type": "string"}, "purpose": {"type": "string"}})},
        "steps": {"type": "array", "items": obj({
            "agent": {"type": "string", "enum": AGENTS},
            "task": {"type": "string", "description": "What this agent will do for THIS requirement (concise)"},
            "approval": {"type": "string", "description": "What the user approves before the hand-off"},
        })},
        "research": {"type": "array", "description": "Facts you verified", "items": obj({
            "claim": {"type": "string", "description": "What you checked, e.g. 'lxml supports Python 3.14'"},
            "finding": {"type": "string", "description": "What the evidence shows"},
            "verdict": {"type": "string", "enum": ["confirmed", "refuted", "partly", "unverified"]},
            "source": {"type": "string", "description": "URL or tool (e.g. 'PyPI: lxml 6.1.3 cp314 wheels')"},
        })},
        "risks": {"type": "array", "description": "Only evidence-backed risks", "items": obj({
            "risk": {"type": "string"}, "mitigation": {"type": "string"},
            "evidence": {"type": "string", "description": "Why this is a real risk: source or reasoning from the requirement"},
        })},
        "open_points": {"type": "array", "items": {"type": "string"}},
        "feedback_addressed": {"type": "string", "description": "How you handled the user's feedback, or empty"},
        "rerun": {"type": "array", "items": {"type": "string", "enum": AGENTS},
                  "description": "Agents whose work must be (re)done. A first plan: all of them. A change request: ONLY the "
                                 "agents whose output this change alters, e.g. a DLQ or a timeout is design+infra (ta, tp; "
                                 "plus de/qa only if code or tests change); a new field is mapping onwards (ba, ta, tp, de, qa). "
                                 "The others keep their approved work and are skipped."},
    }),
)


def plan_markdown(p: dict) -> str:
    out = ["# Delivery plan (Orion · CTO)", "", p["summary"], ""]
    if p.get("feedback_addressed"):
        out += ["> **Your feedback:** " + p["feedback_addressed"], ""]
    out += ["## AWS services", "", "| Service | Purpose |", "|---|---|"]
    out += [f"| {s['service']} | {s['purpose']} |" for s in p["services"]]
    out += ["", "## Steps", "", "| # | Agent | Task | Your approval |", "|---|---|---|---|"]
    out += [f"| {i} | {NAMES.get(s['agent'], s['agent'])} | {s['task']} | {s['approval']} |" for i, s in enumerate(p["steps"], 1)]
    if p.get("research"):
        out += ["", "## Verified by research", "", "| Claim | Finding | Verdict | Source |", "|---|---|---|---|"]
        out += [f"| {r['claim']} | {r['finding']} | {r['verdict']} | {r['source']} |" for r in p["research"]]
    out += ["", "## Risks", ""] + [f"- **{r['risk']}** → {r['mitigation']}  \n  _Evidence: {r['evidence']}_" for r in p["risks"]]
    if p["open_points"]:
        out += ["", "## Open points", ""] + [f"- {o}" for o in p["open_points"]]
    return "\n".join(out) + "\n"


def research_tools(ctx: JobContext, trail: list[dict], agent: str = "cto") -> list[Tool]:
    """web_search / fetch_url / pypi_package for any agent that must verify facts before stating them."""
    async def note(activity: str) -> None:
        await ctx.set_agent(agent, "working", activity)

    async def search(a: dict):
        await note(f"🔎 Searching: {a['query']}")
        res = await research.web_search(a["query"])
        trail.append({"tool": "web_search", "query": a["query"], "results": len(res)})
        return res or "No results. Try different words."

    async def fetch(a: dict):
        host = a["url"].split("/")[2] if "//" in a["url"] else a["url"]
        await note(f"📖 Reading {host}")
        res = await research.fetch_url(a["url"])
        trail.append({"tool": "fetch_url", "url": res["url"], "status": res["status"]})
        return res

    async def pypi(a: dict):
        await note(f"📦 Checking PyPI: {a['package']} on Python {a['python']}")
        res = await research.pypi_package(a["package"], a["python"])
        trail.append({"tool": "pypi_package", "package": a["package"], "verdict": res.get("verdict")})
        return res

    return [
        Tool("web_search", "Search the web (Bing). Returns titles, URLs and snippets.",
             obj({"query": {"type": "string"}}), handler=search),
        Tool("fetch_url", "Read a public web page as text (docs, release notes, registry pages).",
             obj({"url": {"type": "string"}}), handler=fetch),
        Tool("pypi_package", "Authoritative PyPI facts: latest version, requires_python, wheels available for a Python version.",
             obj({"package": {"type": "string"}, "python": {"type": "string", "description": "e.g. 3.14"}}), handler=pypi),
    ]


SUBMIT_TRIAGE = Tool(
    name="submit_triage",
    terminal=True,
    description="Submit your triage of the change request. Call it exactly once.",
    schema=obj({
        "summary": {"type": "string", "description": "One or two sentences: what the user wants changed"},
        "route": {"type": "string", "enum": ["requirement", "plan", "mapping", "design", "infra", "code", "tests"], "description": (
            "requirement = adds or changes WHAT is built or HOW it must behave (new rule, file, field, format, "
            "service, constraint) → Echo amends the requirement (new version). The others fix one agent's work without "
            "changing the requirement: plan → you; mapping → Atlas; design (HLD, LLD, diagram, quality gates) → Archie; "
            "infra (Terraform, AWS resource names or settings, something wrong in AWS) → Terra; code → Dev; tests (live "
            "test scenarios) → Quinn.")},
        "reason": {"type": "string"},
        "brief": {"type": "string", "description": (
            "Instructions for the agent that handles it. For Echo: exactly what to clarify with the user, e.g. "
            "'ask the user to attach logger.py if not attached; review it and ask about gaps you find'.")},
        "affected_agents": {"type": "array", "description": "ONLY agents whose output must change (e.g. a DLQ changes Archie's "
                            "design and Terra's infrastructure, not Atlas's mapping). Agents not listed keep their work.",
                            "items": obj({"agent": {"type": "string", "enum": AGENTS}, "why": {"type": "string"}})},
        "needs_from_user": {"type": "array", "items": {"type": "string"}, "description": "Files or answers still missing"},
    }),
)


ROUTES = {  # change-request route → (owner, job that revises its work, what it is)
    "mapping": ("ba", "ba.map", "mapping"), "design": ("ta", "ta.design", "design"), "infra": ("tp", "tp.iac", "infrastructure"),
    "code": ("de", "de.code", "code"), "tests": ("qa", "qa.live", "test"),
}


@handler("cto.triage", resumable=False)
async def triage(ctx: JobContext) -> None:
    from app.orchestrator import changes, runner as runner_mod

    pid = ctx.project_id
    cr = await changes.get(ctx.payload["cr_id"])
    intake = await load_intake(pid)
    await ctx.set_agent("cto", "working", f"Triaging {changes.label(cr)}: is it a requirement change or a plan fix?")
    await crewchat.say(pid, "cto", "crew", f"Looking at {changes.label(cr)}: is it a requirement change, a plan fix or "
                       "a mapping fix? Hold on, crew.", "work", cr=cr.id)
    try:
        files = "\n\n".join(f"# Attached: {u['name']}\n{u['text'][:6000]}" for u in (intake.uploads or [])
                            if u["name"] in (cr.attachments or []))
        from app.services.storage import ProjectStore as _PS

        done = [f["path"] for f in _PS(pid).tree() if f["path"] in ("01_data_mapping.md", "02_hld.md", "03_lld.md")
                or f["path"].startswith(("infra/", "src/", "qa/"))]
        stage = ("Work done so far: " + ", ".join(sorted({p.split('/')[0] + ('/' if '/' in p else '') for p in done}))) if done else "No work after the plan yet."
        ask = (f"A change request arrived after the requirement was signed off.\n\n# {changes.label(cr)} (source: {cr.source})\n"
               f"{cr.text}\n\n{files or '(no files attached)'}\n\n# Current signed-off requirement ({cr.version_from})\n"
               f"{intake.requirement_md}\n\n# Current plan summary\n{(intake.plan or {}).get('summary', '(none)')}\n\n# {stage}\n\n"
               f"If this is a requirement change, Echo saves the amended requirement as {ProjectStore(pid).next_version(minor=True)} "
               f"(the {cr.version_from} file stays as it is); use that version name in your brief.\n\n"
               "Decide the route, who is affected and what the handling agent must clarify. Call submit_triage.")
        res = await run_loop(project_id=pid, agent="cto",
                             system=[{"type": "text", "text": llm.prompt("cto.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}], tools=[SUBMIT_TRIAGE], max_turns=3,
                             purpose="triage", effort="medium")
        t = res.terminal.get("submit_triage")
        if not t:
            raise AgentError("Orion finished without a triage decision.")
        cr = await changes.update(cr.id, route=t["route"], triage=t)
        affected = [a["agent"] for a in t["affected_agents"]]
        who = ", ".join(changes.AGENT_NAMES[a] for a in affected) or "nobody downstream yet"
        await ctx.emit("change.triaged", f"Orion triaged {changes.label(cr)} as a {t['route']} change: {t['summary']} "
                                         f"Affects: {who}.", agent="cto", cr=cr.id, route=t["route"], affected=affected,
                       reason=t["reason"])
        await ctx.set_agent("cto", "done", f"{changes.label(cr)} → {t['route']} change. Affects {who}")
        await crewchat.say(pid, "cto", "crew", f"{changes.label(cr)} is a **{t['route']} change**: {t['summary']} "
                           f"Why: {t['reason']} Affects: {who}.", "decision", cr=cr.id)
        whys = {a["agent"]: a["why"] for a in t["affected_agents"]}
        if t["route"] == "requirement":
            new_v = await changes.start_amendment(pid, cr, t["brief"])
            await ctx.set_agent("intake", "working", f"Updating the requirement for {changes.label(cr)} (→ {new_v})")
            await changes.inform_agents(pid, cr, affected, f"requirement is being updated to {new_v}. Wait for it, then use {new_v}.",
                                        whys=whys, version=new_v)
            await ctx.set_project(status="waiting", last_activity=f"Echo is updating the requirement for {changes.label(cr)} ({new_v})")
        elif t["route"] == "plan":
            await changes.update(cr.id, status="planning")
            await crewchat.say(pid, "cto", "crew", f"No new requirement version needed. I'll revise my plan for {changes.label(cr)}.", "update")
            await runner_mod.runner.enqueue("cto.plan", project_id=pid, feedback=f"{cr.text}\n\nTriage brief: {t['brief']}", cr_id=cr.id)
        else:  # one agent's own work, no new requirement version
            owner, job, what = ROUTES[t["route"]]
            await changes.update(cr.id, status="in_progress")
            await crewchat.say(pid, "cto", owner, f"{crewchat.NAMES[owner]}, {changes.label(cr)} is a {what} change for you: {t['brief']}",
                               "handoff", cr=cr.id, files=cr.attachments or [])
            await runner_mod.runner.enqueue(job, project_id=pid, feedback=f"{cr.text}\n\nOrion's brief: {t['brief']}")
    except Exception as exc:
        await changes.update(cr.id, status="triage")
        await ctx.set_agent("cto", "failed", f"Triage failed: {str(exc)[:200]}")
        await crewchat.say(pid, "cto", "crew", f"I couldn't triage {changes.label(cr)}: {str(exc)[:300]}", "issue")
        raise


@handler("cto.plan", resumable=False)
async def plan(ctx: JobContext) -> None:
    from app.orchestrator import changes

    pid = ctx.project_id
    intake = await load_intake(pid)
    feedback = (ctx.payload.get("feedback") or "").strip()
    cr = await changes.get(ctx.payload["cr_id"]) if ctx.payload.get("cr_id") else None
    trail: list[dict] = []
    activity = (f"Re-planning for {changes.label(cr)} (impact analysis)" if cr else
                "Revising the plan with your feedback" if feedback else "Reading the signed-off requirement")
    await ctx.set_agent("cto", "working", activity)
    version = ProjectStore(pid).manifest()["current_version"]
    await crewchat.say(pid, "cto", "intake" if not feedback else "crew",
                       f"Thanks Echo. Re-planning for {changes.label(cr)} from {version}: checking what the change touches." if cr else
                       f"Revising my plan with the user's feedback: {feedback[:400]}" if feedback else
                       f"Thanks Echo. Reading {version} and checking the facts before I plan.", "ack" if not feedback else "update")
    try:
        ask = (f"Here is the signed-off requirement ({version}). Research the facts that matter, then produce the "
               f"delivery plan and call submit_plan.\n\n{intake.requirement_md}")
        if cr and cr.diff:
            ask += (f"\n\n---\n# The requirement changed: {changes.label(cr)} ({cr.version_from} → {cr.version_to})\n{cr.text}\n\n"
                    f"## Diff of 00_requirement.md\n```diff\n{cr.diff[:12000]}\n```\nDo an impact analysis: say in "
                    "`feedback_addressed` what changed, which steps and agents are affected, and that they must use "
                    f"{cr.version_to}.")
        if (feedback or cr) and intake.plan:
            ask += "\n\n---\n# Your previous plan\n" + json.dumps({k: v for k, v in intake.plan.items() if k != "research_trail"}, ensure_ascii=False)
        if feedback:
            ask += f"\n\n# The user's feedback (address every point)\n{feedback}"
        res = await run_loop(
            project_id=pid, agent="cto",
            system=[{"type": "text", "text": llm.prompt("cto.md") + "\n\n" + llm.prompt("org_context.md"),
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": ask}],
            tools=[*research_tools(ctx, trail), SUBMIT_PLAN], max_turns=14, purpose="plan-revise" if feedback else "plan")
        data = res.terminal.get("submit_plan")
        if not data:
            raise AgentError("Orion finished without a plan.")
        data["research_trail"] = trail
        if cr:
            data["cr_id"], data["cr_label"] = cr.id, changes.label(cr)
        else:
            data["rerun"] = list(AGENTS)  # a first plan: everyone works
        ProjectStore(pid).write("plan.md", plan_markdown(data))
        await update_intake(pid, plan=data)
        route, skipped = flow.plan_route(pid, data)
        verified = sum(1 for r in data.get("research", []) if r["verdict"] in ("confirmed", "refuted", "partly"))
        await ctx.set_agent("cto", "done", f"Plan ready: {len(data['steps'])} steps, {verified} facts verified, {len(data['risks'])} risks")
        if route.get("next_agent"):
            await ctx.set_agent(route["next_agent"], "waiting", "Starts when you approve Orion's plan")
        await ctx.emit("intake.updated", f"Orion re-planned for {changes.label(cr)}" if cr else
                       "Orion revised the plan" if feedback else "Orion published the delivery plan", agent="cto",
                       research=len(trail))
        if cr:
            await changes.update(cr.id, status="planning")
        await crewchat.say(pid, "cto", "crew", f"Plan ready for {version}: {len(data['steps'])} steps, {verified} facts verified, "
                           f"{len(data['risks'])} risks. " + (f"Impact of {changes.label(cr)}: {data.get('feedback_addressed', '')[:900]}" if cr else data["summary"]),
                           "update", files=["plan.md"])
        for s in data["steps"]:
            if s["agent"] in skipped:
                continue
            await crewchat.say(pid, "cto", s["agent"], f"{crewchat.NAMES.get(s['agent'], s['agent'])}, your part: {s['task']}", "assign")
        for a in skipped:
            await crewchat.say(pid, "cto", a, f"{crewchat.NAMES.get(a, a)}, {changes.label(cr) if cr else 'this change'} doesn't change your "
                               f"work: your approved output carries over to {version} unchanged. Nothing to do.", "update")
        if route.get("next_agent"):
            nxt = route["next_agent"]
            await crewchat.say(pid, nxt, "cto", f"Copy that. I'll start as soon as the user approves the plan"
                               + (f", from {version}." if cr else "."), "ack")
        title = f"Orion's revised plan ({changes.label(cr)}, requirement {version})" if cr else "Orion's delivery plan"
        await flow.request_approval(pid, "plan", title, data["summary"], ["plan.md", "00_requirement.md"])
    except Exception as exc:
        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("cto", "blocked" if blocked else "failed", str(exc)[:240])
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Orion: {str(exc)[:200]}")
        await crewchat.say(pid, "cto", "user", f"I'm blocked: {str(exc)[:300]}", "issue")
        raise


# ── inbox: another agent tells Orion something mid-task ─────────────────────────────────────────────────────────
REPLY = Tool(
    name="reply",
    terminal=True,
    description="Answer the agent who messaged you, and say what (if anything) changes. Call it exactly once.",
    schema=obj({
        "reply": {"type": "string", "description": "Your answer to the agent, 1-4 sentences, specific"},
        "brief_update": {"type": "string", "description": "An addition to the change request's brief, or empty"},
        "notify": {"type": "array", "description": "Other agents who must know (usually empty)", "items": obj({
            "agent": {"type": "string", "enum": AGENTS}, "note": {"type": "string"}})},
    }),
)


@handler("cto.inbox", resumable=False)
async def inbox(ctx: JobContext) -> None:
    """An agent (e.g. Echo) messaged Orion: he reads it with the context, answers, and tells whoever else must know."""
    import asyncio

    from app.orchestrator import changes

    pid, sender, message = ctx.project_id, ctx.payload["from"], ctx.payload["message"]
    cr = await changes.get(ctx.payload["cr_id"]) if ctx.payload.get("cr_id") else None
    intake = await load_intake(pid)
    version = ProjectStore(pid).manifest()["current_version"]
    ask = (f"{crewchat.NAMES.get(sender, sender)} sent you this message:\n\n> {message}\n\n"
           + (f"# Open change request {changes.label(cr)} (status {cr.status})\n{cr.text}\nAttachments: "
              f"{', '.join(cr.attachments or []) or 'none'}\nYour brief: {(cr.triage or {}).get('brief', '')}\n\n" if cr else "")
           + f"# Current requirement version: {version}\n"
           + ("# Requirement status: signed off\n" if intake.status == "signed_off" else
              f"# Requirement status: {intake.status}: NOT signed off yet. Only the user's Sign off button freezes it and "
              "starts your plan automatically; don't say you have it or that the crew starts.\n")
           + f"# Your plan summary\n{(intake.plan or {}).get('summary', '(no plan yet)')}\n\n"
           "Answer the agent with the `reply` tool.")
    res = await run_loop(project_id=pid, agent="cto",
                         system=[{"type": "text", "text": llm.prompt("cto.md") + "\n\n" + llm.prompt("cto_inbox.md"),
                                  "cache_control": {"type": "ephemeral"}}],
                         messages=[{"role": "user", "content": ask}], tools=[REPLY], max_turns=2, purpose="inbox", effort="low")
    r = res.terminal.get("reply") or {"reply": res.text.strip() or "Noted.", "brief_update": "", "notify": []}
    await crewchat.say(pid, "cto", sender, r["reply"], "answer", cr=cr.id if cr else None)
    if cr and r["brief_update"].strip():
        triage = dict(cr.triage or {})
        triage["brief"] = (triage.get("brief", "") + f"\n\nUpdate from Orion: {r['brief_update'].strip()}").strip()
        await changes.update(cr.id, triage=triage)
    for n in r["notify"]:
        await crewchat.say(pid, "cto", n["agent"], n["note"], "update", cr=cr.id if cr else None)
    if sender == "intake":
        # Echo sees Orion's answer in her chat on the next turn (wait until her current turn has been saved)
        for _ in range(60):
            if not (await load_intake(pid)).busy:
                break
            await asyncio.sleep(1)
        it = await load_intake(pid)
        await update_intake(pid, chat=list(it.chat or []) + [{"role": "orion", "kind": "reply", "ts": utcnow().isoformat(),
                                                               "text": r["reply"]}])
        await ctx.emit("intake.updated", "Orion answered Echo", agent="cto")
