"""The Build tab (Terra's infrastructure, Dev's code, Quinn's QA, the AWS deployment) and each agent's AWS access."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.de import load_code
from app.agents.qa import load_live, load_qa
from app.agents.ta import quality_gates
from app.agents.tp import load_preview
from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import AgentState, Job, User
from app.orchestrator import runner as runner_mod
from app.services import aws_access

router = APIRouter(prefix="/api/projects/{project_id}", tags=["build"])
LIVE_DEPLOY = ("deployed", "partial", "destroy_failed")  # something may exist in AWS


@router.get("/build")
async def build(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    states = {s.agent: {"status": s.status, "activity": s.activity, "started_at": s.started_at, "cost_usd": s.cost_usd}
              for s in (await db.execute(select(AgentState).where(AgentState.project_id == project_id,
                                                                  AgentState.agent.in_(["cto", "ta", "tp", "de", "qa"])))).scalars()}
    qa = load_qa(project_id)
    if qa:
        qa = {k: v for k, v in qa.items() if k != "tests"} | {"counts": {k: qa["tests"][k] for k in ("total", "passed", "failed", "error")}}
    access = aws_access.load(project_id)
    dep = aws_access.load(project_id, "deploy")
    if dep and dep.get("plan"):
        dep = {**dep, "plan": {k: v for k, v in dep["plan"].items() if k != "config_keys"}}
    from app.services import tickets as tk

    rows = await tk.listing(project_id, with_comments=False)
    return {"infra": {"state": states.get("tp"), "preview": load_preview(project_id)},
            "code": {"state": states.get("de"), "data": load_code(project_id), "gates": quality_gates(project_id), "reviewer": states.get("ta"),
                     "review": _review_brief(project_id)},
            "qa": {"state": states.get("qa"), "data": qa},
            "deploy": {"orion": states.get("cto"), "access": _brief(access), "draft": _brief(aws_access.load(project_id, "access_draft")),
                       "state": dep, "live": load_live(project_id), "code": aws_access.load(project_id, "code")},
            "handover": _handover(project_id, dep),
            "tickets": {"active": sum(t["status"] in tk.ACTIVE for t in rows), "resolved": sum(t["status"] == "resolved" for t in rows),
                        "closed": sum(t["status"] == "closed" for t in rows), "total": len(rows)}}


def _handover(project_id: str, dep: dict | None) -> dict:
    """Dev → Archie → Terra → AWS (user, 10-03: "we need to see the movement: Dev giving the codebase and layers to Terra,
    Terra removing the fake code and deploying the real code"). mode: "terra" (Terra deploys everything), "layers" (10-02:
    Terra publishes layers, Dev uploads code) or "dev" (older projects). Each package: live / with_terra."""
    from app.agents.buildkit import files_under
    from app.agents.de import load_packages
    from app.services.storage import ProjectStore
    from app.tools import terraform

    infra = files_under(ProjectStore(project_id), ("infra/",))
    mode = "terra" if terraform.manages_code(infra) else "layers" if terraform.manages_layers(infra) else "dev"
    pk = load_packages(project_id)
    keep = ("function_name", "source_dir", "name", "kb", "version", "handed_at", "applied_version", "applied_at", "adopted", "uri")
    out = {kind: [{"key": k, **{f: r.get(f) for f in keep if r.get(f) is not None},
                   "status": "live" if r.get("applied") and r.get("applied") == r.get("fingerprint") else "with_terra"}
                  for k, r in sorted(pk[kind].items())] for kind in ("code", "layers", "images")}
    intent = (dep or {}).get("intent") or {}
    plan = (dep or {}).get("plan") or {}
    return {"mode": mode, **out, "pending": intent.get("reason") in ("packages", "layers"),
            "planned": plan.get("reason") in ("packages", "layers") and intent.get("reason") in ("packages", "layers"),
            "plan": {k: plan.get(k) for k in ("counts", "planned_at", "version")} if plan.get("reason") in ("packages", "layers") else None}


def _review_brief(project_id: str) -> dict | None:
    from app.agents.codereview import load_review

    r = load_review(project_id)
    return {k: r.get(k) for k in ("verdict", "round", "version", "at")} | {"must": sum(f["severity"] == "must" for f in r.get("findings", []))} if r else None


def _brief(access: dict | None) -> dict | None:
    """The access plan without the policy documents (those are in /access, shown in each agent's tab)."""
    if not access:
        return None
    return {**{k: v for k, v in access.items() if k != "roles"},
            "roles": [{k: v for k, v in r.items() if k != "policy"} for r in access.get("roles", [])]}


@router.get("/access")
async def access(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Who may do what in AWS: the platform role (Orion), the crew boundary, each agent's project role, and the audit trail."""
    await owned(project_id, user, db)
    pol = aws_access.platform_policies()
    return {"platform": {"role": aws_access.PLATFORM_ROLE, "arn": aws_access.PLATFORM_ROLE_ARN, "policy": pol["fence"],
                         "can": ["Call Claude on Amazon Bedrock (the crew's brains)",
                                 "Create, change and delete only orkestra-* resources in eu-west-1",
                                 "Create the crew's roles, always with the crew boundary, and act as them",
                                 "Read (list/describe) what exists, to check names and plans"],
                         "cannot": ["Change or delete anything that isn't named orkestra-* (colleagues' resources)",
                                    "Create a role without the crew boundary", "Change its own permissions or the boundary",
                                    "Work outside eu-west-1 (except Claude, which AWS routes across EU regions)"]},
            "boundary": {"arn": aws_access.BOUNDARY_ARN, "policy": pol["boundary"]},
            "plan": aws_access.load(project_id), "deploy": aws_access.load(project_id, "deploy"),
            "calls": aws_access.calls(project_id), "extras": aws_access.extras(project_id)}


@router.get("/code-review")
async def code_review(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Archie's review of the current code, your conversation with him about it, and whether the gate waits for you."""
    from app.agents.codereview import STATE, load_review
    from app.db.models import Approval

    await owned(project_id, user, db)
    st = aws_access.load(project_id, STATE) or {}
    ta = (await db.execute(select(AgentState).where(AgentState.project_id == project_id, AgentState.agent == "ta"))).scalar_one_or_none()
    pending = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.stage == "code_review",
                                                       Approval.status == "pending"))).scalars().first()
    busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.kind.in_(["ta.code_review", "ta.review_chat"]),
                                               Job.status.in_(["queued", "running"])))).scalars().first()
    return {"review": load_review(project_id), "chat": st.get("chat") or [], "round": st.get("round"), "version": st.get("version"),
            "pending": {"id": pending.id, "title": pending.title} if pending else None, "busy": busy.kind if busy else None,
            "state": {"status": ta.status, "activity": ta.activity, "started_at": ta.started_at} if ta else None}


@router.post("/code-review/start", status_code=202)
async def start_code_review(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """You ask Archie to review the code as it is now (user, 10-02: "the user will ask the TA to completely review the
    codebase"). A review only: no gate, nothing is redeployed; changes go to Dev as a ticket."""
    from app.orchestrator import crewchat

    await owned(project_id, user, db)
    if not load_code(project_id):
        raise HTTPException(409, "There's no code to review yet")
    busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"])))).scalars().first()
    if busy:
        raise HTTPException(409, f"The crew is busy ({busy.kind}): ask again when it's idle")
    await crewchat.say(project_id, "user", "ta", "Archie, please review the current code completely and tell me what you think.", "question")
    job = await runner_mod.runner.enqueue("ta.code_review", project_id=project_id, on_demand=True)
    return {"job_id": job}


class AskIn(BaseModel):
    question: str


@router.post("/code-review/ask", status_code=202)
async def ask_archie(project_id: str, body: AskIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Ask Archie anything about the code under review: a deeper security review, classes or functions, comments or docstrings…"""
    from app.agents.base import AgentError
    from app.agents.codereview import ask

    await owned(project_id, user, db)
    if not body.question.strip():
        raise HTTPException(422, "Ask Archie something")
    try:
        st = await ask(project_id, body.question, user.display_name or user.username)
    except AgentError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"chat": st["chat"]}


class ExtraIn(BaseModel):
    statements: list
    reason: str = ""
    apply: bool = False


@router.put("/access/{agent}/extra")
async def policy_change(project_id: str, agent: str, body: ExtraIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Your change to an agent's AWS policy (Terra, Dev or Quinn). Orion checks it against the project's rules first
    (apply=false: just the check and the resulting policy); apply=true writes it to the role. Orion's own platform role
    can't be changed from here."""
    import asyncio

    from app.orchestrator import crewchat

    await owned(project_id, user, db)
    if agent == "cto":
        raise HTTPException(403, "Orion's access is the platform role: it always keeps the higher permission and can't be changed here")
    if agent not in aws_access.PERSONA:
        raise HTTPException(404, "Only Terra, Dev and Quinn have AWS roles")
    stmts, problems = aws_access.check_extra(project_id, agent, body.statements)
    if problems:
        raise HTTPException(422, {"problems": problems})
    plan = aws_access.load(project_id) or {}
    role = next(r for r in plan["roles"] if r["agent"] == agent)
    preview = {**role["policy"], "Statement": [*role["policy"]["Statement"], *stmts]}
    if not body.apply:
        return {"ok": True, "statements": stmts, "policy": preview}
    if not body.reason.strip():
        raise HTTPException(422, {"problems": ["Say why you're changing it: it goes in the audit trail and the crew room"]})
    entry = await asyncio.to_thread(aws_access.apply_extra, project_id, agent, stmts, body.reason.strip(), user.display_name or user.username)
    name = aws_access.PERSONA[agent].title()
    await crewchat.say(project_id, "cto", agent, f"The user changed {name}'s AWS access ({len(stmts)} statement(s) of their own on top of "
                       f"mine): “{body.reason.strip()}”. Checked against the project rules and the crew boundary, and applied.", "decision")
    return {"ok": True, "statements": stmts, "policy": preview, "entry": entry}


class TeardownIn(BaseModel):
    confirm: str


@router.post("/deploy/teardown", status_code=202)
async def teardown(project_id: str, body: TeardownIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    p = await owned(project_id, user, db)
    if body.confirm.strip() != p.name:
        raise HTTPException(422, "Type the project name exactly to confirm the tear down")
    state = aws_access.load(project_id, "deploy") or {}
    acc = aws_access.load(project_id) or {}
    if state.get("status") not in (*LIVE_DEPLOY, "planned") and acc.get("status") != "active":
        raise HTTPException(409, "Nothing of this project is in AWS")
    busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"]),
                                               Job.kind.in_(["tp.deploy", "tp.apply", "tp.destroy", "qa.live", "cto.grant", "de.deploy"])))).scalars().first()
    if busy:
        raise HTTPException(409, f"Wait for {busy.kind} to finish first")
    from app.db.models import Approval

    for a in (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.status == "pending",
                                                      Approval.stage.in_(["deploy", "infra_check", "code", "live", "live_bugs"])))).scalars():
        a.status = "superseded"
    await db.commit()
    job = await runner_mod.runner.enqueue("tp.destroy", project_id=project_id)
    return {"job_id": job}
