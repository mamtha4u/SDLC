from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ba import load_mapping
from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db
from app.db.models import AgentState, User

router = APIRouter(prefix="/api/projects/{project_id}/mapping", tags=["mapping"])


@router.get("")
async def get_mapping(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await owned(project_id, user, db)
    st = (await db.execute(select(AgentState).where(AgentState.project_id == project_id, AgentState.agent == "ba"))).scalar_one_or_none()
    return {"status": st.status if st else "waiting", "activity": st.activity if st else "",
            "cost_usd": st.cost_usd if st else 0, "mapping": load_mapping(project_id)}
