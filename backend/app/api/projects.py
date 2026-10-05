from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import current_user
from app.db.base import get_db
from app.db.models import AgentState, Event, Project, User
from app.orchestrator import runner as runner_mod
from app.orchestrator.bus import bus, serialize
from app.orchestrator.crew import CREW
from app.services.storage import ProjectStore, StorageError

router = APIRouter(prefix="/api/projects", tags=["projects"])

ACCENTS = ["violet", "cyan", "emerald", "amber", "rose", "blue", "orange"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    budget_usd: float | None = Field(default=None, ge=1, le=1000)  # None: the user's default (Settings)


class ProjectSettingsIn(BaseModel):
    drift_watch: bool | None = None
    talks: bool | None = None  # kickoff conversations: each agent asks its own specialist before it works


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    budget_usd: float | None = Field(default=None, ge=1, le=1000)
    archived: bool | None = None
    theme: str | None = Field(default=None, description='a theme id, or "" for the account\'s theme')
    accent: str | None = None
    settings: ProjectSettingsIn | None = None


class AgentOut(BaseModel):
    key: str
    persona: str
    role: str
    accent: str
    model: str
    status: str = "waiting"
    activity: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    retries: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    progress: int | None = None  # while working: an estimate from how long this kind of job usually takes (services/progress)
    expected_s: int | None = None
    job_started_at: datetime | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    status: str
    current_agent: str | None
    current_version: str
    progress: float
    cost_usd: float
    budget_usd: float
    paused: bool
    archived: bool
    accent: str
    theme: str | None = None
    settings: dict | None = None
    last_activity: str
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    agents: list[AgentOut]
    versions: list[str]


def _out(p: Project) -> ProjectOut:
    out = ProjectOut.model_validate(p, from_attributes=True)
    if p.status == "completed":  # done is 100%, however it got there (10-05: an accepted diagram-only change left it at 50%)
        out.progress = 1.0
    return out


def _detail(p: Project) -> ProjectDetail:
    states = {s.agent: s for s in p.agent_states}
    agents = []
    for meta in CREW:
        s = states.get(meta["key"])
        extra = {k: getattr(s, k) for k in ("status", "activity", "started_at", "ended_at", "retries",
                                             "tokens_in", "tokens_out", "cost_usd")} if s else {}
        agents.append(AgentOut(**meta, **extra))
    try:
        versions = ProjectStore(p.id).versions()
    except FileNotFoundError:
        versions = [p.current_version]
    return ProjectDetail(**_out(p).model_dump(), agents=agents, versions=versions)


async def owned(project_id: str, user: User, db: AsyncSession) -> Project:
    p = await db.get(Project, project_id)
    if not p or p.owner_id != user.id:  # never reveal other users' projects exist
        raise HTTPException(404, "Project not found")
    return p


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    view: Literal["all", "running", "archived"] = "all",
    q: str = "",
    sort: Literal["updated", "created", "name", "cost"] = "updated",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Project).where(Project.owner_id == user.id, Project.archived.is_(view == "archived"))
    if view == "running":
        stmt = stmt.where(Project.status.in_(["running", "waiting"]))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Project.name.ilike(like), Project.description.ilike(like)))
    order = {"updated": Project.updated_at.desc(), "created": Project.created_at.desc(),
             "name": func.lower(Project.name), "cost": Project.cost_usd.desc()}[sort]
    return [_out(p) for p in (await db.execute(stmt.order_by(order))).scalars()]


@router.get("/stats")
async def stats(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Project.status, func.count(), func.sum(Project.cost_usd))
                             .where(Project.owner_id == user.id, Project.archived.is_(False))
                             .group_by(Project.status))).all()
    by_status = {s: c for s, c, _ in rows}
    return {
        "total": sum(by_status.values()),
        "running": by_status.get("running", 0),
        "waiting": by_status.get("waiting", 0),
        "completed": by_status.get("completed", 0),
        "failed": by_status.get("failed", 0),
        "cost_usd": round(sum((c or 0) for _, _, c in rows), 4),
    }


@router.post("", response_model=ProjectDetail, status_code=201)
async def create_project(body: ProjectCreate, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.api.auth import prefs_of

    count = (await db.execute(select(func.count()).select_from(Project).where(Project.owner_id == user.id))).scalar()
    budget = body.budget_usd if body.budget_usd is not None else float(prefs_of(user)["default_budget_usd"])
    # talks: each agent interviews its own specialist before it works (agents/talk.py); older projects keep the old flow
    p = Project(owner_id=user.id, name=body.name.strip(), description=body.description.strip(),
                budget_usd=budget, accent=ACCENTS[count % len(ACCENTS)], settings={"talks": True})
    db.add(p)
    await db.flush()
    for meta in CREW:
        db.add(AgentState(project_id=p.id, agent=meta["key"], status="waiting", activity="Waiting to start"))
    ProjectStore(p.id).create(p.name)
    await db.commit()
    await db.refresh(p)
    await bus.publish("project.created", project_id=p.id, user_id=user.id, message=f"Project “{p.name}” created")
    return _detail(p)


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.services import progress

    d = _detail(await owned(project_id, user, db))
    est = await progress.for_agents(db, project_id, [a.key for a in d.agents if a.status == "working"])
    for a in d.agents:
        if a.key in est:
            a.progress, a.expected_s, a.job_started_at = est[a.key]["progress"], est[a.key]["expected_s"], est[a.key]["job_started_at"]
    return d


@router.patch("/{project_id}", response_model=ProjectOut)
async def patch_project(project_id: str, body: ProjectPatch, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    from app.api.auth import THEMES

    p = await owned(project_id, user, db)
    changes = body.model_dump(exclude_none=True)
    if "theme" in changes:
        if changes["theme"] and changes["theme"] not in THEMES:
            raise HTTPException(422, "Unknown theme")
        changes["theme"] = changes["theme"] or None
    if "accent" in changes and changes["accent"] not in ACCENTS:
        raise HTTPException(422, f"Accent must be one of {', '.join(ACCENTS)}")
    if "settings" in changes:
        changes["settings"] = {**(p.settings or {}), **changes["settings"]}
    for k, v in changes.items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    await db.commit()
    await db.refresh(p)
    await bus.publish("project.updated", project_id=p.id, user_id=user.id, message="Project updated", data=changes)
    return _out(p)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, confirm: str = Query(..., description="must equal the project name"),
                         user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    p = await owned(project_id, user, db)
    if confirm.strip() != p.name:
        raise HTTPException(422, "Type the project name exactly to confirm deletion")
    from app.services import aws_access

    deployed = (aws_access.load(p.id, "deploy") or {}).get("status")
    if deployed in ("deployed", "partial", "destroy_failed") or (aws_access.load(p.id) or {}).get("status") == "active":
        raise HTTPException(409, "This project still has resources or roles in AWS. Tear it down first (Build tab → Tear down), "
                                 "so nothing is left behind in the shared account.")
    if runner_mod.runner:
        await runner_mod.runner.cancel_project_jobs(p.id)
    ProjectStore(p.id).delete()
    await db.delete(p)
    await db.commit()
    await bus.publish("project.deleted", user_id=user.id, message=f"Project “{p.name}” deleted", data={"id": project_id})


async def _set_paused(project_id: str, paused: bool, user: User, db: AsyncSession) -> ProjectOut:
    p = await owned(project_id, user, db)
    p.paused = paused
    if paused and p.status in ("running", "waiting"):
        p.status = "paused"
    elif not paused and p.status == "paused":
        p.status = "running"
    await db.commit()
    await db.refresh(p)
    await bus.publish("project.paused" if paused else "project.resumed", project_id=p.id, user_id=user.id,
                      message="Kill switch ON — all agents paused" if paused else "Resumed — agents continuing",
                      data={"status": p.status, "paused": paused})
    return _out(p)


@router.post("/{project_id}/pause", response_model=ProjectOut)
async def pause(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await _set_paused(project_id, True, user, db)


@router.post("/{project_id}/resume", response_model=ProjectOut)
async def resume(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await _set_paused(project_id, False, user, db)


@router.post("/{project_id}/simulate", status_code=202)
async def simulate(project_id: str, speed: float = 1.0, user: User = Depends(current_user),
                   db: AsyncSession = Depends(get_db)):
    p = await owned(project_id, user, db)
    if p.status in ("running", "waiting"):
        raise HTTPException(409, "A run is already in progress")
    from app.db.models import Approval

    if (await db.execute(select(Approval).where(Approval.project_id == p.id))).scalars().first():
        # the scripted demo rewrites every agent card ("… finished", "Simulation complete"): never over real work (10-02)
        raise HTTPException(409, "This project has real work: a demo run would overwrite its agent cards. Try it on a new, empty project.")
    job_id = await runner_mod.runner.enqueue("demo.simulation", project_id=p.id, speed=max(0.25, min(speed, 4)))
    return {"job_id": job_id}


@router.get("/{project_id}/events")
async def history(project_id: str, limit: int = Query(100, le=500), user: User = Depends(current_user),
                  db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    rows = (await db.execute(select(Event).where(Event.project_id == project_id)
                             .order_by(Event.id.desc()).limit(limit))).scalars().all()
    return [serialize(e) for e in reversed(rows)]


@router.get("/{project_id}/files")
async def files(project_id: str, version: str | None = None, user: User = Depends(current_user),
                db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    store = ProjectStore(project_id)
    return {"versions": store.versions(), "current": store.manifest()["current_version"], "files": store.tree(version)}


TEXT_EXT = {".md", ".txt", ".json", ".xml", ".yaml", ".yml", ".py", ".tf", ".hcl", ".csv", ".drawio", ".xsd", ".sql", ".sh", ".log"}


@router.get("/{project_id}/files/zip")
async def files_zip(project_id: str, paths: str = Query(..., description="comma-separated paths or folder prefixes ending in /"),
                    name: str = "files", version: str | None = None, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Several project files as one zip (a Code-tab section's "Download all"), with their project paths inside."""
    import io
    import re
    import zipfile

    p = await owned(project_id, user, db)
    store = ProjectStore(project_id)
    want = [x.strip() for x in paths.split(",") if x.strip()]
    picked = [f["path"] for f in store.tree(version) if any(f["path"] == w or (w.endswith("/") and f["path"].startswith(w)) for w in want)]
    if not picked:
        raise HTTPException(404, "Nothing to download")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in picked[:2000]:
            z.writestr(path, store.read(path, version))
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{p.name}-{name}").strip("-")[:80] or "files"
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{safe}.zip"'})


@router.get("/{project_id}/files/content")
async def file_content(project_id: str, path: str, version: str | None = None, download: bool = False,
                       user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """A project file (any version). Text files come back as text for the in-app viewer; ?download=1 saves it."""
    await owned(project_id, user, db)
    try:
        data = ProjectStore(project_id).read(path, version)
    except StorageError as exc:
        raise HTTPException(404, str(exc)) from exc
    name = path.rsplit("/", 1)[-1]
    is_text = any(name.lower().endswith(e) for e in TEXT_EXT)
    headers = {"Content-Disposition": f'attachment; filename="{name}"'} if download else {}
    return Response(data, media_type="text/plain; charset=utf-8" if is_text else "application/octet-stream", headers=headers)