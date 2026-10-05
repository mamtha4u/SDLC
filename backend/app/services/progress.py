"""How far along a working agent probably is (user, 10-04: "whenever an agent is working, show the progress in
percentage, so the user has hope it's really working in the background").

Agents run open-ended model loops, so there's no exact step count. The estimate is honest instead: how long this kind
of job usually takes on this host (the median of its last finished runs, across projects), against how long this one
has been running. It climbs steadily to 90% at the usual time, then creeps towards 98% ("taking longer than usual")
and never claims 100% before the job really ends.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job

# first guesses until the host has history (seconds), from the runs logged in docs/08
DEFAULT_SECONDS = {"intake.chat": 25, "intake.review": 90, "intake.finalize": 150, "cto.plan": 180, "cto.triage": 40,
                   "cto.review": 90, "cto.inbox": 20, "ba.map": 480, "ta.design": 600, "ta.review": 240, "tp.iac": 600,
                   "tp.deploy": 60, "tp.apply": 150, "tp.drift": 60, "tp.restore": 90, "tp.destroy": 120, "de.code": 1200,
                   "de.deploy": 300, "qa.plan": 300, "qa.run": 600, "qa.live": 900,
                   # kickoff conversations (agents/talk.py): the opening reads the earlier phases, a reply is a chat turn
                   "ba.talk": 40, "ta.talk": 60, "tp.talk": 40, "de.talk": 40, "qa.talk": 40,
                   **{f"{a}.talk_reply": 30 for a in ("ba", "ta", "tp", "de", "qa")}, **{f"{a}.talk_finish": 5 for a in ("ba", "ta", "tp", "de", "qa")}}
_typical: dict[str, tuple[float, float]] = {}  # kind → (computed at, seconds); recomputed every 10 minutes


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def typical_seconds(db: AsyncSession, kind: str) -> float:
    hit = _typical.get(kind)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    rows = (await db.execute(select(Job.started_at, Job.finished_at).where(Job.kind == kind, Job.status == "done",
                                                                          Job.started_at.is_not(None), Job.finished_at.is_not(None))
                             .order_by(Job.finished_at.desc()).limit(25))).all()
    spans = sorted(s for s in ((_utc(f) - _utc(st)).total_seconds() for st, f in rows) if s > 2)
    seconds = spans[len(spans) // 2] if len(spans) >= 3 else DEFAULT_SECONDS.get(kind, 120)
    _typical[kind] = (time.time(), float(seconds))
    return float(seconds)


def percent(elapsed: float, expected: float) -> int:
    t = max(0.0, elapsed) / max(expected, 5.0)
    p = 90 * t if t <= 1 else 90 + 8 * (1 - math.exp(-(t - 1) * 1.5))
    return max(1, min(98, int(p)))


async def for_agents(db: AsyncSession, project_id: str, working: list[str]) -> dict[str, dict]:
    """agent → {progress, expected_s, elapsed_s, kind} for the agents that are working now (their running job)."""
    if not working:
        return {}
    jobs = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status == "running")
                             .order_by(Job.started_at.desc()))).scalars().all()
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    for agent in working:
        job = next((j for j in jobs if j.kind.split(".", 1)[0] == agent and j.started_at), None)
        if not job:
            continue
        expected = await typical_seconds(db, job.kind)
        elapsed = (now - _utc(job.started_at)).total_seconds()
        out[agent] = {"progress": percent(elapsed, expected), "expected_s": int(expected), "elapsed_s": int(elapsed), "kind": job.kind,
                      "job_started_at": _utc(job.started_at)}
    return out
