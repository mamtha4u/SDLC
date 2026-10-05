"""Tickets: how the crew (and the user) hand bugs and tasks to each other, like a real team's board.

  Quinn's live test fails ─▶ TKT-00N (open) ─▶ assigned to Dev (code) or Terra (infrastructure)
  the fixer starts ─▶ in_progress ─▶ fixes, deploys, comments ─▶ resolved, assigned back to Quinn
  Quinn retests ─▶ closed, or reopened (back to the fixer, with what still fails)

People can open tickets, comment and re-assign them to any agent. Every step is a line in the ticket's history (who,
what, when), and the crew room hears about it. Tickets for Atlas, Archie or Echo become change requests (their work
changes through a CR, with a re-plan); Orion's tickets are triaged like a change request too.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db.base import SessionLocal, utcnow
from app.db.models import Project, Ticket, TicketComment
from app.orchestrator import crewchat
from app.orchestrator.bus import bus

FIXER = {"code": "de", "infra": "tp"}       # who fixes what
ACTIVE = ("open", "reopened", "in_progress")  # still with the fixer
AGENTS = ("cto", "ba", "ta", "tp", "de", "qa", "intake")
SEVERITIES = ("critical", "major", "minor")
AREAS = ("code", "infra", "design", "other")
STATUSES = ("open", "in_progress", "resolved", "closed", "reopened")


class TicketError(ValueError):
    pass


def label(t: Ticket) -> str:
    return f"TKT-{t.number:03d}"


def name(who: str) -> str:
    return "You" if who == "user" else crewchat.NAMES.get(who, who)


def out(t: Ticket, comments: list[TicketComment] | None = None) -> dict:
    d = {"id": t.id, "label": label(t), "title": t.title, "description": t.description, "steps": t.steps, "expected": t.expected,
         "actual": t.actual, "severity": t.severity, "area": t.area, "status": t.status, "assignee": t.assignee,
         "reporter": t.reporter, "check_id": t.check_id, "version_found": t.version_found, "version_fixed": t.version_fixed,
         "created_at": t.created_at, "updated_at": t.updated_at}
    if comments is not None:
        d["comments"] = [{"id": c.id, "author": c.author, "kind": c.kind, "text": c.text, "created_at": c.created_at} for c in comments]
    return d


async def _publish(project_id: str, t: Ticket, message: str, agent: str | None = None) -> None:
    async with SessionLocal() as db:
        owner = (await db.get(Project, project_id)).owner_id
    await bus.publish("ticket.updated", project_id=project_id, user_id=owner, agent=agent if agent in crewchat.NAMES else None,
                      message=message, data={"ticket": t.id, "label": label(t), "status": t.status, "assignee": t.assignee})


async def create(project_id: str, *, title: str, description: str = "", steps: str = "", expected: str = "", actual: str = "",
                 severity: str = "major", area: str = "code", assignee: str | None = None, reporter: str = "qa",
                 check_id: str | None = None, version: str | None = None, announce: bool = True) -> Ticket:
    if severity not in SEVERITIES:
        severity = "major"
    if area not in AREAS:
        area = "other"
    assignee = assignee or FIXER.get(area, "cto")
    if assignee not in (*AGENTS, "user"):
        raise TicketError(f"Unknown assignee {assignee!r}")
    async with SessionLocal() as db:
        n = (await db.execute(select(func.max(Ticket.number)).where(Ticket.project_id == project_id))).scalar() or 0
        p = await db.get(Project, project_id)
        t = Ticket(project_id=project_id, number=n + 1, title=title.strip()[:300] or "Untitled", description=description,
                   steps=steps, expected=expected, actual=actual, severity=severity, area=area, assignee=assignee,
                   reporter=reporter, check_id=check_id, version_found=version or p.current_version)
        db.add(t)
        await db.flush()
        db.add(TicketComment(ticket_id=t.id, author=reporter, kind="created",
                             text=f"Opened {label(t)} and assigned it to {name(assignee)}."))
        await db.commit()
        await db.refresh(t)
    if announce:
        await crewchat.say(project_id, reporter, assignee if assignee != "user" else "user",
                           f"🎫 **{label(t)}** ({severity}, {area}): {t.title}" + (f". Expected: {expected} Actual: {actual}" if expected else ""),
                           "issue", ticket=t.id)
    await _publish(project_id, t, f"{name(reporter)} opened {label(t)}: {t.title}", reporter)
    return t


async def get(ticket_id: str) -> Ticket | None:
    async with SessionLocal() as db:
        return await db.get(Ticket, ticket_id)


async def by_label(project_id: str, text: str) -> Ticket | None:
    try:
        n = int(text.upper().removeprefix("TKT-"))
    except ValueError:
        return None
    async with SessionLocal() as db:
        return (await db.execute(select(Ticket).where(Ticket.project_id == project_id, Ticket.number == n))).scalar_one_or_none()


async def listing(project_id: str, with_comments: bool = True) -> list[dict]:
    async with SessionLocal() as db:
        rows = (await db.execute(select(Ticket).where(Ticket.project_id == project_id).order_by(Ticket.number))).scalars().all()
        comments: dict[str, list[TicketComment]] = {}
        if with_comments and rows:
            for c in (await db.execute(select(TicketComment).where(TicketComment.ticket_id.in_([t.id for t in rows]))
                                       .order_by(TicketComment.id))).scalars():
                comments.setdefault(c.ticket_id, []).append(c)
        return [out(t, comments.get(t.id, []) if with_comments else None) for t in rows]


async def detail(ticket_id: str) -> dict | None:
    async with SessionLocal() as db:
        t = await db.get(Ticket, ticket_id)
        if not t:
            return None
        cs = (await db.execute(select(TicketComment).where(TicketComment.ticket_id == ticket_id).order_by(TicketComment.id))).scalars().all()
        return out(t, list(cs))


async def query(project_id: str, *, assignee: str | None = None, statuses: tuple[str, ...] = ACTIVE) -> list[Ticket]:
    async with SessionLocal() as db:
        q = select(Ticket).where(Ticket.project_id == project_id, Ticket.status.in_(statuses))
        if assignee:
            q = q.where(Ticket.assignee == assignee)
        return list((await db.execute(q.order_by(Ticket.number))).scalars().all())


async def change(ticket_id: str, by: str, note: str = "", *, status: str | None = None, assignee: str | None = None,
                 version_fixed: str | None = None, kind: str | None = None, say: bool = True, **fields) -> Ticket:
    """Move a ticket (status and/or assignee) and record it in its history with an optional note."""
    async with SessionLocal() as db:
        t = await db.get(Ticket, ticket_id)
        if not t:
            raise TicketError("Ticket not found")
        lines = []
        if status and status != t.status:
            if status not in STATUSES:
                raise TicketError(f"Unknown status {status!r}")
            lines.append(f"{t.status.replace('_', ' ')} → {status.replace('_', ' ')}")
            t.status = status
        if assignee and assignee != t.assignee:
            if assignee not in (*AGENTS, "user"):
                raise TicketError(f"Unknown assignee {assignee!r}")
            lines.append(f"assigned to {name(assignee)}")
            t.assignee = assignee
        if version_fixed:
            t.version_fixed = version_fixed
        for k, v in fields.items():
            if k in ("title", "description", "steps", "expected", "actual", "severity", "area") and v is not None:
                setattr(t, k, v)
        t.updated_at = utcnow()
        text = "; ".join(lines)
        if note:
            text = f"{text}. {note}" if text else note
        if text:
            db.add(TicketComment(ticket_id=t.id, author=by, kind=kind or ("assign" if assignee and not status else "status" if lines else "comment"),
                                 text=text))
        await db.commit()
        await db.refresh(t)
    if say and text:
        to = t.assignee if t.assignee not in (by, "user") else "crew"
        await crewchat.say(t.project_id, by, to, f"🎫 {label(t)}: {text}", "fix" if t.status in ("resolved", "closed") else "update", ticket=t.id)
    await _publish(t.project_id, t, f"{name(by)} · {label(t)}: {text or 'updated'}", by)
    return t


async def comment(ticket_id: str, by: str, text: str) -> Ticket:
    return await change(ticket_id, by, text.strip(), kind="comment")


VIA_CR = ("cto", "ba", "ta", "intake")  # their work changes through a change request
WORKERS = ("de", "tp", "qa")


async def resume_queued(project_id: str) -> list[str]:
    """Tasks that had to wait get going once the crew is free (called after an approval that starts nobody, a
    documentation-only design update, and Echo's sign-off). For Atlas / Archie / Echo / Orion, a ticket still "open"
    (no change request yet) becomes one when the requirement is signed off and Echo is free. For Dev / Terra / Quinn, open
    tickets are handed over (`cto.tickets`) when no job runs and nothing waits for your decision. Returns what it did."""
    from app.db.models import Approval, Intake, Job
    from app.orchestrator import changes
    from app.orchestrator import runner as runner_mod

    done: list[str] = []
    rows = await listing(project_id, with_comments=False)
    async with SessionLocal() as db:
        intake = await db.get(Intake, project_id)
        busy = (await db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"])))).scalars().first()
        pending = (await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.status == "pending"))).scalars().first()
    if intake and intake.status == "signed_off" and not intake.busy and not busy:
        for t in rows:
            if t["assignee"] in VIA_CR and t["status"] in ("open", "reopened"):
                text = f"{t['label']}: {t['title']}" + (f"\n\n{t['description']}" if t["description"] else "")
                cr = await changes.create(project_id, text, [], source=f"ticket:{t['label']}")
                await change(t["id"], "cto", f"Turned into {changes.label(cr)}: I'm triaging it (it changes {name(t['assignee'])}'s work).",
                             status="in_progress")
                done.append(f"{t['label']} → {changes.label(cr)}")
                break  # one change request at a time: the next one after this is done
    if not busy and not pending and not done and any(t["assignee"] in WORKERS and t["status"] in (*ACTIVE, "resolved") for t in rows):
        await runner_mod.runner.enqueue("cto.tickets", project_id=project_id)
        done.append("cto.tickets")
    return done


async def close_for_crs(project_id: str, by: str | None = None, note: str = "") -> list[str]:
    """A task you gave Atlas, Archie, Echo or Orion became a change request (source "ticket:TKT-00N"). When that change
    request is done, the ticket gets the agent's closing comment and is closed (user, 10-02)."""
    from app.db.models import ChangeRequest

    async with SessionLocal() as db:
        crs = (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id, ChangeRequest.status == "done",
                                                            ChangeRequest.source.like("ticket:%")))).scalars().all()
    closed = []
    for cr in crs:
        t = await by_label(project_id, cr.source.split(":", 1)[1])
        if t and t.status != "closed":
            await change(t.id, by or t.assignee, f"{note or 'Done.'} (CR-{cr.number:03d})", status="closed")
            closed.append(label(t))
    return closed


def prompt_block(rows: list[dict]) -> str:
    """Tickets with their history, for an agent's prompt."""
    out_ = []
    for t in rows:
        hist = "\n".join(f"  - {name(c['author'])} ({c['kind']}): {c['text'][:600]}" for c in t.get("comments", [])[-8:])
        out_.append(f"- **{t['label']}** ({t['severity']}, {t['area']}, {t['status']}) {t['title']}"
                    + (f"\n  Check: {t['check_id']}" if t.get("check_id") else "")
                    + (f"\n  Description: {t['description'][:1500]}" if t.get("description") else "")
                    + (f"\n  Steps: {t['steps'][:1500]}" if t.get("steps") else "")
                    + (f"\n  Expected: {t['expected'][:800]}" if t.get("expected") else "")
                    + (f"\n  Actual: {t['actual'][:1500]}" if t.get("actual") else "")
                    + (f"\n  History:\n{hist}" if hist else ""))
    return "\n".join(out_)


async def rows_for(project_id: str, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    return [t for t in await listing(project_id) if t["id"] in ids]
