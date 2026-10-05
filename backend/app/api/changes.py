"""Change requests: raise one (text + files), list them. Orion triages and routes each one."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db, utcnow
from app.db.models import Approval, ChangeRequest, Intake, User
from app.orchestrator import changes
from app.services.requirement_template import parse_upload
from app.services.storage import ProjectStore

router = APIRouter(prefix="/api/projects/{project_id}/changes", tags=["changes"])
MAX_BYTES = 8 * 1024 * 1024


@router.get("")
async def list_changes(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    rows = (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id)
                             .order_by(ChangeRequest.number.desc()))).scalars().all()
    return [changes.out(c) for c in rows]


@router.post("", status_code=202)
async def raise_change(project_id: str, text: str = Form(...), approval_id: str | None = Form(None),
                       files: list[UploadFile] = File(default=[]), user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    return await _raise(project_id, text, approval_id, [(f.filename or "attachment", await f.read()) for f in files], user, db)


class FileIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    data: str  # base64


class ChangeIn(BaseModel):
    text: str
    approval_id: str | None = None
    files: list[FileIn] = []


@router.post("/json", status_code=202)
async def raise_change_json(project_id: str, body: ChangeIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The same as JSON with base64 files: what the UI sends, because company proxies block some file types in multipart
    uploads (user, 10-04)."""
    import base64
    import binascii

    try:
        files = [(f.name, base64.b64decode(f.data, validate=True)) for f in body.files]
    except (binascii.Error, ValueError):
        raise HTTPException(422, "An attachment didn't arrive intact; try again") from None
    return await _raise(project_id, body.text, body.approval_id, files, user, db)


async def _raise(project_id: str, text: str, approval_id: str | None, files: list[tuple[str, bytes]], user: User,
                 db: AsyncSession) -> dict:
    await owned(project_id, user, db)
    text = text.strip()
    if not text:
        raise HTTPException(422, "Describe the change")
    intake = await db.get(Intake, project_id)
    if not intake or intake.status not in ("signed_off", "amending"):
        raise HTTPException(409, "The requirement isn't signed off yet. Just tell Echo in the chat.")
    if intake.status == "amending":
        raise HTTPException(409, "Echo is already updating the requirement for another change. Add this in Echo's chat.")
    if intake.busy:
        raise HTTPException(409, "Echo is busy. Try again in a moment.")
    open_cr = (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id,
                                                            ChangeRequest.status == "triage"))).scalar_one_or_none()
    if open_cr:
        raise HTTPException(409, f"Orion is still triaging {changes.label(open_cr)}. Wait a moment.")

    # attachments → stored unchanged in inputs/ and readable by the agents
    names: list[str] = []
    store = ProjectStore(project_id)
    uploads = list(intake.uploads or [])
    for filename, data in files:
        if not data:
            continue
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"{filename} is larger than 8 MB")
        name = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", filename)[:120]
        store.write(f"inputs/{name}", data)
        try:
            parsed = parse_upload(name, data)
        except Exception as exc:  # noqa: BLE001 - kept as a file even if it can't be read
            parsed = {"text": f"[{name}: couldn't be read as a document ({str(exc)[:120]}); kept in inputs/{name}]"}
        content = parsed.get("text") or "\n".join(f"{k}: {v}" for k, v in parsed.get("answers", {}).items())
        uploads = [u for u in uploads if u["name"] != name] + [{"name": name, "kind": "attachment", "chars": len(content), "text": content}]
        names.append(name)
    intake.uploads = uploads

    source, route = "user", None
    if approval_id:
        a = await db.get(Approval, approval_id)
        if a and a.project_id == project_id and a.status == "pending":
            a.status, a.comment, a.decided_at = "changes_requested", text, utcnow()
            source = f"gate:{a.stage}"
            if a.stage in ("infra", "infra_check", "deploy"):  # infrastructure: straight to Orion and Terra
                route = "infra"
    await db.commit()
    cr = await changes.create(project_id, text, names, source, route=route)
    return changes.out(cr)
