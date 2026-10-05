"""Live check of Orion (research + plan) and Atlas (mapping document + examples check, no code) on a scratch copy of a real project.
Real Bedrock calls (~$0.5–1). Not collected by pytest.
Run on the host:  cd /opt/orkestra/app/backend && ORKESTRA_DATA_DIR=/opt/orkestra/data \
                  /opt/orkestra/venv/bin/python -m tests.live_crew_flow <source_project_id>
"""
import asyncio
import json
import sys
import time

from app.agents import ba, cto  # noqa: F401
from app.agents.intake import load_intake, update_intake
from app.db.base import SessionLocal, init_db
from app.db.models import AgentState, Intake, Project
from app.orchestrator.runner import JobContext
from app.services.storage import ProjectStore


async def main(src: str) -> None:
    await init_db()
    source = await load_intake(src)
    async with SessionLocal() as db:
        sp = await db.get(Project, src)
        p = Project(owner_id=sp.owner_id, name="[scratch] crew check", budget_usd=5, archived=True)
        db.add(p)
        await db.flush()
        for k in ("cto", "intake", "ba", "ta", "tp", "de", "qa"):
            db.add(AgentState(project_id=p.id, agent=k))
        db.add(Intake(project_id=p.id, answers=source.answers, uploads=source.uploads, status="signed_off",
                      requirement_md=source.requirement_md))
        await db.commit()
        pid = p.id
    ProjectStore(pid).create("scratch")
    try:
        t0 = time.time()
        await cto.plan(JobContext("job_live_cto", pid, {"feedback": "lxml with Python 3.14 works fine for us; verify facts instead of guessing."}, {}, sp.owner_id))
        plan = (await load_intake(pid)).plan
        print(f"=== ORION ({time.time() - t0:.0f}s) research trail: {plan['research_trail']}")
        for r in plan.get("research", []):
            print(f"  [{r['verdict']}] {r['claim']} → {r['finding'][:160]}  ({r['source'][:90]})")
        print("  risks:")
        for r in plan["risks"]:
            print(f"   - {r['risk'][:150]}")
        t1 = time.time()
        await ba.map_data(JobContext("job_live_ba", pid, {}, {}, sp.owner_id))
        m = ba.load_mapping(pid)
        print(f"=== ATLAS ({time.time() - t1:.0f}s) {m['title']} · {len(m['rows'])} rows · check attempts {m['check_attempts']}")
        for p_ in m["proof"]:
            print(f"  {'AGREES' if p_['pass'] else 'DISAGREES'} {p_['name']} ({p_['expect']}): {p_['actual'][:120]}")
        print("  row 1:", json.dumps(m["rows"][0])[:300])
        async with SessionLocal() as db:
            print(f"=== cost ${(await db.get(Project, pid)).cost_usd:.3f}")
    finally:
        async with SessionLocal() as db:
            await db.delete(await db.get(Project, pid))
            await db.commit()
        ProjectStore(pid).delete()


asyncio.run(main(sys.argv[1]))
