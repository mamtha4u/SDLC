"""Event bus: persist every event (activity feed + SSE replay), then fan out to live subscribers."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from app.db.base import SessionLocal
from app.db.models import Event


def serialize(ev: Event) -> dict[str, Any]:
    return {
        "id": ev.id,
        "project_id": ev.project_id,
        "type": ev.type,
        "agent": ev.agent,
        "message": ev.message,
        "data": ev.data or {},
        "created_at": ev.created_at.isoformat(),
    }


class EventBus:
    def __init__(self) -> None:
        # channel = "project:<id>" or "user:<id>"
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subs[channel].discard(q)

    async def publish(
        self,
        type: str,
        *,
        project_id: str | None = None,
        user_id: str | None = None,
        agent: str | None = None,
        message: str = "",
        data: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        ev = Event(project_id=project_id, user_id=user_id, type=type, agent=agent, message=message, data=data or {})
        if persist:
            async with SessionLocal() as db:
                db.add(ev)
                await db.commit()
                await db.refresh(ev)
        else:
            from app.db.base import utcnow

            ev.id, ev.created_at = 0, utcnow()
        payload = serialize(ev)
        for channel in filter(None, [project_id and f"project:{project_id}", user_id and f"user:{user_id}"]):
            for q in list(self._subs.get(channel, ())):
                if q.full():  # slow consumer: drop oldest rather than block agents
                    q.get_nowait()
                q.put_nowait(payload)
        return payload


bus = EventBus()
