"""The Testing tab (Quinn: test plan → your approval → live runs → tickets → test sign-off) and every agent's sign-off
documents."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.qa import load_live, load_plan, load_runs, plan_basis
from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import AgentState, Approval, Job, User
from app.orchestrator import crewchat
from app.orchestrator import runner as runner_mod
from app.services import aws_access, signoff
from app.services import tickets as tk
from app.services.storage import ProjectStore

router = APIRouter(prefix="/api/projects/{project_id}", tags=["testing"])
QA_JOBS = ("qa.plan", "qa.live", "qa.test")


@router.get("/testing")
async def testing(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    st = (await db.execute(select(AgentState).where(AgentState.project_id == project_id, AgentState.agent == "qa"))).scalar_one_or_none()
    plan = load_plan(project_id)
    rows = await tk.listing(project_id, with_comments=False)
    have = {f["path"] for f in ProjectStore(project_id).tree()}
    code = aws_access.load(project_id, "code") or {}
    pending = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.status == "pending",
                                                       Approval.stage.in_(["test_plan", "live", "live_bugs"])))).scalars().first()
    final = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.stage == "live", Approval.status == "approved")
                              .order_by(Approval.decided_at.desc()))).scalars().first()
    return {"state": {"status": st.status, "activity": st.activity, "started_at": st.started_at} if st else None,
            "plan": plan, "plan_current": bool(plan) and plan.get("basis") == plan_basis(project_id),
            "live": load_live(project_id), "runs": load_runs(project_id),
            "progress": aws_access.load(project_id, "qa_progress"),  # the live run so far, scenario by scenario (10-05)
            "tickets": [{k: t[k] for k in ("id", "label", "title", "severity", "area", "status", "assignee", "reporter", "check_id",
                                           "version_found", "version_fixed")} for t in rows],
            "signoff": signoff.TEST_SIGNOFF if signoff.TEST_SIGNOFF in have else None,
            "deployed": code.get("status") == "deployed", "sanity": (code.get("sanity") or {}).get("passed"),
            "pending": {"stage": pending.stage, "id": pending.id, "title": pending.title} if pending else None,
            "signed_off": {"at": final.decided_at, "comment": final.comment} if final else None}


@router.post("/testing/cycle", status_code=202)
async def new_cycle(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """A new test cycle on what is live now (e.g. a regression round): Quinn writes (or refreshes) the plan for your
    approval, then tests. Only when the code is deployed and the crew is idle."""
    await owned(project_id, user, db)
    code = aws_access.load(project_id, "code") or {}
    if code.get("status") != "deployed":
        raise HTTPException(409, "Dev's code isn't deployed yet: testing starts after his deploy and sanity check")
    busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"])))).scalars().first()
    if busy:
        raise HTTPException(409, f"The crew is busy ({busy.kind}): start a new test cycle when it's idle")
    for a in (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.status == "pending"))).scalars():
        raise HTTPException(409, f"“{a.title}” is waiting for your decision first")
    plan = load_plan(project_id)
    if plan:  # the next plan starts from this one; it needs your approval again
        aws_access.save(project_id, {**code, "live_ok": None}, "code")
    await crewchat.say(project_id, "user", "qa", "Start a new test cycle on what's live now: refresh the test plan for my approval, then test.", "assign")
    job = await runner_mod.runner.enqueue("qa.plan", project_id=project_id,
                                          feedback="The test lead started a new test cycle: review the plan against the current requirement, "
                                                   "mapping and tickets; keep what still applies, add what's missing." if plan else "")
    return {"job_id": job}


@router.get("/signoffs")
async def signoffs(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Every agent's sign-off document. Stages approved before sign-offs existed get theirs written now (from the records)."""
    await owned(project_id, user, db)
    have = {d["stage"] for d in await signoff.listing(project_id)}
    approved = {a.stage for a in (await db.execute(select(Approval).where(Approval.project_id == project_id,
                                                                         Approval.status == "approved"))).scalars()}
    if (approved & set(signoff.STAGES)) - have or ("requirement" not in have):
        await signoff.backfill(project_id)
    return await signoff.listing(project_id)
