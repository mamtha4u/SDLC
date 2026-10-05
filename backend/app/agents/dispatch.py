"""Orion routes the work after the infrastructure exists, like a lead on a real team (no AI call: these are rules).

cto.infra_ready  the user checked Terra's resources in AWS and gave the go-ahead: Terra tells Orion, Orion tells Archie,
                 Archie briefs Dev ("the infrastructure is ready, waiting for your code") → de.code. After an
                 infrastructure-only change, Dev just makes sure his code is still in place (de.deploy sync).
cto.tickets      after Dev's code is approved, after the user lets Quinn's tickets go, after a ticket fix: who's next?
                 Terra's tickets first (infrastructure), then Dev's, then Quinn retests (or tests for the first time);
                 nothing left → the project is done.
"""
from __future__ import annotations

from app.orchestrator import crewchat
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import aws_access, inventory
from app.services import tickets as tk
from app.services.storage import ProjectStore


@handler("cto.infra_ready", resumable=False)
async def infra_ready(ctx: JobContext) -> None:
    pid = ctx.project_id
    version = ProjectStore(pid).manifest()["current_version"]
    dep = aws_access.load(pid, "deploy") or {}
    inv = inventory.load(pid) or {}
    code = aws_access.load(pid, "code") or {}
    cd = (dep.get("outputs") or {}).get("code_deploy") or {}
    intent = dep.get("last_intent") or {}
    primary = [r for r in inv.get("resources", []) if r.get("primary")]
    listed = ", ".join(f"{r['kind']} {r['name']}" for r in primary[:8]) + (f" and {len(primary) - 8} more" if len(primary) > 8 else "")
    await crewchat.say(pid, "tp", "cto", f"Orion, the user checked the infrastructure for {version} in AWS and gave the go-ahead ✅"
                       + (f": {listed}." if listed else "."), "handoff", files=["reports/aws_inventory.md"])
    has_code = code.get("status") == "deployed" and bool(code.get("functions"))
    follow = (dep.get("followups") or {}).get("de")
    if has_code and follow:  # Orion's review said the code must follow this infrastructure change (e.g. a new runtime)
        aws_access.save(pid, {**dep, "followups": {}}, "deploy")
        await crewchat.say(pid, "cto", "de", f"Dev, the infrastructure change is live and confirmed. Your part: {follow}", "handoff")
        await ctx.set_agent("cto", "done", "Infrastructure change confirmed: Dev adapts the code")
        await runner_mod.runner.enqueue("de.code", project_id=pid, feedback=follow)
        return
    if has_code and intent.get("reason") != "build":
        await crewchat.say(pid, "cto", "de", f"Dev, the user confirmed Terra's infrastructure change in {version}. Check your code is "
                           "still in place (a replaced function comes back with placeholder code).", "handoff")
        await ctx.set_agent("cto", "done", "Infrastructure change confirmed")
        await runner_mod.runner.enqueue("de.deploy", project_id=pid, sync=True)
        return
    await crewchat.say(pid, "cto", "ta", f"Archie, Terra's infrastructure for {version} is live and the user confirmed it matches. "
                       "Please brief Dev: it's ready for the code.", "handoff")
    from app.agents.buildkit import files_under
    from app.tools import terraform

    terra_code = terraform.manages_code(files_under(ProjectStore(pid), ("infra/",)))
    fns = [f for f in (cd.get("functions") or {}).values() if isinstance(f, dict)]
    lines = "\n".join(f"- **{f.get('function_name')}** ← your `{str(f.get('source_dir', '')).strip('/')}/`"
                      + (f" (layers: {', '.join(f['layers'])})" if f.get("layers") else "") for f in fns)
    layers = ", ".join(f"{k} → {v}" for k, v in (cd.get("layers") or {}).items())
    await crewchat.say(pid, "ta", "de", f"Dev, the infrastructure is ready and waiting for your code 🚀. Build to my LLD ({version}). "
                       + ("Terra's functions run placeholder code until Terra deploys your packages (after my review and the user's "
                          "approval, you hand them to him):\n" if terra_code else "Terra's functions run placeholder code until you deploy yours:\n")
                       + (lines or "- (see Terra's outputs)")
                       + (f"\nLayers: {layers} (you build them, Terra publishes them)." if layers and terra_code else
                          f"\nLayers you publish: {layers}." if layers else "") + "\nNames, settings and console links are on the AWS tab.",
                       "handoff", files=["03_lld.md", "reports/aws_inventory.md"])
    await ctx.set_agent("ta", "done", f"Briefed Dev: the infrastructure for {version} is ready")
    await ctx.set_agent("cto", "done", "Infrastructure confirmed: Dev is up")
    if has_code:
        await runner_mod.runner.enqueue("de.code", project_id=pid, feedback=f"The design and infrastructure changed in {version}: "
                                        "update the code to match the current LLD and Terra's infrastructure.")
    else:  # Dev's first code: he talks to the developer first (agents/talk.py; straight to de.code for older projects)
        await runner_mod.runner.enqueue("de.talk", project_id=pid)


@handler("cto.tickets", resumable=False)
async def route_tickets(ctx: JobContext) -> None:
    pid = ctx.project_id
    rows = await tk.listing(pid, with_comments=False)
    active = [t for t in rows if t["status"] in tk.ACTIVE]
    for agent, job, what in (("tp", "tp.iac", "infrastructure"), ("de", "de.code", "code")):
        mine = [t for t in active if t["assignee"] == agent]
        if mine:
            labels = ", ".join(t["label"] for t in mine)
            await crewchat.say(pid, "cto", agent, f"{crewchat.NAMES[agent]}, {labels} {'is' if len(mine) == 1 else 'are'} yours ({what}). "
                               "Fix, deploy and hand back to Quinn with a comment.", "assign")
            await ctx.set_agent("cto", "done", f"Sent {labels} to {crewchat.NAMES[agent]}")
            await runner_mod.runner.enqueue(job, project_id=pid, tickets=[t["id"] for t in mine])
            return
    waiting = [t for t in rows if t["assignee"] == "qa" and t["status"] in ("resolved", "open", "reopened", "in_progress")]
    code = aws_access.load(pid, "code") or {}
    from app.agents.qa import plan_ready

    if (waiting or code.get("live_ok") != code.get("deploys")) and not plan_ready(pid):
        # like a real team: the tester writes the scenarios, the lead (the user) approves them, then testing starts
        await crewchat.say(pid, "cto", "qa", "Quinn, the code is deployed and sanity-checked. First the test plan "
                           "(after your kickoff with the tester, if you have one): every scenario (happy path, negative, edge cases, errors, logging), for the user to approve as test lead.", "assign")
        await ctx.set_agent("cto", "done", "Quinn writes the test plan")
        await runner_mod.runner.enqueue("qa.talk", project_id=pid)  # he talks to the tester first (agents/talk.py)
        return
    if waiting or code.get("live_ok") != code.get("deploys"):
        await crewchat.say(pid, "cto", "qa", (f"Quinn, over to you: retest {', '.join(t['label'] for t in waiting)} in AWS." if waiting else
                                              "Quinn, the code is deployed and sanity-checked: test every scenario live in AWS."), "assign")
        await ctx.set_agent("cto", "done", "Quinn is up")
        await runner_mod.runner.enqueue("qa.live", project_id=pid)
        return
    await crewchat.say(pid, "cto", "crew", "Every ticket is closed and the live flow passed Quinn's tests: nothing left to do. 🎉", "decision")
    await ctx.set_agent("cto", "done", "All tickets closed")
    await ctx.set_project(status="completed", progress=1.0, last_activity="Live in AWS and tested: every ticket closed")
