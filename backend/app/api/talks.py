"""Kickoff conversations (agents/talk.py): each agent's chat with the person who owns its phase."""
from __future__ import annotations

import base64
import binascii
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import talk as talk_agent
from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db, utcnow
from app.db.models import PhaseTalk, User
from app.orchestrator import runner as runner_mod
from app.orchestrator.bus import bus
from app.services.requirement_template import parse_upload
from app.services.storage import ProjectStore

router = APIRouter(prefix="/api/projects/{project_id}/talks", tags=["talks"])
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _agent(agent: str) -> str:
    if agent not in talk_agent.AGENTS:
        raise HTTPException(404, "No such conversation")
    return agent


async def _talk(project_id: str, agent: str, user: User, db: AsyncSession) -> PhaseTalk | None:
    await owned(project_id, user, db)
    return await db.get(PhaseTalk, (project_id, _agent(agent)))


async def _open(project_id: str, agent: str, user: User, db: AsyncSession) -> PhaseTalk:
    row = await _talk(project_id, agent, user, db)
    if row is None or row.status != "talking":
        raise HTTPException(409, f"{talk_agent.NAME[agent]} isn't asking anything right now")
    return row


@router.get("")
async def overview(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Every agent's talk in one call (for the tabs and the "who's waiting for you" card)."""
    await owned(project_id, user, db)
    rows = {r.agent: r for r in (await db.execute(select(PhaseTalk).where(PhaseTalk.project_id == project_id))).scalars()}
    out = {}
    for a in talk_agent.AGENTS:
        r = rows.get(a)
        last = (r.chat or [])[-1] if r and r.chat else None
        out[a] = {"status": r.status if r else "none", "busy": r.busy if r else None,
                  "waiting": bool(r and r.status == "talking" and not r.busy and last and last["role"] == "agent"),
                  "messages": len([m for m in (r.chat or []) if m["role"] in ("agent", "user")]) if r else 0,
                  "error": bool(r and r.last_error)}
    return out


@router.get("/{agent}")
async def get_talk(project_id: str, agent: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return talk_agent.state(await _talk(project_id, agent, user, db), agent)


@router.get("/{agent}/draft")
async def draft(project_id: str, agent: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Tiny payload polled while the agent writes its reply."""
    row = await _talk(project_id, agent, user, db)
    return {"busy": row.busy if row else None, "draft": row.draft if row else None, "chat_len": len(row.chat or []) if row else 0,
            "status": row.status if row else "none", "last_error": row.last_error if row else None}


class MessageIn(BaseModel):
    message: str = Field(default="", max_length=20000)
    attachments: list[str] = []


@router.post("/{agent}/message", status_code=202)
async def message(project_id: str, agent: str, body: MessageIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = await _open(project_id, agent, user, db)
    if row.busy:
        raise HTTPException(409, f"{talk_agent.NAME[agent]} is still writing. Wait a moment.")
    text = body.message.strip()
    if body.attachments:
        text = (text + "\n\n" if text else "") + "📎 Attached: " + ", ".join(body.attachments)
    if not text:
        raise HTTPException(422, "Type a message or attach a file")
    row.chat = list(row.chat or []) + [{"role": "user", "text": text, "ts": utcnow().isoformat(), "attachments": body.attachments}]
    row.busy, row.last_error = "thinking", None
    await db.commit()
    await runner_mod.runner.enqueue(f"{agent}.talk_reply", project_id=project_id)
    from app.orchestrator import crewchat

    await crewchat.say(project_id, "user", agent, text, "chat", files=body.attachments)
    return talk_agent.state(row, agent)


@router.post("/{agent}/start", status_code=202)
async def start_now(project_id: str, agent: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """"That's all, start": the agent uses its recommendations for anything still open."""
    row = await _open(project_id, agent, user, db)
    if row.busy:
        raise HTTPException(409, f"{talk_agent.NAME[agent]} is still writing. Wait a moment.")
    if not any(m["role"] == "agent" for m in row.chat or []):
        raise HTTPException(409, f"{talk_agent.NAME[agent]} hasn't asked anything yet")
    row.busy = "starting"
    await db.commit()
    await runner_mod.runner.enqueue(f"{agent}.talk_finish", project_id=project_id)
    return talk_agent.state(row, agent)


@router.post("/{agent}/retry", status_code=202)
async def retry(project_id: str, agent: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = await _open(project_id, agent, user, db)
    if not row.last_error:
        raise HTTPException(409, "Nothing to retry")
    row.last_error, row.busy = None, "thinking"
    await db.commit()
    await runner_mod.runner.enqueue(f"{agent}.talk_reply", project_id=project_id)
    return talk_agent.state(row, agent)


class UploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    data: str = Field(description="the file, base64")


@router.post("/{agent}/upload-json")
async def upload_json(project_id: str, agent: str, body: UploadIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Any kind of file, as JSON (base64): company proxies block some multipart uploads (as for Echo, 10-04)."""
    row = await _open(project_id, agent, user, db)
    try:
        data = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "The file didn't arrive intact; try again") from None
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 8 MB")
    if not data:
        raise HTTPException(422, f"{body.name} is empty")
    name = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", body.name)[:120]
    try:
        parsed = parse_upload(name, data)
    except Exception as exc:  # noqa: BLE001 - a damaged document is still kept
        parsed = {"text": f"[{name}: couldn't be read as a document ({str(exc)[:120]}); kept in inputs/{name}]"}
    ProjectStore(project_id).write(f"inputs/{name}", data)  # every agent's context reads inputs/
    text = (parsed.get("text") or "").strip() or f"[{name}: no readable text in it; kept in inputs/{name}]"
    row.uploads = [u for u in (row.uploads or []) if u["name"] != name] + [{"name": name, "kind": "document", "chars": len(text), "text": text[:120_000]}]
    await db.commit()
    await bus.publish("talk.updated", project_id=project_id, user_id=user.id, agent=agent, message=f"Uploaded {name}", data={"agent": agent})
    return {"name": name, "chars": len(text), "state": talk_agent.state(row, agent)}


@router.delete("/{agent}/uploads/{name}")
async def remove_upload(project_id: str, agent: str, name: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = await _open(project_id, agent, user, db)
    row.uploads = [u for u in (row.uploads or []) if u["name"] != name]
    await db.commit()
    return talk_agent.state(row, agent)
