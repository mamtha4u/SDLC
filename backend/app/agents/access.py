"""Orion's AWS access steps (the CTO holds the higher permission; each agent gets only what its task needs).

Terra's infrastructure gate carries Orion's access plan (drafted from the Terraform in tp.handover, saved as
deploy/access_draft.json). Approving it runs:
cto.grant   creates the roles (always with the crew boundary) and the Terraform state bucket with the platform role,
            then hands over to Terra (tp.deploy: plan, and create what was approved). Access that's already live and
            unchanged goes straight on.
cto.access  (older projects, after QA) drafts the access on its own "access" gate.
"""
from __future__ import annotations

import asyncio

from app.orchestrator import crewchat, flow
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import aws_access
from app.services.storage import ProjectStore


@handler("cto.access", resumable=False)
async def access(ctx: JobContext) -> None:
    pid = ctx.project_id
    store = ProjectStore(pid)
    await ctx.set_agent("cto", "working", "🔐 Drafting each agent's AWS access for this project")
    await ctx.set_project(status="running", last_activity="Orion is drafting the crew's AWS access")
    try:
        plan = aws_access.draft(pid)
    except aws_access.AccessError as exc:
        await _blocked(ctx, str(exc))
        return
    current = aws_access.load(pid)
    if plan["problem"]:
        await crewchat.say(pid, "cto", "tp", f"I can't grant AWS access for this Terraform: {plan['problem']}", "issue")
        await _blocked(ctx, plan["problem"])
        return
    store.write("reports/aws_access.md", aws_access.markdown(plan))
    if current and current.get("status") == "active" and aws_access.same_access(current, plan):
        await crewchat.say(pid, "cto", "tp", f"Terra, AWS access for {plan['version']} is unchanged: you, Dev and Quinn keep your "
                           f"roles ({plan['prefix']}* only). Go ahead and plan.", "handoff", files=["reports/aws_access.md"])
        await ctx.set_agent("cto", "done", "AWS access unchanged: roles reused")
        await runner_mod.runner.enqueue("tp.deploy", project_id=pid)
        return
    aws_access.save(pid, plan, "access_draft")
    await crewchat.say(pid, "cto", "crew", f"Drafted the crew's AWS access for {plan['version']}: one role per agent, only "
                       f"{', '.join(aws_access.label(s) for s in plan['services'])} named **{plan['prefix']}\\***, only eu-west-1, "
                       "each capped by the crew boundary. Nothing is created until the user approves.", "update",
                       files=["reports/aws_access.md"])
    await ctx.set_agent("cto", "needs_approval", f"AWS access plan ready: 3 roles for {plan['prefix']}*")
    await ctx.emit("build.updated", "Orion drafted the crew's AWS access", agent="cto")
    await flow.request_approval(pid, "access", f"Orion's AWS access plan (3 roles, {plan['prefix']}*)",
                                f"Terra builds, Dev deploys his code, Quinn tests the live flow: each with its own role, scoped to "
                                f"{plan['prefix']}* in eu-west-1 and capped by the crew boundary.", ["reports/aws_access.md"])


@handler("cto.grant", resumable=False)
async def grant(ctx: JobContext) -> None:
    pid = ctx.project_id
    draft = aws_access.load(pid, "access_draft")
    current = aws_access.load(pid)
    if not draft:
        if current and current.get("status") == "active":  # nothing new to grant: the crew keeps its roles
            await crewchat.say(pid, "cto", "tp", "Terra, the crew's AWS access is unchanged. Go ahead.", "handoff")
            await runner_mod.runner.enqueue("tp.deploy", project_id=pid, approved_infra=True)
            return
        if current and current.get("status") == "proposed":  # drafted before the access moved into Terra's gate
            draft = current
    if not draft or draft.get("problem"):
        await _blocked(ctx, "There's no approved access plan to grant. Ask Terra to try again.")
        return
    await ctx.set_agent("cto", "working", "🔐 Creating the crew's IAM roles (with the crew boundary)")
    await ctx.set_project(status="running", last_activity="Orion is creating the crew's AWS roles")
    try:
        granted = await asyncio.to_thread(aws_access.grant, pid, draft)
    except Exception as exc:
        await ctx.set_agent("cto", "failed", f"Couldn't create the roles: {str(exc)[:200]}")
        await ctx.set_project(status="failed", last_activity=f"Orion couldn't create the AWS roles: {str(exc)[:160]}")
        await crewchat.say(pid, "cto", "user", f"I couldn't create the crew's roles: {str(exc)[:300]}", "issue")
        await ctx.emit("build.updated", "Orion couldn't create the AWS roles", agent="cto")
        raise
    aws_access.save(pid, granted)
    aws_access.drop(pid, "access_draft")
    for r in granted["roles"]:
        await crewchat.say(pid, "cto", r["agent"], f"Your role is ready: `{r['role']}` (capped by the crew boundary). "
                           "You'll get short-lived credentials for it only while you work.", "update")
    await crewchat.say(pid, "cto", "tp", f"Terraform state goes to s3://{granted['state_bucket']} (versioned, private). "
                       "Terra, the user approved your infrastructure: plan it; the user approves the plan before anything is created.", "handoff")
    await ctx.set_agent("cto", "done", f"Crew roles created for {granted['prefix']}*")
    await ctx.emit("build.updated", "Orion created the crew's AWS roles", agent="cto")
    await runner_mod.runner.enqueue("tp.deploy", project_id=pid, approved_infra=True)


async def _blocked(ctx: JobContext, why: str) -> None:
    pid = ctx.project_id
    await crewchat.say(pid, "cto", "user", f"I've paused before AWS: {why}", "question")
    await ctx.set_agent("cto", "blocked", why[:240])
    await ctx.set_project(status="waiting", last_activity=f"Paused before AWS: {why[:160]}")
    await ctx.emit("build.updated", "Orion paused before AWS", agent="cto")
