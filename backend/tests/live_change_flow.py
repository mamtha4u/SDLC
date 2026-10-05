"""Live check of the change-request flow on a scratch copy of a real, signed-off project:
CR (+ logger.py) → Orion triage → Echo amendment chat → sign-off writes v1.1 + diff → Orion impact re-plan → approval.
Real Bedrock calls (~$1). Not collected by pytest. Jobs are captured in-process so the live server never runs them.
Run on the host:  cd /opt/orkestra/app/backend && ORKESTRA_DATA_DIR=/opt/orkestra/data \
                  /opt/orkestra/venv/bin/python -m tests.live_change_flow <source_project_id>
"""
import asyncio
import sys
import time

from sqlalchemy import select

from app.agents import cto, intake as echo  # noqa: F401
from app.agents.intake import load_intake, update_intake
from app.db.base import SessionLocal, init_db, utcnow
from app.db.models import AgentState, Approval, Intake, Project
from app.orchestrator import changes, runner as runner_mod
from app.orchestrator.runner import JobContext
from app.services.storage import ProjectStore

LOGGER_PY = '''"""Team logging standard: one JSON line per event. Every Lambda must log through get_logger()."""
import json
import logging
import os
import time

SERVICE = os.environ.get("SERVICE_NAME", "unknown")


def mask_pii(value):
    # TODO: mask e-mail addresses and phone numbers before they are logged
    pass


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "service": SERVICE,
            "correlation_id": getattr(record, "correlation_id", None),
            "msg": mask_pii(record.getMessage()),
        })


def get_logger(name, correlation_id=None):
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
    log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    return logging.LoggerAdapter(log, {"correlation_id": correlation_id})
'''
CR_TEXT = ("We must use our team's own logging format. logger.py (attached) has to be packaged as a Lambda layer and "
           "the transform Lambda must use it for every log line.")
USER_REPLY = ("Go with your recommendations. Layer name orkestra-logger-layer, Python 3.14 compatible. mask_pii must mask "
              "e-mails and phone numbers; we'll fix the TODO ourselves before go-live, but note it as a dependency. "
              "correlation_id = the API Gateway request id.")


class CapturedJobs:
    """Stands in for the job runner: records what would be enqueued instead of handing it to the live workers."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue(self, kind: str, project_id: str | None = None, **payload) -> str:
        self.jobs.append((kind, payload))
        print(f"  ↳ would enqueue {kind} {sorted(payload)}")
        return "job_captured"

    def pop(self, kind: str) -> dict:
        for i, (k, p) in enumerate(self.jobs):
            if k == kind:
                return self.jobs.pop(i)[1]
        raise AssertionError(f"{kind} was not enqueued; got {[k for k, _ in self.jobs]}")


async def last_echo(pid: str) -> str:
    return next(m["text"] for m in reversed((await load_intake(pid)).chat) if m["role"] == "echo")


async def main(src: str) -> None:
    await init_db()
    jobs = CapturedJobs()
    runner_mod.runner = jobs
    source = await load_intake(src)
    async with SessionLocal() as db:
        sp = await db.get(Project, src)
        p = Project(owner_id=sp.owner_id, name="[scratch] change-request check", budget_usd=5, archived=True)
        db.add(p)
        await db.flush()
        for k in ("cto", "intake", "ba", "ta", "tp", "de", "qa"):
            db.add(AgentState(project_id=p.id, agent=k, status="done" if k in ("cto", "intake") else "waiting"))
        db.add(Intake(project_id=p.id, answers=source.answers, uploads=list(source.uploads or []), chat=list(source.chat or []),
                      rounds=source.rounds, status="signed_off", requirement_md=source.requirement_md, plan=source.plan,
                      signed_off_at=source.signed_off_at))
        await db.commit()
        pid, owner = p.id, sp.owner_id
    store = ProjectStore(pid)
    store.create("scratch")
    store.write("00_requirement.md", source.requirement_md)
    try:
        t0 = time.time()
        # what POST /changes does: store the file, add it to Echo's uploads, open the CR
        store.write("inputs/logger.py", LOGGER_PY.encode())
        it = await load_intake(pid)
        await update_intake(pid, uploads=list(it.uploads or []) + [{"name": "logger.py", "kind": "document", "chars": len(LOGGER_PY), "text": LOGGER_PY}])
        cr = await changes.create(pid, CR_TEXT, ["logger.py"])
        print(f"=== {changes.label(cr)} raised")

        await cto.triage(JobContext("job_live_triage", pid, jobs.pop("cto.triage"), {}, owner))
        cr = await changes.get(cr.id)
        print(f"=== ORION TRIAGE ({time.time() - t0:.0f}s) route={cr.route} status={cr.status} {cr.version_from}→{cr.version_to}")
        print(f"  summary: {cr.triage['summary']}\n  reason: {cr.triage['reason']}\n  brief: {cr.triage['brief']}")
        print(f"  affected: {[(a['agent'], a['why'][:90]) for a in cr.triage['affected_agents']]}")
        print(f"  needs_from_user: {cr.triage['needs_from_user']}")
        assert cr.route == "requirement", "logger.py + layer is new build scope; it must go to Echo"

        t1 = time.time()
        await echo.chat(JobContext("job_live_echo1", pid, jobs.pop("intake.chat"), {}, owner))
        print(f"=== ECHO turn 1 ({time.time() - t1:.0f}s)\n{await last_echo(pid)}\n")

        it = await load_intake(pid)
        await update_intake(pid, chat=list(it.chat) + [{"role": "user", "text": USER_REPLY, "ts": utcnow().isoformat()}], busy="chatting")
        t2 = time.time()
        await echo.chat(JobContext("job_live_echo2", pid, {}, {}, owner))
        print(f"=== ECHO turn 2 ({time.time() - t2:.0f}s)\n{await last_echo(pid)}\n")

        await update_intake(pid, busy="finalizing")
        t3 = time.time()
        await echo.finalize(JobContext("job_live_final", pid, {}, {}, owner))
        cr = await changes.get(cr.id)
        added = sum(1 for ln in cr.diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in cr.diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        print(f"=== ECHO FINALIZE ({time.time() - t3:.0f}s) {cr.version_to}: +{added} −{removed} lines · versions {store.versions()}")
        print("\n".join(ln for ln in cr.diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))[:2500])

        t4 = time.time()
        await cto.plan(JobContext("job_live_replan", pid, jobs.pop("cto.plan"), {}, owner))
        plan = (await load_intake(pid)).plan
        print(f"\n=== ORION RE-PLAN ({time.time() - t4:.0f}s)\n  impact: {plan.get('feedback_addressed')}")
        for s in plan["steps"]:
            print(f"   - {s['agent']}: {s['task'][:160]}")
        async with SessionLocal() as db:
            ap = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.status == "pending"))).scalar_one()
            states = {s.agent: (s.status, s.activity[:80]) for s in (await db.execute(select(AgentState).where(AgentState.project_id == pid))).scalars()}
            cost = (await db.get(Project, pid)).cost_usd
        print(f"  approval: {ap.title}")
        print(f"  agent states: {states}")
        print(f"=== total {time.time() - t0:.0f}s · cost ${cost:.3f} · leftover jobs {jobs.jobs}")
    finally:
        async with SessionLocal() as db:
            await db.delete(await db.get(Project, pid))
            await db.commit()
        store.delete()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
