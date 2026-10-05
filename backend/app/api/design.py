"""Archie's design: HLD, LLD, the diagram (.drawio) and the edit-in-draw.io round trip."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ta import DRAWIO, USER_DRAWIO, load_design
from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db, utcnow
from app.db.models import AgentState, Approval, User
from app.orchestrator import crewchat
from app.services import diagram
from app.services.storage import ProjectStore, StorageError

router = APIRouter(prefix="/api/projects/{project_id}", tags=["design"])


@router.get("/design")
async def get_design(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    st = (await db.execute(select(AgentState).where(AgentState.project_id == project_id, AgentState.agent == "ta"))).scalar_one_or_none()
    d = load_design(project_id)
    xml = None
    if d:
        try:
            xml = ProjectStore(project_id).read(DRAWIO).decode()
        except StorageError:
            xml = None
    return {"status": st.status if st else "waiting", "activity": st.activity if st else "", "cost_usd": st.cost_usd if st else 0,
            "started_at": st.started_at if st else None, "design": d, "drawio": xml,
            "editor_url": diagram.editor_url(xml) if xml else None}


@router.get("/design/stack")
async def tech_stack(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The tech stack read from the project's files: language, Lambda runtime, packages, test tools, AWS services,
    Terraform and its providers, quality gates. No AI call; each item names its source file."""
    from app.services.stack import stack

    await owned(project_id, user, db)
    return stack(project_id)


@router.post("/design/diagram")
async def upload_diagram(project_id: str, file: UploadFile = File(...), user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db)):
    """You edited the diagram in draw.io: see what changed before sending it to Archie."""
    await owned(project_id, user, db)
    data = await file.read()
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(413, "Diagram is larger than 4 MB")
    store = ProjectStore(project_id)
    try:
        current = store.read(DRAWIO).decode()
    except StorageError as exc:
        raise HTTPException(409, "There's no diagram yet to compare with") from exc
    text = data.decode("utf-8", errors="replace")
    try:
        d = diagram.diff(current, text)
    except (ValueError, Exception) as exc:  # noqa: BLE001 — unreadable upload
        raise HTTPException(422, f"That doesn't look like a draw.io diagram (.drawio / .xml): {str(exc)[:200]}") from exc
    store.write(USER_DRAWIO, text)
    return {"diff": d, "summary": diagram.diff_summary(d)}


class GatesIn(BaseModel):
    min_coverage_percent: float = Field(ge=0, le=100)


@router.put("/design/gates")
async def set_gates(project_id: str, body: GatesIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Change Archie's quality gate yourself: no AI call, applies to Dev's next submission."""
    import json

    await owned(project_id, user, db)
    d = load_design(project_id)
    if not d:
        raise HTTPException(409, "There's no design yet")
    from app.agents.ta import sync_coverage

    old = (d.get("quality_gates") or {}).get("min_coverage_percent", 70)
    pct = body.min_coverage_percent
    d["quality_gates"] = {**(d.get("quality_gates") or {"rules": []}), "min_coverage_percent": pct, "set_by": "user"}
    store = ProjectStore(project_id)
    # the documents follow the number: the LLD/HLD said "Minimum line coverage is 70%" after the user had set 80%
    d["lld_markdown"], d["hld_markdown"] = sync_coverage(d.get("lld_markdown", ""), pct), sync_coverage(d.get("hld_markdown", ""), pct)
    for path, text in (("03_lld.md", d["lld_markdown"]), ("02_hld.md", d["hld_markdown"])):
        if text:
            store.write(path, text)
    store.write("diagrams/design.json", json.dumps(d, indent=2, ensure_ascii=False))
    store.append_changelog(store.manifest()["current_version"], [f"Coverage gate set to {pct:g}% by the user (was {old:g}%)"])
    await crewchat.say(project_id, "user", "de", f"Coverage gate changed from {old:g}% to {pct:g}%. "
                       "It applies to Dev's next submission; the HLD and LLD now say the same.", "decision")
    return d["quality_gates"]


@router.get("/naming")
async def get_naming(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.services import naming

    await owned(project_id, user, db)
    return naming.state(project_id)


class NamingIn(BaseModel):
    prefix: str = Field(min_length=3, max_length=60)
    names: dict[str, str]


@router.put("/naming")
async def set_naming(project_id: str, body: NamingIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The user renames resources before they exist in AWS (no AI call). Live resources are renamed from the AWS page
    as an infrastructure change (Terra plans the replacement, the user approves)."""
    from app.api.infra import live, rename
    from app.services import naming

    await owned(project_id, user, db)
    if live(project_id):
        raise HTTPException(409, "These resources are live in AWS: rename them on the AWS tab (Change settings → the pencil next to a "
                                 "name), so Terra can plan the replacement for your approval.")
    try:
        return await rename(project_id, body.prefix, body.names)
    except naming.NamingError as exc:
        raise HTTPException(422, str(exc)) from exc


class ConventionIn(BaseModel):
    prefix: str = Field(min_length=10, max_length=60)
    pattern: str = Field(default="", max_length=300)


@router.put("/naming/convention")
async def set_convention(project_id: str, body: ConventionIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The user's naming convention before Terra writes the infrastructure (no AI call): Archie and Terra build with it.
    Afterwards the names themselves are edited (free before the deploy, a plan after it)."""
    from app.orchestrator import crewchat
    from app.services import naming

    await owned(project_id, user, db)
    st = naming.state(project_id)
    if st["editable"]:
        raise HTTPException(409, "Terra has written the infrastructure: rename the prefix and each name directly on the AWS tab.")
    try:
        c = naming.set_convention(project_id, body.prefix, body.pattern, by=user.username)
    except naming.NamingError as exc:
        raise HTTPException(422, str(exc)) from exc
    await crewchat.say(project_id, "user", "crew", f"Naming convention: every name starts with `{c['prefix']}`"
                       + (f", then {c['pattern']}" if c["pattern"] else "") + ". Archie and Terra, build with it.", "decision")
    return naming.state(project_id)


class ApplyIn(BaseModel):
    note: str = ""


@router.post("/design/diagram/apply", status_code=202)
async def apply_diagram(project_id: str, body: ApplyIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Use your edited drawing: it stays exactly as you drew it; Archie updates the HLD/LLD to match."""
    from app.orchestrator import runner as runner_mod

    await owned(project_id, user, db)
    store = ProjectStore(project_id)
    try:
        uploaded, current = store.read(USER_DRAWIO).decode(), store.read(DRAWIO).decode()
    except StorageError as exc:
        raise HTTPException(409, "Upload your edited diagram first") from exc
    summary = diagram.diff_summary(diagram.diff(current, uploaded))
    pending = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.stage == "design",
                                                       Approval.status == "pending"))).scalars().first()
    if pending:
        pending.status, pending.comment, pending.decided_at = "changes_requested", f"Edited diagram: {summary}", utcnow()
        await db.commit()
    feedback = summary + (f"\nThe user's note: {body.note.strip()}" if body.note.strip() else "")
    await crewchat.say(project_id, "user", "ta", f"I edited the diagram in draw.io. {feedback}", "decision", files=[USER_DRAWIO])
    await runner_mod.runner.enqueue("ta.design", project_id=project_id, feedback=feedback, user_diagram=True)
    return {"queued": True, "summary": summary}
