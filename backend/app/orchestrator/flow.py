"""Stage flow: which agent runs after which approval, and what "request changes" sends back.

  requirement (Echo) ──sign-off──▶ plan (Orion) ──▶ mapping (Atlas) ──▶ design (Archie)
  ──▶ infra (Terra's Terraform + Orion's AWS access) ──approve──▶ roles + terraform plan
  ──▶ deploy (only apply is left) ──approve──▶ terraform apply (placeholder code)
  ──▶ infra_check (you check the real resources in AWS) ──go-ahead──▶ Terra → Orion → Archie → Dev
  ──▶ Dev writes code + unit tests (coverage gate) ──▶ Archie reviews it ──▶ code_review (you check it, ask Archie anything)
  ──▶ Dev hands his packages to Terra ──▶ deploy (Terra's plan swaps them in for the placeholder) ──approve──▶ apply
  ──▶ code (Dev checked it's his code and tested the whole flow, with a walkthrough for you) ──▶ test_plan (Quinn's scenarios; you approve as test lead)
  ──▶ Quinn tests live ─┬─ live (his test sign-off; you sign it off: done)
                        └─ live_bugs: tickets → Dev / Terra → Quinn retests
  Every gate: approve, or request changes (the owner revises). Every approval writes that agent's sign-off document
  (services/signoff). An infrastructure change request goes to Orion's review; the plan for live resources has its own
  "deploy" gate.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.base import SessionLocal, utcnow
from app.db.models import AgentState, Approval, Intake, Project
from app.orchestrator import crewchat
from app.orchestrator.bus import bus
from app.services.storage import ProjectStore

# stage → (job that produces it, job to start when approved, label of the next step)
STAGES: dict[str, dict] = {
    # <agent>.talk (agents/talk.py): the agent first asks the person who owns its phase (new projects), then works. A gate's
    # "redo" goes straight to the work: the feedback is the input.
    "plan": {"agent": "cto", "redo": "cto.plan", "next": "ba.talk", "next_agent": "ba",
             "next_label": "Atlas (BA/DA) asks your data analyst whether and how the data changes, then writes the data mapping"},
    "mapping": {"agent": "ba", "redo": "ba.map", "next": "ta.talk", "next_agent": "ta",
                "next_label": "Archie (TA) proposes the architecture to your technical lead and agrees the tech stack, then designs the HLD, LLD and diagram"},
    "design": {"agent": "ta", "redo": "ta.design", "next": "tp.talk", "next_agent": "tp",
               "next_label": "Terra checks the platform details with your platform engineer, writes the Terraform, and Orion drafts the crew's AWS access"},
    "infra": {"agent": "tp", "redo": "tp.iac", "next": "cto.grant", "next_agent": "cto",
              "next_label": "Orion creates the crew's roles and Terra plans it; you approve the plan before anything is created"},
    "deploy": {"agent": "tp", "redo": "tp.deploy", "next": "tp.apply", "next_agent": "tp",
               "next_label": "Terra applies the plan in AWS, then you check it (for Dev's packages: Dev checks them live and tests the whole flow)"},
    "infra_check": {"agent": "tp", "redo": "tp.iac", "next": "cto.infra_ready", "next_agent": "cto",
                    "next_label": "Terra tells Orion, Orion tells Archie, Archie briefs Dev: the infrastructure is ready for the code"},
    # Archie reviewed Dev's code and you checked it (you can ask him anything first): only then does it go to AWS
    "code_review": {"agent": "ta", "redo": "de.code", "next": "de.deploy", "next_agent": "de",
                    "next_label": "Dev hands his packages to Terra, who deploys them (a plan you approve); then Dev tests the whole flow (API → Lambda → queue → logs) and shows you how"},
    "code": {"agent": "de", "redo": "de.code", "next": "cto.tickets", "next_agent": "cto",
             "next_label": "Quinn writes the test plan for you to approve, then tests every scenario live in AWS (failures become tickets)"},
    # Quinn's test plan: the user approves the scenarios as test lead before any test runs
    "test_plan": {"agent": "qa", "redo": "qa.plan", "next": "qa.live", "next_agent": "qa",
                  "next_label": "Quinn runs every approved scenario live in AWS; failures become tickets for Dev or Terra"},
    "live_bugs": {"agent": "qa", "redo": "qa.live", "next": "cto.tickets", "next_agent": "cto",
                  "next_label": "Orion sends each ticket to its fixer (code → Dev, infrastructure → Terra); then Quinn retests"},
    # "ask for more tests" at the final gate: Quinn adds them to the plan (you approve it), then runs it again
    "live": {"agent": "qa", "redo": "qa.plan", "next": None, "next_agent": None, "final": True,
             "next_label": "Done: testing signed off, the flow is live in AWS and tested"},
    # Terra's drift watch found changes made outside Terraform (user, 10-03): Restore applies the source of truth directly
    # (no Dev or Quinn steps); "request changes" keeps them (a change request / a ticket); Accept leaves it for now
    "drift": {"agent": "tp", "redo": "tp.drift_keep", "next": "tp.restore", "next_agent": "tp",
              "next_label": "Terra restores AWS from the Terraform state and Dev's packages: exactly the restore plan, nothing else"},
    # an infrastructure change Orion won't OK on his own (affects code, a requirement change, a risk): the user decides
    "change_review": {"agent": "cto", "redo": "cto.review", "next": "cto.change_go", "next_agent": "cto",
                      "next_label": "Orion routes it: Terra changes the infrastructure (Dev adapts the code if needed), or Echo updates the requirement"},
    # older projects (before infrastructure-first): component QA, then a separate access gate
    "qa_bugs": {"agent": "qa", "redo": "qa.test", "next": "de.code", "next_agent": "de",
                "next_label": "Dev fixes the bugs in a new version, then Quinn retests"},
    "qa": {"agent": "qa", "redo": "qa.test", "next": "cto.access", "next_agent": "cto",
           "next_label": "Orion drafts each agent's AWS access for this project"},
    "access": {"agent": "cto", "redo": "cto.access", "next": "cto.grant", "next_agent": "cto",
               "next_label": "Orion creates the roles, then Terra plans the deploy"},
}


PIPELINE = [  # build order after the plan: (agent, job, what it does, the file that proves its work exists)
    ("ba", "ba.talk", "Atlas (BA/DA) writes the data mapping", "mapping/01_data_mapping.json"),  # *.talk: a finished talk
    ("ta", "ta.talk", "Archie (TA) designs the HLD, LLD and architecture diagram", "diagrams/design.json"),  # goes straight
    ("tp", "tp.talk", "Terra (TP) writes the infrastructure and creates it in AWS", "infra/plan_preview.json"),  # to the work
    ("de", "de.talk", "Dev (DE) writes, tests and deploys the code", "reports/code.json"),
    ("qa", "qa.live", "Quinn (QA) tests the live flow in AWS", "reports/live_qa.json"),
]


def plan_route(project_id: str, plan: dict | None) -> tuple[dict, list[str]]:
    """Where an approved plan hands over, and who is skipped. A change request's re-plan lists the agents whose work it
    changes (`rerun`); everyone before the first of them keeps their approved work (it carries into the new version).
    An agent whose work doesn't exist yet is never skipped."""
    rerun = (plan or {}).get("rerun")
    if not rerun:
        return STAGES["plan"], []
    store = ProjectStore(project_id)
    skipped: list[str] = []
    for agent, job, label, proof in PIPELINE:
        try:
            done = bool(store.read(proof))
        except Exception:  # noqa: BLE001  (missing file)
            done = False
        if agent in rerun or not done:
            return {**STAGES["plan"], "next": job, "next_agent": agent, "next_label": label}, skipped
        skipped.append(agent)
    return {**STAGES["plan"], "next": None, "next_agent": None, "next_label": "Nothing to redo: no agent's work changes"}, skipped


async def continue_pipeline(project_id: str) -> str | None:
    """A project that paused because the next agent wasn't built yet: start that agent now (with Orion's hand-off).
    Returns the job kind started, or None if nothing is waiting."""
    from app.db.models import Job
    from app.orchestrator import runner as runner_mod

    async with SessionLocal() as db:
        last = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.status == "approved")
                                 .order_by(Approval.decided_at.desc()))).scalars().first()
        if not last or last.stage not in STAGES:
            return None
        stage = STAGES[last.stage]
        if last.stage == "plan":
            intake = await db.get(Intake, project_id)
            stage = plan_route(project_id, intake.plan if intake else None)[0]
        if not stage["next"]:
            return None
        if (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.status == "pending"))).scalars().first():
            return None
        kind = stage["next"]
        ran = (await db.execute(select(Job).where(Job.project_id == project_id, Job.kind == kind,
                                                  Job.created_at >= last.decided_at))).scalars().first()
        if ran:
            return None
    await _handoff(project_id, last, stage, last.comment or "")
    await runner_mod.runner.enqueue(kind, project_id=project_id, approved_comment=last.comment or "")
    return kind


async def request_approval(project_id: str, stage: str, title: str, summary: str, artifacts: list[str]) -> Approval:
    async with SessionLocal() as db:
        for old in (await db.execute(select(Approval).where(
                Approval.project_id == project_id, Approval.stage == stage, Approval.status == "pending"))).scalars():
            old.status = "superseded"
        a = Approval(project_id=project_id, stage=stage, agent=STAGES[stage]["agent"], title=title,
                     summary=summary, artifacts=artifacts)
        db.add(a)
        p = await db.get(Project, project_id)
        p.status, p.last_activity = "waiting", f"Waiting for your approval: {title}"
        await db.commit()
        await db.refresh(a)
        owner = p.owner_id
    await bus.publish("approval.requested", project_id=project_id, user_id=owner, agent=a.agent,
                      message=f"Approval needed: {title}", data={"approval_id": a.id, "stage": stage})
    await crewchat.say(project_id, a.agent, "user", f"{title} is ready for your review. Approve it, or raise a change request.",
                       "question", files=artifacts)
    return a


async def _set_agent(project_id: str, agent: str, status: str, activity: str) -> None:
    async with SessionLocal() as db:
        row = (await db.execute(select(AgentState).where(AgentState.project_id == project_id,
                                                         AgentState.agent == agent))).scalar_one_or_none()
        if row:
            row.status, row.activity = status, activity
            if status == "done":
                row.ended_at = utcnow()
            await db.commit()
    await bus.publish("agent.state", project_id=project_id, agent=agent, message=activity, data={"status": status})


async def _handoff(project_id: str, a: Approval, stage: dict, comment: str) -> None:
    """After an approval Orion briefs the next agent: what was approved, which version and files to use, its task."""
    version = ProjectStore(project_id).manifest()["current_version"]
    if stage.get("final"):
        await crewchat.say(project_id, "cto", "crew", f"🎉 The user approved {a.title}" + (f" (“{comment}”)" if comment else "")
                           + f". {version} is live in AWS and tested end to end. Thanks, crew.", "decision", version=version)
        return
    if stage["next_agent"] == "cto":  # Orion's own next step: no brief to himself
        await crewchat.say(project_id, "cto", "crew", f"The user approved {a.title}. Next: {stage['next_label']}.", "handoff",
                           files=a.artifacts, version=version)
        return
    async with SessionLocal() as db:
        intake = await db.get(Intake, project_id)
        plan = (intake.plan if intake else None) or {}
    nxt = stage["next_agent"]
    name = crewchat.NAMES.get(nxt, nxt)
    task = next((s["task"] for s in plan.get("steps", []) if s["agent"] == nxt), "")
    files = ", ".join(a.artifacts or [])
    brief = (f"{name}, the user approved {a.title}" + (f" (comment: “{comment}”)" if comment else "") + f". Use requirement "
             f"**{version}** and the approved files: {files}." + (f" Your task from the plan: {task}" if task else ""))
    if stage["next"]:
        # the next agent starts right away and answers with its own "starting" message
        await crewchat.say(project_id, "cto", nxt, brief, "handoff", files=a.artifacts, version=version)
    else:
        await crewchat.say(project_id, "cto", "crew", brief + f" Note: {name} isn't part of this build yet, so the project "
                           "pauses here. Everything he needs is approved and saved.", "handoff", files=a.artifacts, version=version)
        await _set_agent(project_id, nxt, "waiting", "Not built yet: arrives in the next build. Everything is ready for him")


# "Accept: nothing to rebuild" (user, 10-02: a reworded diagram label shouldn't send Terra, Dev and Quinn round again):
# for these gates, once the next agent's work already exists, you can accept the revision without redoing anything after it
ACCEPT_ONLY = {"plan": "ba", "mapping": "ta", "design": "tp"}


def accept_allowed(project_id: str, stage: str) -> bool:
    if stage == "drift":  # "Leave it for now": AWS keeps the changes, nothing is restored or changed
        return True
    nxt = ACCEPT_ONLY.get(stage)
    proof = next((p for agent, _job, _label, p in PIPELINE if agent == nxt), None)
    if not proof:
        return False
    try:
        return bool(ProjectStore(project_id).read(proof))
    except Exception:  # noqa: BLE001 (not built yet)
        return False


async def decide(project_id: str, approval_id: str, decision: str, comment: str) -> Approval:
    from app.orchestrator import runner as runner_mod

    accept = decision == "accept"
    async with SessionLocal() as db:
        a = await db.get(Approval, approval_id)
        if not a or a.project_id != project_id:
            raise LookupError("Approval not found")
        if a.status != "pending":
            raise ValueError(f"Already {a.status.replace('_', ' ')}")
        if accept and not accept_allowed(project_id, a.stage):
            raise ValueError("Nothing after this step is built yet, so there's nothing to keep: approve it to continue")
        a.status = "approved" if decision in ("approve", "accept") else "changes_requested"
        a.comment, a.decided_at = (comment or None), utcnow()
        p = await db.get(Project, project_id)
        owner = p.owner_id
        stage, skipped = STAGES[a.stage], []
        if a.stage == "plan":  # a change request's re-plan may skip agents whose work it doesn't touch
            intake = await db.get(Intake, project_id)
            stage, skipped = plan_route(project_id, intake.plan if intake else None)
        if decision in ("approve", "accept"):
            if accept:  # back to where the project was: done if its testing was signed off, else waiting
                final = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.stage == "live",
                                                                 Approval.status == "approved"))).scalars().first()
                p.status = "completed" if final else "waiting"
                p.last_activity = f"Accepted: {a.title}. Nothing after it is redone"
            else:
                p.status = "running" if stage["next"] else "completed" if stage.get("final") else "waiting"
                p.last_activity = f"Approved: {a.title}. Next: {stage['next_label']}" if not stage.get("final") else stage["next_label"]
            if stage.get("final"):
                p.progress = 1.0
            # a change request is complete once its re-plan / revised artifact is approved; an infrastructure one once
            # the user has checked the result in AWS
            from app.db.models import ChangeRequest
            for cr in (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id,
                                                                    ChangeRequest.status.in_(["planning", "in_progress"])))).scalars():
                if cr.route == "infra" and a.stage not in ("infra_check",):
                    continue
                cr.status = "done"
        else:
            p.status, p.last_activity = "running", f"Changes requested on {a.title}"
        await db.commit()
        await db.refresh(a)

    if decision in ("approve", "accept"):
        await bus.publish("approval.approved", project_id=project_id, user_id=owner, agent=a.agent,
                          message=f"You {'accepted' if accept else 'approved'}: {a.title}" + (f" ({comment})" if comment else ""),
                          data={"stage": a.stage, "accept_only": accept})
        if accept and a.stage == "drift":  # left as it is: the watch raises it again only if AWS changes further
            from app.services import aws_access

            rep = aws_access.load(project_id, "drift") or {}
            aws_access.save(project_id, {**rep, "status": "left", "left_at": a.decided_at.strftime("%Y-%m-%dT%H:%M:%SZ")}, "drift")
        await crewchat.say(project_id, "user", "crew", (f"Leave it for now: {a.title}. AWS keeps those changes; Terra tells me if anything else changes"
                                                        if accept and a.stage == "drift" else
                                                        f"Accepted: {a.title}. Nothing to rebuild: the work after it stays as it is"
                                                        if accept else f"Approved: {a.title}") + (f". “{comment}”" if comment else ""), "decision")
        await _set_agent(project_id, a.agent, "done", f"{'Accepted' if accept else 'Approved'} by you: {a.title}" + (" (nothing to rebuild)" if accept else ""))
        try:  # the agent's sign-off document (and stage side effects, e.g. the test plan becomes "approved")
            from app.services import signoff
            from app.services import tickets as tk

            await signoff.on_approved(project_id, a)
            # tasks you gave an agent through a ticket: its change request is done now → comment and close the ticket
            await tk.close_for_crs(project_id, a.agent, f"Done: “{a.title}” approved by you on {a.decided_at:%Y-%m-%d %H:%M} UTC.")
        except Exception as exc:  # noqa: BLE001 (a document must never block the flow)
            import logging

            logging.getLogger(__name__).warning("sign-off for %s/%s failed: %s", project_id, a.stage, exc)
        if accept or not stage["next"]:  # nobody starts after this: tasks that had to wait get going now
            from app.services import tickets as tk

            await tk.resume_queued(project_id)
        if accept:
            return a
        if skipped:
            version = ProjectStore(project_id).manifest()["current_version"]
            names = ", ".join(crewchat.NAMES.get(k, k) for k in skipped)
            await crewchat.say(project_id, "cto", "crew", f"This change doesn't touch {names}'s work, so I'm skipping "
                               f"{'them' if len(skipped) > 1 else 'that step'}: the approved work carries over to {version} unchanged. "
                               f"Next: {stage['next_label']}.", "decision", version=version)
            for k in skipped:
                await _set_agent(project_id, k, "done", f"No change needed: approved work carried over to {version}")
        await _handoff(project_id, a, stage, comment or "")
        if stage["next"]:
            await runner_mod.runner.enqueue(stage["next"], project_id=project_id, approved_comment=comment or "")
    else:
        await bus.publish("approval.changes", project_id=project_id, user_id=owner, agent=a.agent,
                          message=f"You asked for changes on {a.title}: {comment}", data={"stage": a.stage})
        await crewchat.say(project_id, "user", a.agent, f"Changes requested on {a.title}: {comment}", "decision")
        await runner_mod.runner.enqueue(stage["redo"], project_id=project_id, feedback=comment)
    return a


AGENT_OF_JOB = {"intake": "intake", "cto": "cto", "ba": "ba", "ta": "ta", "tp": "tp", "de": "de", "qa": "qa"}


async def reconcile(project_id: str | None = None) -> int:
    """Self-heal agent cards (runs at startup): an agent still showing "needs you" with no pending approval is done
    (approved earlier); an agent still showing "working" with no queued/running job was interrupted (a server restart):
    it says so, with Try again, instead of a clock that runs forever."""
    from app.db.models import Job

    fixed = 0
    async with SessionLocal() as db:
        q = select(AgentState).where(AgentState.status == "working")
        if project_id:
            q = q.where(AgentState.project_id == project_id)
        for st in (await db.execute(q)).scalars().all():
            live = (await db.execute(select(Job).where(Job.project_id == st.project_id, Job.status.in_(["queued", "running"]),
                                                       Job.kind.like(f"{st.agent}.%")))).scalars().first()
            if not live:
                st.status, st.activity = "failed", "Interrupted by a platform update before finishing: press Try again"
                fixed += 1
        await db.commit()
    from app.db.models import PhaseTalk

    async with SessionLocal() as db:
        # a kickoff reply a restart cut off: no more "thinking…" forever (the crew watch runs the reply again)
        q = select(PhaseTalk).where(PhaseTalk.busy.is_not(None))
        if project_id:
            q = q.where(PhaseTalk.project_id == project_id)
        for t in (await db.execute(q)).scalars().all():
            live = (await db.execute(select(Job).where(Job.project_id == t.project_id, Job.status.in_(["queued", "running"]),
                                                       Job.kind.like(f"{t.agent}.talk%")))).scalars().first()
            if not live:
                t.busy, t.draft = None, None
                t.last_error = {"kind": f"{t.agent}.talk_reply", "message": "Interrupted by a platform update before replying: press Try again.",
                                "at": utcnow().isoformat()}
                fixed += 1
        await db.commit()
    async with SessionLocal() as db:
        q = select(AgentState).where(AgentState.status == "needs_approval", AgentState.agent != "intake")
        if project_id:
            q = q.where(AgentState.project_id == project_id)
        for st in (await db.execute(q)).scalars().all():
            pending = (await db.execute(select(Approval).where(Approval.project_id == st.project_id, Approval.agent == st.agent,
                                                               Approval.status == "pending"))).scalars().first()
            talking = await db.get(PhaseTalk, (st.project_id, st.agent))
            if not pending and not (talking and talking.status == "talking"):  # waiting for your answers is still "needs you"
                last = (await db.execute(select(Approval).where(Approval.project_id == st.project_id, Approval.agent == st.agent,
                                                                Approval.status == "approved")
                                         .order_by(Approval.decided_at.desc()))).scalars().first()
                if last:
                    st.status, st.activity = "done", f"Approved by you: {last.title}"
                    fixed += 1
        await db.commit()
    return fixed
