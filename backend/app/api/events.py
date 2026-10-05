"""Server-Sent Events: one stream per project (pipeline view) or per user (dashboard).
Reconnects replay missed events from the DB via the standard Last-Event-ID header."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api.projects import owned
from app.core.security import current_user
from app.db.base import SessionLocal
from app.db.models import Event, User
from app.orchestrator.bus import bus, serialize

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/stream")
async def stream(request: Request, project_id: str | None = None, user: User = Depends(current_user)):
    if project_id:
        async with SessionLocal() as db:
            await owned(project_id, user, db)
    channel = f"project:{project_id}" if project_id else f"user:{user.id}"
    last_id = request.headers.get("last-event-id")
    queue = bus.subscribe(channel)

    async def gen():
        # ~2 KB comment first: some corporate proxies buffer small streamed responses; this flushes them.
        yield {"comment": " " * 2048}
        try:
            if last_id and last_id.isdigit():
                async with SessionLocal() as db:
                    col = Event.project_id if project_id else Event.user_id
                    missed = (await db.execute(select(Event).where(col == (project_id or user.id),
                                                                   Event.id > int(last_id))
                                               .order_by(Event.id).limit(500))).scalars().all()
                for ev in missed:
                    yield {"id": str(ev.id), "event": "message", "data": json.dumps(serialize(ev))}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                    yield {"id": str(ev["id"]) if ev["id"] else None, "event": "message", "data": json.dumps(ev)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            bus.unsubscribe(channel, queue)

    return EventSourceResponse(gen(), ping=15, headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
