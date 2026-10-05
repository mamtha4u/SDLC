"""The Tickets page: the crew's bugs and tasks, with their history. People can open tickets, comment, and assign them to
any agent (Dev or Terra fix; Quinn retests; Atlas, Archie, Echo and Orion get it as a change request)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import Approval, Intake, Job, User
from app.orchestrator import changes, crewchat
from app.orchestrator import runner as runner_mod
from app.services import tickets as tk

router = APIRouter(prefix="/api/projects/{project_id}/tickets", tags=["tickets"])
Agent = Literal["cto", "ba", "ta", "tp", "de", "qa", "intake", "user"]
WORKERS = ("tp", "de", "qa")          # take tickets directly
VIA_CR = ("cto", "ba", "ta", "intake")  # their work changes through a change request


class TicketIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=8000)
    steps: str = Field(default="", max_length=6000)
    expected: str = Field(default="", max_length=4000)
    actual: str = Field(default="", max_length=4000)
    severity: Literal["critical", "major", "minor"] = "major"
    area: Literal["code", "infra", "design", "other"] = "code"
    assignee: Agent = "de"


class TicketPatch(BaseModel):
    status: Literal["open", "in_progress", "resolved", "closed", "reopened"] | None = None
    assignee: Agent | None = None
    severity: Literal["critical", "major", "minor"] | None = None
    note: str = Field(default="", max_length=4000)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=6000)


@router.get("")
async def list_tickets(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    rows = await tk.listing(project_id)
    counts = {s: sum(t["status"] == s for t in rows) for s in tk.STATUSES}
    return {"tickets": rows, "counts": counts, "active": sum(t["status"] in tk.ACTIVE for t in rows)}


@router.get("/{ticket_id}")
async def get_ticket(project_id: str, ticket_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    t = await tk.detail(ticket_id)
    if not t or (await tk.get(ticket_id)).project_id != project_id:
        raise HTTPException(404, "Ticket not found")
    return t


@router.post("", status_code=201)
async def create_ticket(project_id: str, body: TicketIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    t = await tk.create(project_id, title=body.title, description=body.description, steps=body.steps, expected=body.expected,
                        actual=body.actual, severity=body.severity, area=body.area, assignee=body.assignee, reporter="user")
    note = await _route(project_id, t, db)
    return {**(await tk.detail(t.id)), "routed": note}


@router.patch("/{ticket_id}")
async def update_ticket(project_id: str, ticket_id: str, body: TicketPatch, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    t = await tk.get(ticket_id)
    if not t or t.project_id != project_id:
        raise HTTPException(404, "Ticket not found")
    reassigned = body.assignee and body.assignee != t.assignee
    status = body.status
    if reassigned and not status and t.status in ("resolved", "closed"):
        status = "reopened"  # handing a finished ticket to someone means there's work again
    try:
        t = await tk.change(ticket_id, "user", body.note.strip(), status=status, assignee=body.assignee, severity=body.severity)
    except tk.TicketError as exc:
        raise HTTPException(422, str(exc)) from exc
    note = await _route(project_id, t, db) if reassigned or (status in ("open", "reopened")) else None
    return {**(await tk.detail(t.id)), "routed": note}


@router.post("/{ticket_id}/comments", status_code=201)
async def add_comment(project_id: str, ticket_id: str, body: CommentIn, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    t = await tk.get(ticket_id)
    if not t or t.project_id != project_id:
        raise HTTPException(404, "Ticket not found")
    await tk.comment(ticket_id, "user", body.text)
    return await tk.detail(ticket_id)


async def _route(project_id: str, t, db: AsyncSession) -> str:
    """Hand a person's ticket to its assignee. Dev / Terra / Quinn: Orion dispatches now if the crew is free, else at
    the next hand-off. Atlas / Archie / Echo / Orion: it becomes a change request (their work changes through one)."""
    if t.status not in tk.ACTIVE and t.status != "resolved":
        return "closed"
    if t.assignee in VIA_CR:
        intake = await db.get(Intake, project_id)
        if not intake or intake.status != "signed_off" or intake.busy:
            await tk.comment(t.id, "cto", "I'll turn this into a change request once the requirement is signed off and Echo is free.")
            return "waiting"
        text = f"{tk.label(t)}: {t.title}" + (f"\n\n{t.description}" if t.description else "") + \
            (f"\n\nExpected: {t.expected}" if t.expected else "") + (f"\nActual: {t.actual}" if t.actual else "")
        cr = await changes.create(project_id, text, [], source=f"ticket:{tk.label(t)}")
        await tk.change(t.id, "cto", f"Turned into {changes.label(cr)}: I'm triaging it (it changes {crewchat.NAMES.get(t.assignee, t.assignee)}'s work).",
                        status="in_progress")
        return f"change request {changes.label(cr)}"
    if t.assignee not in WORKERS:
        return "with you"
    busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"])))).scalars().first()
    pending = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.status == "pending"))).scalars().all()
    if pending and all(a.stage == "live" for a in pending):  # the flow looked done, but there's a new problem: not done yet
        for a in pending:
            a.status = "superseded"
        await db.commit()
        pending = []
    if busy or pending:
        await tk.comment(t.id, "cto", "Queued: I'll hand it over at the next step, when the crew is free"
                         + (f" (waiting for: {pending[0].title})" if pending else "") + ".")
        return "queued"
    await runner_mod.runner.enqueue("cto.tickets", project_id=project_id)
    return "dispatched"
