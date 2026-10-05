"""Orion reviews every change to the live infrastructure before anyone touches anything (user, 10-02: "whatever
configuration we change needs to be thoroughly analysed; the CTO gives the OK, and confirms with the user when it's
really a requirement change, e.g. Python → Java").

cto.review     an AI impact analysis of the change request (what it touches: code, layers, requirement; risks with
               researched facts; who must act). Infrastructure only, low risk → Orion gives the OK himself. Otherwise →
               the "change_review" gate: the user confirms (or asks for something else).
cto.change_go  rules, no AI: requirement change → Echo amends the requirement (then the usual re-plan); otherwise the
               names are written (if any), Terra changes the Terraform (or just plans a rename), and Dev's follow-up is
               remembered for after the infrastructure check (Dev adapts and redeploys, Quinn retests).
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.intake import load_intake
from app.db.base import SessionLocal
from app.db.models import ChangeRequest
from app.orchestrator import changes, crewchat, flow
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import aws_access, inventory
from app.services.storage import ProjectStore

VERDICT = {"infra_only": "infrastructure only", "affects_code": "affects the code", "requirement_change": "a requirement change"}
SUBMIT_IMPACT = Tool(
    name="submit_impact",
    terminal=True,
    description="Submit your impact review of the infrastructure change. Call it exactly once.",
    schema=obj({
        "summary": {"type": "string", "description": "2-3 sentences: what changes and what it means"},
        "verdict": {"type": "string", "enum": list(VERDICT)},
        "recommendation": {"type": "string", "enum": ["go", "confirm", "advise_against"]},
        "risks": {"type": "array", "items": obj({"risk": {"type": "string"}, "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                                                 "mitigation": {"type": "string"}})},
        "affected": {"type": "array", "description": "Only agents that must do something", "items": obj({
            "agent": {"type": "string", "enum": ["intake", "ta", "tp", "de", "qa"]}, "what": {"type": "string"}})},
        "questions": {"type": "array", "items": {"type": "string"}, "description": "What the user must confirm or decide (or empty)"},
        "terra_brief": {"type": "string", "description": "Exact instructions for Terra"},
        "dev_brief": {"type": "string", "description": "Exact instructions for Dev if the code must change, else empty"},
    }),
)
CANCEL = re.compile(r"\b(cancel|drop it|abort|forget it|don'?t do|do not do|not needed)\b", re.I)


async def _cr(pid: str, cr_id: str | None, statuses: tuple[str, ...]) -> ChangeRequest | None:
    if cr_id:
        return await changes.get(cr_id)
    async with SessionLocal() as db:
        return (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == pid, ChangeRequest.route == "infra",
                                                             ChangeRequest.status.in_(statuses))
                                 .order_by(ChangeRequest.number.desc()))).scalars().first()


def review_markdown(cr: ChangeRequest, t: dict) -> str:
    lines = [f"# Orion's review of {changes.label(cr)}", "", t["summary"], "",
             f"**Verdict:** {VERDICT[t['verdict']]} · **Recommendation:** {t['recommendation'].replace('_', ' ')}", "",
             "## The change", "", cr.text, ""]
    if t.get("risks"):
        lines += ["## Risks", "", "| Risk | Severity | Mitigation |", "|---|---|---|",
                  *[f"| {r['risk']} | {r['severity']} | {r['mitigation']} |" for r in t["risks"]], ""]
    if t.get("affected"):
        lines += ["## Who does what", "", *[f"- **{crewchat.NAMES.get(a['agent'], a['agent'])}**: {a['what']}" for a in t["affected"]], ""]
    if t.get("questions"):
        lines += ["## Please confirm", "", *[f"- {q}" for q in t["questions"]], ""]
    return "\n".join(lines) + "\n"


@handler("cto.review", resumable=False)
async def review(ctx: JobContext) -> None:
    from app.agents.cto import research_tools

    pid = ctx.project_id
    feedback = (ctx.payload.get("feedback") or "").strip()
    cr = await _cr(pid, ctx.payload.get("cr_id"), ("triage", "reviewing"))
    if not cr:
        return
    if feedback and CANCEL.search(feedback):
        await changes.update(cr.id, status="done", triage={**(cr.triage or {}), "cancelled": True})
        await crewchat.say(pid, "cto", "crew", f"{changes.label(cr)} is cancelled at the user's request: nothing changes.", "decision", cr=cr.id)
        await ctx.set_agent("cto", "done", f"{changes.label(cr)} cancelled")
        await ctx.set_project(status="waiting", last_activity=f"{changes.label(cr)} cancelled")
        return
    store = ProjectStore(pid)
    intake = await load_intake(pid)
    inv = inventory.load(pid) or {}
    touched = {e["address"] for e in ((cr.triage or {}).get("pending") or {}).get("edits") or []}
    current = [{"address": r["address"], "kind": r["kind"], "name": r["name"],
                "settings": {i["key"]: i["value"] for i in r["set"] + r["defaults"] if r["address"] in touched or r["primary"]}}
               for r in inv.get("resources", []) if r["address"] in touched or (r["primary"] and r["type"] in ("aws_lambda_function", "aws_sqs_queue"))]
    code = store.read("reports/code.json").decode() if any(f["path"] == "reports/code.json" for f in store.tree()) else ""
    src = sorted(f["path"] for f in store.tree() if f["path"].startswith(("src/", "layers/")))
    lld = store.read("03_lld.md").decode()[:15000] if any(f["path"] == "03_lld.md" for f in store.tree()) else "(none)"
    ask = (f"# The change ({changes.label(cr)}, source: {cr.source})\n{cr.text}\n"
           + (f"\n# The user's answer to your previous review\n{feedback}\n" if feedback else "")
           + f"\n# The resources it touches, as they are in AWS now\n```json\n{json.dumps(current, indent=1, default=str)[:12000]}\n```\n"
           + f"\n# Dev's code (deployed: {'yes' if (aws_access.load(pid, 'code') or {}).get('functions') else 'not yet'})\nFiles: {', '.join(src) or '(none yet)'}\n"
           + (f"{code[:3000]}\n" if code else "")
           + f"\n# The signed-off requirement\n{(intake.requirement_md or '')[:15000]}\n\n# Archie's LLD (excerpt)\n{lld}\n"
           + "\nReview the impact, then call submit_impact.")
    await ctx.set_agent("cto", "working", f"🔍 Reviewing the impact of {changes.label(cr)}")
    await ctx.set_project(status="running", last_activity=f"Orion is reviewing {changes.label(cr)}")
    trail: list[dict] = []
    try:
        res = await run_loop(project_id=pid, agent="cto",
                             system=[{"type": "text", "text": llm.prompt("cto_review.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}], tools=[*research_tools(ctx, trail, agent="cto"), SUBMIT_IMPACT],
                             max_turns=8, purpose="change-review", effort="medium")
        t = res.terminal.get("submit_impact")
        if not t:
            raise AgentError("Orion finished without a review.")
    except Exception as exc:
        await changes.update(cr.id, status="triage")
        await ctx.set_agent("cto", "failed", f"Review failed: {str(exc)[:200]}")
        await crewchat.say(pid, "cto", "crew", f"I couldn't review {changes.label(cr)}: {str(exc)[:300]}", "issue", cr=cr.id)
        raise
    triage = {**(cr.triage or {}), **t, "review": True, "research": trail, "route": "requirement" if t["verdict"] == "requirement_change" else "infra"}
    path = f"changes/{changes.label(cr)}-review.md"
    store.write(path, review_markdown(cr, t))
    high = [r for r in t["risks"] if r["severity"] == "high"]
    auto = t["verdict"] == "infra_only" and t["recommendation"] == "go" and not high and not t["questions"]
    await changes.update(cr.id, triage=triage, status="in_progress" if auto else "reviewing")
    await crewchat.say(pid, "cto", "crew", f"Review of {changes.label(cr)}: **{VERDICT[t['verdict']]}**. {t['summary']}"
                       + "".join(f"\n- ⚠️ {r['risk']} ({r['severity']})" for r in t["risks"][:5]), "decision", cr=cr.id, files=[path])
    if auto:
        await crewchat.say(pid, "cto", "tp", f"Infrastructure only and low risk: I'm giving the OK. Terra, over to you: {t['terra_brief']}", "handoff", cr=cr.id)
        await ctx.set_agent("cto", "done", f"{changes.label(cr)}: OK, infrastructure only")
        await runner_mod.runner.enqueue("cto.change_go", project_id=pid, cr_id=cr.id)
        return
    await ctx.set_agent("cto", "needs_approval", f"{changes.label(cr)}: {VERDICT[t['verdict']]}, needs your confirmation")
    what = {"requirement_change": "this changes the requirement: Echo updates it with you, then the crew re-plans and redoes the affected work",
            "affects_code": "Terra changes the infrastructure, then Dev adapts and redeploys the code, then Quinn retests",
            "infra_only": "Terra changes the infrastructure; you approve his plan"}[t["verdict"]]
    await flow.request_approval(
        pid, "change_review", f"Orion's review of {changes.label(cr)}: {VERDICT[t['verdict']]}"
        + (" (he advises against it)" if t["recommendation"] == "advise_against" else ""),
        f"{t['summary']} If you approve: {what}." + (f" Please confirm: {' '.join(t['questions'])}" if t["questions"] else "")
        + " Not wanted? Request changes and say cancel, or say what you want instead.", [path])


@handler("cto.change_go", resumable=False)
async def change_go(ctx: JobContext) -> None:
    from app.api.infra import rename

    pid = ctx.project_id
    cr = await _cr(pid, ctx.payload.get("cr_id"), ("reviewing", "in_progress"))
    if not cr:
        return
    t = cr.triage or {}
    pending = t.get("pending") or {}
    label = changes.label(cr)
    if t.get("verdict") == "requirement_change":
        await changes.update(cr.id, route="requirement")
        brief = (f"The user asked from the AWS page: {cr.text}\nOrion's review: {t.get('summary')}\n"
                 + "".join(f"- {q}\n" for q in t.get("questions") or []) + "Clarify it with the user and amend the requirement.")
        new_v = await changes.start_amendment(pid, cr, brief)
        await changes.inform_agents(pid, cr, [a["agent"] for a in t.get("affected") or [] if a["agent"] != "intake"],
                                    f"the requirement is being updated to {new_v} (from {label}). Wait for it.", version=new_v)
        await ctx.set_agent("intake", "working", f"Updating the requirement for {label} (→ {new_v})")
        await ctx.set_project(status="waiting", last_activity=f"Echo is updating the requirement for {label} ({new_v})")
        return
    await changes.update(cr.id, status="in_progress")
    if pending.get("renames"):
        await rename(pid, pending["renames"]["prefix"], pending["renames"]["names"])
    dep = aws_access.load(pid, "deploy") or {}
    dep["review"] = {"cr": label, "summary": t.get("summary"), "verdict": t.get("verdict")}
    if t.get("verdict") == "affects_code" and t.get("dev_brief"):
        dep["followups"] = {"de": f"{label}: {t['dev_brief']}"}  # after the infrastructure check, Dev adapts and redeploys
    aws_access.save(pid, dep, "deploy")
    if pending.get("plan_only") and t.get("verdict") == "infra_only":
        aws_access.save(pid, {**dep, "intent": {"reason": "change", "tickets": [], "version": cr.version_from, "changes": cr.text,
                                                "summary": label}}, "deploy")
        await crewchat.say(pid, "cto", "tp", f"Terra, {label}: the names file is updated. Plan it; the user approves before anything changes.", "handoff", cr=cr.id)
        await runner_mod.runner.enqueue("tp.deploy", project_id=pid)
        return
    await crewchat.say(pid, "cto", "tp", f"Terra, {label} is yours: {t.get('terra_brief') or cr.text}", "handoff", cr=cr.id)
    await runner_mod.runner.enqueue("tp.iac", project_id=pid, cr_id=cr.id,
                                    feedback=f"{label}: {cr.text}\n\nOrion's review: {t.get('summary', '')}\nOrion's brief for you: {t.get('terra_brief', '')}")
