from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from app.api import (agents, approvals, assistant, auth, build, changes, crew, design, events, infra, intake, mapping, projects, system,
                     talks, testing, tickets, usage)
from app.core.config import get_settings
from app.db.base import init_db
from app.agents import access, ba, codereview, cto, de, dispatch, drift, guide, intake as intake_agent, qa, review, ta, talk, tp, unblock, watch  # noqa: F401  (register job handlers)
from app.orchestrator import demo  # noqa: F401
from app.orchestrator import runner as runner_mod

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    from app.orchestrator import flow
    runner_mod.runner = runner_mod.JobRunner(get_settings().job_workers)
    await runner_mod.runner.start()  # first: marks jobs a restart cut off as interrupted (or queued again)
    await flow.reconcile()  # then: a card still "working" with no job behind it is told so (10-05: it ran before, and missed them)
    import asyncio

    watcher = asyncio.create_task(drift.watch())  # Terra's drift watch for live projects (settings.drift_check_minutes)
    crew_watch = asyncio.create_task(watch.watch())  # Orion and Archie watch the working agents (settings.crew_watch_seconds)
    yield
    watcher.cancel()
    crew_watch.cancel()
    await runner_mod.runner.stop()


app = FastAPI(title="Orkestra", version="0.1.0", lifespan=lifespan)
# Compress JSON + the JS/CSS bundle (~680 KB → ~200 KB): the biggest win for page speed behind corporate proxies.
class GZipExceptStreams:
    """gzip everything except the SSE stream (compression would buffer it)."""

    def __init__(self, app_) -> None:
        self.plain, self.gz = app_, GZipMiddleware(app_, minimum_size=1024, compresslevel=6)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        streaming = scope["type"] == "http" and scope["path"].startswith("/api/events")
        await (self.plain if streaming else self.gz)(scope, receive, send)


app.add_middleware(GZipExceptStreams)
for r in (auth.router, projects.router, intake.router, approvals.router, changes.router, agents.router, crew.router,
          mapping.router, design.router, build.router, infra.router, tickets.router, testing.router, assistant.router, usage.router,
          talks.router, events.router, system.router):
    app.include_router(r)

# Single origin: the built React app is served by FastAPI, with SPA fallback for client-side routes.
_dist = get_settings().frontend_dist
class ImmutableAssets(StaticFiles):
    """Vite assets have content-hashed names, so browsers may cache them forever."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_cache(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message["headers"]) + [(b"cache-control", b"public, max-age=31536000, immutable")]
            await send(message)
        await super().__call__(scope, receive, send_with_cache)


if (_dist / "assets").is_dir():
    app.mount("/assets", ImmutableAssets(directory=_dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    candidate = (_dist / path).resolve()
    if path and candidate.is_file() and _dist.resolve() in candidate.parents:
        return FileResponse(candidate)
    index = _dist / "index.html"
    if index.is_file():  # always revalidated: it names this release's assets, so a cached copy would keep the old app
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return {"app": "Orkestra API", "note": "frontend not built yet"}
