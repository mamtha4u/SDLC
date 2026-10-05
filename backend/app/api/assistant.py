"""Sage, the project Q&A bubble: ask anything about this project; answers come from its real records (read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.guide import history
from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import AssistantMessage, User

router = APIRouter(prefix="/api/projects/{project_id}/assistant", tags=["assistant"])


def _out(m: AssistantMessage) -> dict:
    return {"id": m.id, "role": m.role, "text": m.text, "refs": m.refs or [], "status": m.status, "created_at": m.created_at}


@router.get("")
async def messages(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    return [_out(m) for m in await history(project_id, 200)]


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


@router.post("", status_code=202)
async def ask(project_id: str, body: AskIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.orchestrator import runner as runner_mod

    await owned(project_id, user, db)
    busy = (await db.execute(select(AssistantMessage).where(AssistantMessage.project_id == project_id,
                                                            AssistantMessage.status == "streaming"))).scalars().first()
    if busy:
        raise HTTPException(409, "Sage is still answering the last question")
    q = AssistantMessage(project_id=project_id, role="user", text=body.question.strip())
    a = AssistantMessage(project_id=project_id, role="assistant", text="", status="streaming")
    db.add_all([q, a])
    await db.commit()
    await db.refresh(a)
    await runner_mod.runner.enqueue("guide.answer", project_id=project_id, message_id=a.id)
    return [_out(m) for m in await history(project_id, 200)]


@router.delete("", status_code=204)
async def clear(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    await db.execute(delete(AssistantMessage).where(AssistantMessage.project_id == project_id))
    await db.commit()
