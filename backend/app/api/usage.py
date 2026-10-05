"""Token & cost transparency: per-project usage and the user's overall usage, from the llm_calls ledger."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import _config
from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db, utcnow
from app.db.models import LlmCall, Project, User

router = APIRouter(tags=["usage"])

SUMS = (
    func.count(LlmCall.id).label("calls"),
    func.coalesce(func.sum(LlmCall.input_tokens), 0).label("input_tokens"),
    func.coalesce(func.sum(LlmCall.output_tokens), 0).label("output_tokens"),
    func.coalesce(func.sum(LlmCall.cache_read_tokens), 0).label("cache_read_tokens"),
    func.coalesce(func.sum(LlmCall.cache_write_tokens), 0).label("cache_write_tokens"),
    func.coalesce(func.sum(LlmCall.cost_usd), 0.0).label("cost_usd"),
)


def _row(r) -> dict:
    d = dict(r._mapping)
    d["cost_usd"] = round(float(d["cost_usd"]), 6)
    d["total_tokens"] = d["input_tokens"] + d["output_tokens"] + d["cache_read_tokens"] + d["cache_write_tokens"]
    return d


async def _grouped(db: AsyncSession, where, *cols) -> list[dict]:
    rows = (await db.execute(select(*cols, *SUMS).where(*where).group_by(*cols).order_by(func.sum(LlmCall.cost_usd).desc()))).all()
    return [_row(r) for r in rows]


def _models() -> dict:
    return {k: {"id": v["id"], "label": v["label"], "price": v["price"]} for k, v in _config()["models"].items()}


@router.get("/api/projects/{project_id}/usage")
async def project_usage(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    p = await owned(project_id, user, db)
    where = [LlmCall.project_id == project_id]
    totals = _row((await db.execute(select(*SUMS).where(*where))).one())
    calls = (await db.execute(select(LlmCall).where(*where).order_by(LlmCall.id.desc()).limit(300))).scalars().all()
    return {
        "budget_usd": p.budget_usd, "totals": totals, "models": _models(),
        "by_agent": await _grouped(db, where, LlmCall.agent),
        "by_model": await _grouped(db, where, LlmCall.model_key),
        "calls": [{"id": c.id, "at": c.created_at, "agent": c.agent, "model_key": c.model_key, "purpose": c.purpose,
                   "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
                   "cache_read_tokens": c.cache_read_tokens, "cache_write_tokens": c.cache_write_tokens,
                   "cost_usd": c.cost_usd, "duration_ms": c.duration_ms, "price": c.price} for c in calls],
    }


@router.get("/api/usage")
async def overall_usage(days: int = Query(30, ge=1, le=365), user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    where = [LlmCall.owner_id == user.id, LlmCall.created_at >= since]
    by_project = await _grouped(db, where, LlmCall.project_id)
    names = dict((await db.execute(select(Project.id, Project.name).where(Project.owner_id == user.id))).all())
    for r in by_project:
        r["name"] = names.get(r["project_id"], "(deleted project)")
    day = func.date(LlmCall.created_at)
    daily = (await db.execute(select(day.label("day"), *SUMS).where(*where).group_by(day).order_by(day))).all()
    return {
        "days": days, "models": _models(),
        "totals": _row((await db.execute(select(*SUMS).where(*where))).one()),
        "by_project": by_project,
        "by_agent": await _grouped(db, where, LlmCall.agent),
        "by_model": await _grouped(db, where, LlmCall.model_key),
        "daily": [_row(r) for r in daily],
    }
