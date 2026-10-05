"""The AWS page: what this project has in AWS (every resource and its settings, with console links), and changing many
settings at once: the edits become one infrastructure change request for Orion and Terra (plan → approve → apply →
you check it), never a direct change in AWS."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import ChangeRequest, Intake, Job, User
from app.orchestrator import changes
from app.services import aws_access, inventory

router = APIRouter(prefix="/api/projects/{project_id}/infra", tags=["infra"])


@router.get("")
async def get_infra(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    dep = aws_access.load(project_id, "deploy") or {}
    acc = aws_access.load(project_id) or {}
    crs = (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id, ChangeRequest.route == "infra")
                            .order_by(ChangeRequest.number.desc()).limit(8))).scalars().all()
    busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"]))
                             .order_by(Job.created_at.desc()))).scalars().first()
    live = dep.get("status") in ("deployed", "partial")
    inv = inventory.load(project_id)
    code = aws_access.load(project_id, "code")
    for r in (inv or {}).get("resources", []):  # what runs in each function now (Dev deploys after Terra's snapshot)
        if r["type"] == "aws_lambda_function":
            r["code"] = ((code or {}).get("functions") or {}).get(r["name"]) or {"placeholder": True}
    plan = {k: v for k, v in (dep.get("plan") or {}).items() if k != "config_keys"} or None
    from app.agents import drift
    from app.core.config import get_settings

    return {"status": dep.get("status") or "none", "applied_at": dep.get("applied_at"), "version": dep.get("version"),
            "outputs": dep.get("outputs") or {}, "error": dep.get("error"), "plan": plan, "intent": dep.get("intent"),
            "drift": drift.load(project_id), "watch_minutes": get_settings().drift_check_minutes,
            "inventory": inv, "code": code, "refreshing": bool(busy and busy.kind in ("tp.inventory", "tp.drift", "tp.restore")),
            "prefix": acc.get("prefix"), "region": aws_access.REGION, "busy": busy.kind if busy else None,
            "can_change": live and not busy, "why": None if live and not busy else
            ("Terra is still working on it" if busy else "Nothing is in AWS yet: changes go through Terra's infrastructure gate"),
            "changes": [changes.out(c) for c in crs]}


@router.post("/refresh", status_code=202)
async def refresh(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Check AWS against the source of truth (Terra's drift check: every resource and setting, every function's code;
    no change in AWS, no AI call). Something changed outside Terraform → the "drift" gate (Restore / Keep / Leave)."""
    from app.agents import drift
    from app.orchestrator import runner as runner_mod

    await owned(project_id, user, db)
    if not live(project_id):
        raise HTTPException(409, "Nothing is in AWS yet")
    busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"])))).scalars().first()
    if busy:
        raise HTTPException(409, f"Wait a moment: {busy.kind} is running")
    why = await drift._busy(project_id)
    if why:
        raise HTTPException(409, f"Not now: {why}")
    return {"job_id": await runner_mod.runner.enqueue("tp.drift", project_id=project_id)}


@router.get("/cost")
async def cost(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Per-resource cost model + AWS list prices (eu-west-1): the UI computes any volume instantly."""
    from app.services import pricing

    await owned(project_id, user, db)
    return {**pricing.model(project_id), "prices": await pricing.prices()}


class Edit(BaseModel):
    address: str = Field(max_length=300)
    key: str = Field(max_length=120)
    to: str | int | float | bool | dict | list | None = None


class Renames(BaseModel):
    prefix: str = Field(min_length=3, max_length=60)
    names: dict[str, str]


class ChangesIn(BaseModel):
    changes: list[Edit] = Field(default_factory=list, max_length=60)
    note: str = Field(default="", max_length=4000)
    renames: Renames | None = None


def live(project_id: str) -> bool:
    return (aws_access.load(project_id, "deploy") or {}).get("status") in ("deployed", "partial")


async def rename(project_id: str, prefix: str, names: dict[str, str]) -> dict:
    """Save the user's names (no AI call: names.auto.tfvars.json + the documents). Before anything exists in AWS,
    Orion's access draft follows a new prefix at once. Raises naming.NamingError."""
    from app.orchestrator import crewchat
    from app.services import naming

    out = naming.apply(project_id, prefix, names)
    if out["renamed"]:
        lines = "\n".join(f"- `{a}` → `{b}`" for a, b in out["renamed"][:20])
        await crewchat.say(project_id, "user", "crew", f"Renamed {len(out['renamed'])} resource(s):\n{lines}", "decision")
        if not live(project_id):
            current = aws_access.load(project_id)
            plan = aws_access.draft(project_id)
            if not (current and current.get("status") == "active" and aws_access.same_access(current, plan)):
                aws_access.save(project_id, plan, "access_draft")
                from app.services.storage import ProjectStore

                ProjectStore(project_id).write("reports/aws_access.md", aws_access.markdown(plan))
            await crewchat.say(project_id, "cto", "tp", "Terra, the user renamed resources before anything exists in AWS: your Terraform reads "
                               "them from infra/names.auto.tfvars.json, and my access plan follows the names.", "update")
    return out


@router.post("/changes", status_code=202)
async def request_changes(project_id: str, body: ChangesIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Settings and/or names, across services, as ONE infrastructure change request. Names are saved by the platform at
    once; settings go to Terra. Only renames → Terra just plans (no AI call)."""
    from app.services import naming

    await owned(project_id, user, db)
    inv = inventory.load(project_id)
    if not inv or not inv.get("resources") or not live(project_id):
        raise HTTPException(409, "Nothing is in AWS yet")
    busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"])))).scalars().first()
    if busy:
        raise HTTPException(409, f"Wait a moment: {busy.kind} is still running")
    intake = await db.get(Intake, project_id)
    if not intake or intake.status not in ("signed_off", "amending"):
        raise HTTPException(409, "The requirement isn't signed off yet")
    try:
        text, clean = inventory.change_text(inv, [c.model_dump() for c in body.changes], body.note, allow_empty=bool(body.renames))
        # renames are only validated here: nothing is written until Orion's review (or the user) gives the go
        renamed = naming.apply(project_id, body.renames.prefix, body.renames.names, dry_run=True)["renamed"] if body.renames else []
    except (inventory.ChangeError, naming.NamingError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if not renamed and not clean and not body.note.strip():
        raise HTTPException(422, "Nothing changed")
    rename_text = ("Rename (the platform writes infra/names.auto.tfvars.json and the documents once approved; don't change names.tf):\n"
                   + "\n".join(f"- `{a}` → **`{b}`**" for a, b in renamed)) if renamed else ""
    full = "\n\n".join(t for t in (rename_text, text) if t)
    pending = {"renames": body.renames.model_dump() if renamed else None, "plan_only": not clean and not body.note.strip(), "edits": clean}
    cr = await changes.create(project_id, full, [], source="aws-page", route="infra", pending=pending)
    return {**changes.out(cr), "edits": clean, "renamed": renamed}
