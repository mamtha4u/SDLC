"""Live check of the build crew on a scratch copy of a real project whose mapping is approved (real Bedrock, ~$3–6):
  Archie (design + diagram) → Terra (Terraform + validate) → Dev (code + tests + coverage gate) → Quinn (QA + bugs)
  → [Dev fixes → Quinn retests, once, if bugs] → Terra's deploy access check (read-only).
Jobs are captured in-process (the live workers never see them); the scratch project is deleted at the end.
Run on the host DETACHED (it takes 20–40 min; SSM kills foreground commands after their timeout):
  src/infra/scripts/remote/live_full_crew.sh <source_project_id> [--resume <scratch_project_id>]   then tail /tmp/live_full_crew.log
"""
import asyncio
import shutil
import sys
import time

from app.agents import de, qa, ta, tp  # noqa: F401
from app.agents.intake import load_intake
from app.db.base import SessionLocal, init_db
from app.db.models import AgentState, Intake, Project
from app.orchestrator import crewchat, runner as runner_mod
from app.orchestrator.runner import JobContext
from app.services.storage import ProjectStore
from tests.live_change_flow import CapturedJobs


def say(*a):
    print(*a, flush=True)


async def cost(pid: str) -> float:
    async with SessionLocal() as db:
        return (await db.get(Project, pid)).cost_usd


async def main(src: str, resume: str | None = None) -> None:
    """resume: an earlier scratch project id (e.g. after the host killed a run): stages already done are skipped."""
    await init_db()
    runner_mod.runner = CapturedJobs()
    source = await load_intake(src)
    async with SessionLocal() as db:
        sp = await db.get(Project, src)
        owner = sp.owner_id
        if resume == "auto":  # the latest leftover scratch copy
            from sqlalchemy import select

            resume = (await db.execute(select(Project.id).where(Project.name == "[scratch] full crew check")
                                       .order_by(Project.created_at.desc()))).scalars().first()
        if resume:
            pid = resume
        else:
            p = Project(owner_id=owner, name="[scratch] full crew check", budget_usd=10, archived=True)
            db.add(p)
            await db.flush()
            for k in ("cto", "intake", "ba", "ta", "tp", "de", "qa"):
                db.add(AgentState(project_id=p.id, agent=k, status="done" if k in ("cto", "intake", "ba") else "waiting"))
            db.add(Intake(project_id=p.id, answers=source.answers, uploads=source.uploads, chat=[], rounds=source.rounds,
                          status="signed_off", requirement_md=source.requirement_md, plan=source.plan, signed_off_at=source.signed_off_at))
            await db.commit()
            pid = p.id
    s_src, store = ProjectStore(src), ProjectStore(pid)
    if not resume:
        store.create("scratch")
        shutil.copytree(s_src.root / s_src.manifest()["current_version"], store.root / "v1", dirs_exist_ok=True)
        for stale in ("mapping/transform_reference.py",):
            (store.root / "v1" / stale).unlink(missing_ok=True)
    say(f"scratch project {pid}" + (" (resumed)" if resume else ""))
    done = {"ARCHIE": lambda: ta.load_design(pid), "TERRA": lambda: tp.load_preview(pid), "DEV": lambda: de.load_code(pid), "QUINN": lambda: qa.load_qa(pid)}
    t00 = time.time()
    try:
        for label, fn, payload in (("ARCHIE", ta.design, {}), ("TERRA", tp.iac, {}), ("DEV", de.code, {}), ("QUINN", qa.test, {})):
            if resume and done[label]():
                say(f"=== {label}: already done, skipped")
                continue
            t0 = time.time()
            await fn(JobContext(f"job_{label}", pid, payload, {}, owner))
            say(f"\n=== {label} done in {time.time() - t0:.0f}s · project cost so far ${await cost(pid):.2f}")
            if label == "ARCHIE":
                d = ta.load_design(pid)
                say(f"  summary: {d['summary']}\n  resources: {[r['name'] for r in d['resources']]}\n  gates: {d['quality_gates']}")
                say(f"  diagram: {len(store.read(ta.DRAWIO))} bytes, problems {__import__('app.services.diagram', fromlist=['x']).validate(d['architecture'])}")
            if label == "TERRA":
                pv = tp.load_preview(pid)
                say(f"  files: {pv['files']}\n  validate: ok={pv['validate']['ok']} available={pv['validate'].get('available')} reformatted={pv['validate'].get('reformatted')}")
                say(f"  resources: {[(r['name'], r['type']) for r in pv['resources']]}")
            if label == "DEV":
                c = de.load_code(pid)
                say(f"  files: {c['files']}\n  tests {c['tests']['passed']}/{c['tests']['total']} · coverage {c['coverage']}% (gate {c['coverage_gate']}) · runs {c['test_runs']}")
            if label == "QUINN":
                q = qa.load_qa(pid)
                say(f"  plan {len(q['plan'])} · tests {q['tests']['passed']}/{q['tests']['total']} · bugs {[(b['id'], b['status'], b['title']) for b in q['bugs']]}")
        if de.open_bugs(pid):
            say("\n=== bugs found → Dev fixes, Quinn retests")
            await de.code(JobContext("job_DEV2", pid, {}, {}, owner))
            c = de.load_code(pid)
            say(f"  DEV fix: version {c['version']} · fixed {c['fixed_bugs']} · tests {c['tests']['passed']}/{c['tests']['total']} · coverage {c['coverage']}%")
            await qa.test(JobContext("job_QUINN2", pid, {}, {}, owner))
            q = qa.load_qa(pid)
            say(f"  QUINN retest: {q['tests']['passed']}/{q['tests']['total']} · bugs {[(b['id'], b['status']) for b in q['bugs']]}")
        from app.agents import access as access_mod
        from app.services import aws_access

        await access_mod.access(JobContext("job_ACCESS", pid, {}, {}, owner))  # drafts only: nothing is created in AWS
        acc = aws_access.load(pid) or {}
        say(f"\n=== ORION ACCESS PLAN: prefix {acc.get('prefix')} · services {acc.get('services')} · problem {acc.get('problem')}")
        say("\n=== CREW ROOM (last 40)")
        for m in (await crewchat.history(pid))[-40:]:
            say(f"  {m['sender']:>6} → {m['to']:<6} {m['kind']:<8} {m['text'][:140]!r}")
        say(f"\n=== TOTAL {time.time() - t00:.0f}s · cost ${await cost(pid):.2f} · versions {store.versions()} · leftover jobs {[k for k, _ in runner_mod.runner.jobs]}")
    finally:
        async with SessionLocal() as db:
            await db.delete(await db.get(Project, pid))
            await db.commit()
        store.delete()
        say("scratch project deleted")


asyncio.run(main(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--resume" else None))
