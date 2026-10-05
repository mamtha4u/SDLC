"""Background job runner.

Jobs live in SQLite, so closing the browser never stops agents and a server restart doesn't lose work:
on startup, jobs that were `running` are re-queued (handlers resume from their checkpoint) or marked
`interrupted` if the handler can't resume. The per-project kill switch (`Project.paused`) is honoured
at every checkpoint.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db.base import SessionLocal, utcnow
from app.db.models import AgentState, Job, Project
from app.orchestrator.bus import bus

log = logging.getLogger("orkestra.runner")

Handler = Callable[["JobContext"], Awaitable[None]]


@dataclass
class HandlerSpec:
    fn: Handler
    resumable: bool


HANDLERS: dict[str, HandlerSpec] = {}


def handler(kind: str, resumable: bool = True):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = HandlerSpec(fn, resumable)
        return fn

    return deco


class JobCancelled(Exception):
    pass


@dataclass
class JobContext:
    job_id: str
    project_id: str | None
    payload: dict
    checkpoint: dict = field(default_factory=dict)
    owner_id: str | None = None

    async def save_checkpoint(self, **values) -> None:
        self.checkpoint.update(values)
        async with SessionLocal() as db:
            job = await db.get(Job, self.job_id)
            job.checkpoint = dict(self.checkpoint)
            await db.commit()

    async def gate(self) -> None:
        """Call between steps: raises if cancelled, blocks while the project's kill switch is on."""
        announced = False
        while True:
            async with SessionLocal() as db:
                job = await db.get(Job, self.job_id)
                if job is None or job.status == "cancelled":
                    raise JobCancelled()
                project = await db.get(Project, self.project_id) if self.project_id else None
                if not project or not project.paused:
                    return
            if not announced:
                await self.emit("project.paused", "Kill switch on — agents paused")
                announced = True
            await asyncio.sleep(1)

    async def emit(self, type: str, message: str = "", agent: str | None = None, **data) -> None:
        await bus.publish(type, project_id=self.project_id, user_id=self.owner_id, agent=agent, message=message, data=data)

    async def set_agent(self, agent: str, status: str, activity: str = "", **metrics) -> None:
        async with SessionLocal() as db:
            row = (await db.execute(select(AgentState).where(
                AgentState.project_id == self.project_id, AgentState.agent == agent))).scalar_one_or_none()
            if row is None:
                row = AgentState(project_id=self.project_id, agent=agent)
                db.add(row)
            if status == "working" and row.status != "working":
                row.started_at, row.ended_at = utcnow(), None
            if status in ("done", "failed"):
                row.ended_at = utcnow()
            row.status, row.activity = status, activity or row.activity
            for k, v in metrics.items():
                setattr(row, k, v)
            project = await db.get(Project, self.project_id)
            if status == "working":
                project.current_agent = agent
            project.last_activity = activity or project.last_activity
            await db.commit()
        await self.emit("agent.state", activity, agent=agent, status=status, **metrics)

    async def set_project(self, **fields) -> None:
        async with SessionLocal() as db:
            project = await db.get(Project, self.project_id)
            for k, v in fields.items():
                setattr(project, k, v)
            await db.commit()
        await self.emit("project.updated", "", **{k: v for k, v in fields.items() if isinstance(v, (str, int, float, bool))})


class JobRunner:
    def __init__(self, workers: int) -> None:
        self.workers = workers
        self._tasks: list[asyncio.Task] = []
        self._wake = asyncio.Event()
        self._claim = asyncio.Lock()
        self._stopping = False
        self._running: dict[str, asyncio.Task] = {}  # job id → its task, so the crew watch can restart a stuck step
        self._aborted: dict[str, str] = {}

    AUTO_RETRIES, RETRY_AFTER_S = 2, 60

    async def _retry_later(self, job: Job, ctx: "JobContext", exc: Exception) -> None:
        """A step that failed only because Claude on AWS was busy (503, overloaded, a dropped or stuck call, even after the
        retries and the other model) runs again by itself in a minute, up to AUTO_RETRIES times (10-05: Bedrock's Opus
        answered 503 for a while and the user was left with Try again)."""
        from app.agents import llm

        n = int((job.payload or {}).get("auto_retry") or 0)
        if not job.project_id or not llm.transient(exc) or n >= self.AUTO_RETRIES:
            return
        line = f"Claude on AWS was busy ({str(exc)[:80]}): retrying automatically in 1 min ({n + 1} of {self.AUTO_RETRIES})"
        agents = {job.kind.split(".", 1)[0], (job.payload or {}).get("agent")} & {"intake", "cto", "ba", "ta", "tp", "de", "qa"}
        for a in agents:
            await ctx.set_agent(a, "blocked", line)
        await ctx.set_project(status="running", last_activity=line)

        async def later() -> None:
            await asyncio.sleep(self.RETRY_AFTER_S)
            await self.enqueue(job.kind, project_id=job.project_id, **{**(job.payload or {}), "auto_retry": n + 1})
        asyncio.create_task(later())

    def abort(self, job_id: str, why: str) -> bool:
        """Stop a running job that stopped responding (agents/watch.py). The handler sees CancelledError wherever it
        waits (e.g. a hung model call); the job ends as "interrupted"."""
        task = self._running.get(job_id)
        if not task or task.done():
            return False
        self._aborted[job_id] = why
        task.cancel()
        return True

    async def enqueue(self, kind: str, project_id: str | None = None, **payload) -> str:
        if kind not in HANDLERS:
            raise ValueError(f"unknown job kind {kind}")
        async with SessionLocal() as db:
            job = Job(kind=kind, project_id=project_id, payload=payload)
            db.add(job)
            await db.commit()
            job_id = job.id
        self._wake.set()
        return job_id

    async def cancel_project_jobs(self, project_id: str) -> None:
        async with SessionLocal() as db:
            for job in (await db.execute(select(Job).where(
                    Job.project_id == project_id, Job.status.in_(["queued", "running"])))).scalars():
                job.status = "cancelled"
            await db.commit()

    async def start(self) -> None:
        await self._recover()
        self._tasks = [asyncio.create_task(self._worker(i)) for i in range(self.workers)]

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _recover(self) -> None:
        async with SessionLocal() as db:
            stale = (await db.execute(select(Job).where(Job.status == "running"))).scalars().all()
            for job in stale:
                spec = HANDLERS.get(job.kind)
                job.status = "queued" if spec and spec.resumable else "interrupted"
                log.warning("recovered job %s (%s) -> %s", job.id, job.kind, job.status)
            await db.commit()
        for job in stale:
            await bus.publish("job.recovered", project_id=job.project_id,
                              message=f"Server restarted — {'resuming' if job.status == 'queued' else 'interrupted'} {job.kind}")

    async def _next_job(self) -> Job | None:
        from sqlalchemy import or_

        async with self._claim, SessionLocal() as db:  # a project with the kill switch on starts nothing new
            job = (await db.execute(select(Job).outerjoin(Project, Job.project_id == Project.id)
                                    .where(Job.status == "queued", or_(Job.project_id.is_(None), Project.paused == False,  # noqa: E712
                                                                       Project.paused.is_(None)))
                                    .order_by(Job.created_at).limit(1))).scalar_one_or_none()
            if job:
                job.status, job.started_at, job.attempts = "running", utcnow(), job.attempts + 1
                await db.commit()
            return job

    async def _worker(self, n: int) -> None:
        while not self._stopping:
            job = await self._next_job()
            if job is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
                continue
            await self._run(job)

    async def _run(self, job: Job) -> None:
        owner = None
        if job.project_id:
            async with SessionLocal() as db:
                p = await db.get(Project, job.project_id)
                owner = p.owner_id if p else None
        ctx = JobContext(job.id, job.project_id, job.payload or {}, dict(job.checkpoint or {}), owner)
        status, error = "done", None
        task = asyncio.create_task(HANDLERS[job.kind].fn(ctx))
        self._running[job.id] = task
        try:
            await task
        except asyncio.CancelledError:
            if job.id not in self._aborted:  # the server is stopping: let it
                raise
            status, error = "interrupted", f"Restarted by the crew watch: {self._aborted.pop(job.id)}"
        except JobCancelled:
            status = "cancelled"
        except Exception as exc:  # noqa: BLE001 — a failing job must never kill the worker
            status, error = "failed", f"{exc}\n{traceback.format_exc()[-2000:]}"
            log.exception("job %s failed", job.id)
            owner_agent = {"intake": "intake", "cto": "cto", "ba": "ba"}.get(job.kind.split(".", 1)[0])
            await ctx.emit("job.failed", f"{job.kind} failed: {exc}", agent=owner_agent)
            await self._retry_later(job, ctx, exc)
        self._running.pop(job.id, None)
        async with SessionLocal() as db:
            row = await db.get(Job, job.id)
            if row and row.status != "cancelled":
                row.status = status
            if row:
                row.error, row.finished_at = error, utcnow()
            await db.commit()


runner: JobRunner | None = None
