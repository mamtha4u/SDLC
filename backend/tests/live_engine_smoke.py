"""Live smoke test for the agent engine (real Bedrock calls, ~$0.02). Not collected by pytest.
Run on the host:  cd /opt/orkestra/app/backend && ORKESTRA_DATA_DIR=/tmp/ork-smoke ../../venv/bin/python -m tests.live_engine_smoke
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("ORKESTRA_DATA_DIR", "/tmp/ork-smoke")

from app.agents.base import Tool, obj, run_loop  # noqa: E402
from app.db.base import SessionLocal, init_db  # noqa: E402
from app.db.models import AgentState, Project, User  # noqa: E402
from app.services.storage import ProjectStore  # noqa: E402


async def main() -> None:
    await init_db()
    async with SessionLocal() as db:
        u = User(username=f"smoke{os.getpid()}", display_name="Smoke", password_hash="x")
        db.add(u)
        await db.flush()
        p = Project(owner_id=u.id, name="smoke", budget_usd=1.0)
        db.add(p)
        await db.flush()
        db.add(AgentState(project_id=p.id, agent="intake"))
        await db.commit()
        pid = p.id
    ProjectStore(pid).create("smoke")

    tool = Tool(name="submit", terminal=True, description="Submit the answer. Call it exactly once.",
                schema=obj({"capital": {"type": "string"}, "confidence": {"type": "integer"}}))
    streamed: list[str] = []

    async def on_text(t: str) -> None:
        streamed.append(t)

    res = await run_loop(project_id=pid, agent="intake", system="You are terse.",
                         messages=[{"role": "user", "content": "What is the capital of Ireland? Call submit."}],
                         tools=[tool], max_turns=2, purpose="smoke", on_text=on_text)
    print("terminal:", res.terminal, "| turns:", res.turns, "| stop:", res.stop_reason)
    async with SessionLocal() as db:
        p = await db.get(Project, pid)
        print(f"project cost: ${p.cost_usd:.5f}")
    print("audit log:", (ProjectStore(pid).root / "logs" / "llm.jsonl").read_text()[:400])


if __name__ == "__main__":
    asyncio.run(main())
