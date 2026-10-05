from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import Approval, Intake, User
from app.orchestrator import flow

router = APIRouter(prefix="/api/projects/{project_id}/approvals", tags=["approvals"])


def _out(a: Approval, plan: dict | None = None) -> dict:
    s = flow.plan_route(a.project_id, plan)[0] if a.stage == "plan" and plan else flow.STAGES.get(a.stage, {})
    return {"id": a.id, "stage": a.stage, "agent": a.agent, "title": a.title, "summary": a.summary,
            "artifacts": a.artifacts or [], "status": a.status, "comment": a.comment,
            "created_at": a.created_at, "decided_at": a.decided_at, "next_label": s.get("next_label", ""),
            "next_ready": bool(s.get("next")), "next_agent": s.get("next_agent"),
            "can_accept": a.status == "pending" and flow.accept_allowed(a.project_id, a.stage)}


@router.get("")
async def list_approvals(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    rows = (await db.execute(select(Approval).where(Approval.project_id == project_id)
                             .order_by(Approval.created_at.desc()))).scalars().all()
    if not rows:  # projects planned before approvals existed: open the plan gate now
        intake = await db.get(Intake, project_id)
        if intake and intake.plan:
            await flow.request_approval(project_id, "plan", "Orion's delivery plan", intake.plan.get("summary", ""), ["plan.md"])
            rows = (await db.execute(select(Approval).where(Approval.project_id == project_id))).scalars().all()
    intake = await db.get(Intake, project_id)
    plan = intake.plan if intake else None
    return [_out(a, plan) for a in rows if a.status != "superseded"]


@router.post("/continue", status_code=202)
async def continue_pipeline(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Start the agent a project paused for (it was approved before that agent existed)."""
    await owned(project_id, user, db)
    kind = await flow.continue_pipeline(project_id)
    if not kind:
        raise HTTPException(409, "Nothing is waiting to start")
    return {"started": kind}


class DecisionIn(BaseModel):
    decision: Literal["approve", "changes", "accept"]  # accept: approve it, but nothing downstream is redone
    comment: str = Field(default="", max_length=4000)


@router.post("/{approval_id}")
async def decide(project_id: str, approval_id: str, body: DecisionIn, user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    if body.decision == "changes" and not body.comment.strip():
        raise HTTPException(422, "Tell the agent what to change")
    try:
        return _out(await flow.decide(project_id, approval_id, body.decision, body.comment.strip()))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
