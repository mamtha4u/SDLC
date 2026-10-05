"""Change requests: the single door for anything new after the requirement is signed off.

  user (text + files) ──▶ CR-00N ──▶ Orion triage ──┬─ requirement ─▶ Echo amends in v<minor+1> ─▶ sign-off ─▶ Orion re-plans
                                                    ├─ plan ────────▶ Orion revises the plan
                                                    └─ mapping ─────▶ Atlas revises the mapping
Every route ends at an approval gate; affected agents are told which requirement version to use.
"""
from __future__ import annotations

import difflib

from sqlalchemy import func, select

from app.db.base import SessionLocal, utcnow
from app.db.models import AgentState, Approval, ChangeRequest, Intake, Project
from app.orchestrator import crewchat
from app.orchestrator.bus import bus
from app.services.storage import ProjectStore

AGENT_NAMES = {"intake": "Echo", "cto": "Orion", "ba": "Atlas", "ta": "Archie", "tp": "Terra", "de": "Dev", "qa": "Quinn"}


def label(cr: ChangeRequest) -> str:
    return f"CR-{cr.number:03d}"


def out(cr: ChangeRequest) -> dict:
    return {"id": cr.id, "label": label(cr), "text": cr.text, "attachments": cr.attachments or [], "source": cr.source,
            "status": cr.status, "route": cr.route, "triage": cr.triage, "version_from": cr.version_from,
            "version_to": cr.version_to, "diff": cr.diff, "created_at": cr.created_at, "updated_at": cr.updated_at}


INFRA_TRIAGE = {"route": "infra", "reason": "An infrastructure change: it goes straight to Orion and Terra (no new requirement "
                "version, nobody else's work changes).", "affected_agents": [{"agent": "tp", "why": "owns the infrastructure"}],
                "needs_from_user": [], "direct": True}


async def create(project_id: str, text: str, attachments: list[str], source: str = "user", route: str | None = None,
                 pending: dict | None = None) -> ChangeRequest:
    """`route="infra"` (the AWS page, the infrastructure gates): Orion's impact review first (cto.review), not the
    general triage. `pending`: what the AWS page validated but hasn't applied (renames, plan_only, the edits)."""
    from app.orchestrator import runner as runner_mod

    async with SessionLocal() as db:
        n = (await db.execute(select(func.count()).select_from(ChangeRequest)
                              .where(ChangeRequest.project_id == project_id))).scalar() or 0
        cr = ChangeRequest(project_id=project_id, number=n + 1, text=text, attachments=attachments, source=source,
                           version_from=(await db.get(Project, project_id)).current_version)
        if route == "infra":
            cr.route, cr.status = "infra", "triage"
            cr.triage = {**INFRA_TRIAGE, "summary": text.splitlines()[0][:300], "brief": text, "pending": pending or {}}
        db.add(cr)
        # Whatever was waiting for approval will be redone after this change: retire it.
        for a in (await db.execute(select(Approval).where(Approval.project_id == project_id,
                                                          Approval.status == "pending"))).scalars():
            a.status = "superseded"
        p = await db.get(Project, project_id)
        p.status, p.last_activity = "running", (f"{label(cr)}: Orion is reviewing the impact" if route == "infra"
                                                else f"{label(cr)} raised. Orion is triaging it")
        await db.commit()
        await db.refresh(cr)
        owner = p.owner_id
    await bus.publish("change.created", project_id=project_id, user_id=owner, agent="cto",
                      message=f"You raised {label(cr)}: {text[:300]}", data={"cr": cr.id, "attachments": attachments})
    await crewchat.say(project_id, "user", "cto", f"{label(cr)}: {text}", "request", cr=cr.id, files=attachments)
    if route == "infra":  # every infrastructure change is reviewed by Orion before anyone touches anything
        await crewchat.say(project_id, "cto", "crew", f"{label(cr)} changes the live infrastructure. Reviewing the impact first: "
                           "what it touches (code, layers, the requirement), the risks and who must act.", "work", cr=cr.id)
        await runner_mod.runner.enqueue("cto.review", project_id=project_id, cr_id=cr.id)
        return cr
    await runner_mod.runner.enqueue("cto.triage", project_id=project_id, cr_id=cr.id)
    return cr


async def update(cr_id: str, **fields) -> ChangeRequest:
    async with SessionLocal() as db:
        cr = await db.get(ChangeRequest, cr_id)
        for k, v in fields.items():
            setattr(cr, k, v)
        await db.commit()
        await db.refresh(cr)
        return cr


async def get(cr_id: str) -> ChangeRequest | None:
    async with SessionLocal() as db:
        return await db.get(ChangeRequest, cr_id)


async def inform_agents(project_id: str, cr: ChangeRequest, agents: list[str], note: str,
                        whys: dict[str, str] | None = None, version: str | None = None) -> None:
    """Tell each affected agent (on its card, in the feed and in the crew room) that the ground under it changed."""
    async with SessionLocal() as db:
        for key in agents:
            st = (await db.execute(select(AgentState).where(AgentState.project_id == project_id,
                                                            AgentState.agent == key))).scalar_one_or_none()
            if st and st.status != "working":
                if st.status in ("done", "needs_approval"):
                    st.status = "waiting"
                st.activity = f"{label(cr)}: {note}"[:250]
        p = await db.get(Project, project_id)
        owner = p.owner_id
        await db.commit()
    for key in agents:
        await bus.publish("change.informed", project_id=project_id, user_id=owner, agent=key,
                          message=f"{AGENT_NAMES.get(key, key)} informed: {label(cr)}. {note}", data={"cr": cr.id})
        why = (whys or {}).get(key)
        await crewchat.say(project_id, "cto", key, f"{AGENT_NAMES.get(key, key)}, {label(cr)} affects you"
                           + (f": {why}" if why else ".") + f" {note[0].upper()}{note[1:]}", "update", cr=cr.id)
        if version:
            await crewchat.ack(project_id, key, version)


async def start_amendment(project_id: str, cr: ChangeRequest, brief: str) -> str:
    """Open a new requirement version (v1 → v1.1) and reopen Echo's interview for this change only."""
    from app.orchestrator import runner as runner_mod

    store = ProjectStore(project_id)
    new_v = store.new_version(f"{label(cr)} opened: {cr.text[:160]}", minor=True)
    async with SessionLocal() as db:
        p = await db.get(Project, project_id)
        p.current_version = new_v
        intake = await db.get(Intake, project_id)
        intake.status, intake.active_cr, intake.busy = "amending", cr.id, "chatting"
        # Orion hands the change to Echo: shown in Echo's chat as Orion's message, not as the user's
        intake.chat = list(intake.chat or []) + [{
            "role": "orion", "ts": utcnow().isoformat(), "cr": cr.id, "label": label(cr), "attachments": cr.attachments,
            "summary": (cr.triage or {}).get("summary", ""), "brief": brief, "version": new_v, "text": cr.text}]
        await db.commit()
    await update(cr.id, status="clarifying", version_to=new_v)
    await crewchat.say(project_id, "cto", "intake", f"Echo, {label(cr)} is yours: it changes the requirement, so it "
                       f"becomes {new_v}. {brief}", "handoff", cr=cr.id, files=cr.attachments)
    await crewchat.ack(project_id, "intake", new_v)
    await runner_mod.runner.enqueue("intake.chat", project_id=project_id, amend=cr.id, brief=brief)
    return new_v


def unified_diff(old: str, new: str, old_v: str, new_v: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=f"00_requirement.md ({old_v})", tofile=f"00_requirement.md ({new_v})", n=2))
