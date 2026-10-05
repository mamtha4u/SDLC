"""The crew room (read-only): what the agents say to each other in one project."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import User
from app.orchestrator import crewchat

router = APIRouter(prefix="/api/projects/{project_id}/crew", tags=["crew"])


@router.get("")
async def crew_room(project_id: str, after: int = Query(0, ge=0), user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    if after == 0:
        await crewchat.backfill(project_id)
    return await crewchat.history(project_id, after)
