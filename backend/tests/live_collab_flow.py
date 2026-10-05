"""Live check of crew collaboration on a scratch copy of a real, signed-off project (real Bedrock, ~$0.5–1):
  CR (+ a stub logger.py) → Orion triage → Echo amendment turn → user: "wrong file, tell Orion, use logger_v2.py"
  → Echo calls message_orion → Orion's inbox answers → Atlas writes a mapping with NO code (examples check).
Prints the crew room and the cache use per Echo turn. Jobs are captured in-process; the scratch project is deleted.
Run on the host:  cd /opt/orkestra/app/backend && ORKESTRA_DATA_DIR=/opt/orkestra/data \
                  /opt/orkestra/venv/bin/python -m tests.live_collab_flow <source_project_id>
"""
import asyncio
import sys
import time

from sqlalchemy import select

from app.agents import ba, cto, intake as echo  # noqa: F401
from app.agents.intake import load_intake, update_intake
from app.db.base import SessionLocal, init_db, utcnow
from app.db.models import AgentState, Intake, LlmCall, Project
from app.orchestrator import changes, crewchat, runner as runner_mod
from app.orchestrator.runner import JobContext
from app.services.storage import ProjectStore
from tests.live_change_flow import CapturedJobs

STUB = '''import logging
def log_success_msg(pattern):
    level = pattern["level"]          # TODO: actually write the line
def log_error_msg():
    pass
log_error_msg()
'''
FIXED = '''import logging
logger = logging.getLogger("app")
def log_success_msg(pattern, message):
    logger.info("%s %s %s - %s", pattern["request_id"], pattern["classname"], pattern["unique_id"], message)
def log_error_msg(pattern, error_code, error_message):
    logger.error("%s %s %s %s - %s", pattern["request_id"], pattern["classname"], pattern["unique_id"], error_code, error_message)
'''


async def add_upload(pid: str, store: ProjectStore, name: str, text: str) -> None:
    store.write(f"inputs/{name}", text.encode())
    it = await load_intake(pid)
    await update_intake(pid, uploads=[u for u in (it.uploads or []) if u["name"] != name]
                        + [{"name": name, "kind": "document", "chars": len(text), "text": text}])


async def say_as_user(pid: str, text: str, files: list[str]) -> None:
    it = await load_intake(pid)
    await update_intake(pid, busy="chatting", chat=list(it.chat) + [{
        "role": "user", "ts": utcnow().isoformat(), "attachments": files,
        "text": text + (f"\n\n📎 Attached: {', '.join(files)}" if files else "")}])


async def last_chat_cost(pid: str) -> str:
    async with SessionLocal() as db:
        c = (await db.execute(select(LlmCall).where(LlmCall.project_id == pid, LlmCall.agent == "intake")
                              .order_by(LlmCall.id.desc()).limit(1))).scalar_one()
        return f"${c.cost_usd:.4f} · input {c.input_tokens} · cache read {c.cache_read_tokens} · cache write {c.cache_write_tokens}"


async def main(src: str) -> None:
    await init_db()
    jobs = CapturedJobs()
    runner_mod.runner = jobs
    source = await load_intake(src)
    async with SessionLocal() as db:
        sp = await db.get(Project, src)
        p = Project(owner_id=sp.owner_id, name="[scratch] crew collaboration check", budget_usd=5, archived=True)
        db.add(p)
        await db.flush()
        for k in ("cto", "intake", "ba", "ta", "tp", "de", "qa"):
            db.add(AgentState(project_id=p.id, agent=k, status="done" if k in ("cto", "intake") else "waiting"))
        db.add(Intake(project_id=p.id, answers=source.answers, uploads=[u for u in (source.uploads or []) if "logger" not in u["name"]],
                      chat=[], rounds=source.rounds, status="signed_off", requirement_md=source.requirement_md, plan=source.plan,
                      signed_off_at=source.signed_off_at))
        await db.commit()
        pid, owner = p.id, sp.owner_id
    store = ProjectStore(pid)
    store.create("scratch")
    store.write("00_requirement.md", source.requirement_md)
    try:
        t0 = time.time()
        await add_upload(pid, store, "logger.py", STUB)
        cr = await changes.create(pid, "Our logs must use our logger.py (attached), shipped as a Lambda layer, not in the function code.", ["logger.py"])
        await cto.triage(JobContext("job_t", pid, jobs.pop("cto.triage"), {}, owner))
        cr = await changes.get(cr.id)
        print(f"=== TRIAGE route={cr.route} {cr.version_from}→{cr.version_to}")

        await echo.chat(JobContext("job_e1", pid, jobs.pop("intake.chat"), {}, owner))
        print(f"=== ECHO 1 ({await last_chat_cost(pid)})\n{(await load_intake(pid)).chat[-1]['text'][:700]}\n")

        await add_upload(pid, store, "logger_v2.py", FIXED)
        await say_as_user(pid, "Sorry, logger.py was the wrong file. Please tell Orion; use logger_v2.py instead.", ["logger_v2.py"])
        await echo.chat(JobContext("job_e2", pid, {}, {}, owner))
        chat = (await load_intake(pid)).chat
        print(f"=== ECHO 2 ({await last_chat_cost(pid)})\n{[m for m in chat if m['role'] == 'echo'][-1]['text'][:700]}")
        print("  notes to Orion:", [m["text"] for m in chat if m["role"] == "note"])
        inbox = jobs.pop("cto.inbox")
        await cto.inbox(JobContext("job_i", pid, inbox, {}, owner))
        cr = await changes.get(cr.id)
        print(f"=== ORION INBOX → attachments now {cr.attachments}")
        print("  Orion's reply in Echo's chat:", [m["text"] for m in (await load_intake(pid)).chat if m.get("kind") == "reply"])

        await say_as_user(pid, "Yes, the crew can make small safe fixes. Level from LOG_LEVEL, default INFO.", [])
        await echo.chat(JobContext("job_e3", pid, {}, {}, owner))
        print(f"=== ECHO 3 ({await last_chat_cost(pid)})")

        t1 = time.time()
        await ba.map_data(JobContext("job_ba", pid, {}, {}, owner))
        m = ba.load_mapping(pid)
        print(f"\n=== ATLAS ({time.time() - t1:.0f}s) {len(m['rows'])} rows · {len(m['samples'])} examples · "
              f"check attempts {m['check_attempts']} · code fields: {[k for k in m if 'code' in k]}")
        for p_ in m["proof"]:
            print(f"  {'✓' if p_['pass'] else '✗'}{'' if p_.get('verified', True) else ' (Dev/Quinn)'} {p_['name']}: {p_['actual'][:110]}")

        print("\n=== CREW ROOM")
        for c in await crewchat.history(pid):
            print(f"  {c['sender']:>6} → {c['to']:<6} {c['kind']:<8} {c['text'][:150]}")
        async with SessionLocal() as db:
            print(f"=== total {time.time() - t0:.0f}s · cost ${(await db.get(Project, pid)).cost_usd:.3f} · leftover jobs {[k for k, _ in jobs.jobs]}")
    finally:
        async with SessionLocal() as db:
            await db.delete(await db.get(Project, pid))
            await db.commit()
        store.delete()


asyncio.run(main(sys.argv[1]))
