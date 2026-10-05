"""The crew room: a read-only group chat of what the agents tell each other.

Messages are written at real hand-off points (sign-off, plan, approvals, change requests, sandbox runs, failures)
or by an agent's own tool call (e.g. Echo → Orion). Nothing here is invented: every line reflects something the
platform actually did. Projects that existed before the crew room get their history rebuilt once from the
event log (`backfill`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import CrewMessage, Event
from app.orchestrator.bus import bus

NAMES = {"intake": "Echo", "cto": "Orion", "ba": "Atlas", "ta": "Archie", "tp": "Terra", "de": "Dev", "qa": "Quinn",
         "user": "You", "crew": "everyone"}

# How each agent acknowledges "the requirement changed / you're assigned" (the fact is real; the phrasing is persona)
ACK = {
    "intake": "On it, Orion. I'll go through it with the user.",
    "ba": "Copy that. I'll map from {v} when it's my turn.",
    "ta": "Noted. My HLD and LLD will follow {v}.",
    "tp": "Noted. I'll build the infrastructure from {v}.",
    "de": "Got it. I'll code against {v}.",
    "qa": "Noted. My test cases will follow {v}.",
}


def out(m: CrewMessage) -> dict:
    return {"id": m.id, "sender": m.sender, "to": m.to, "kind": m.kind, "text": m.text, "data": m.data or {},
            "created_at": m.created_at.isoformat()}


async def say(project_id: str, sender: str, to: str, text: str, kind: str = "update", **data) -> None:
    """Post one line to the crew room (and push it live)."""
    async with SessionLocal() as db:
        m = CrewMessage(project_id=project_id, sender=sender, to=to, kind=kind, text=text.strip()[:4000], data=data)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        payload = out(m)
    await bus.publish("crew.message", project_id=project_id, agent=sender if sender in NAMES else None,
                      message=text[:300], data=payload, persist=False)


QUIET_TOOLS = {"capture", "message_orion", "reply", "submit_triage", "submit_review", "run_tests",  # these post their own lines
               "http_request", "sqs_receive", "sqs_send", "invoke_lambda"}


def describe(tool: str, args: dict, out=None) -> str | None:
    """One human line for a tool call (and its result when known), for the crew room. None = don't post."""
    if tool in QUIET_TOOLS:
        return None
    if tool == "web_search":
        line = f"🔎 Searched the web: “{args.get('query', '')}”"
        if isinstance(out, list):
            tops = "; ".join(str(r.get("title", ""))[:70] for r in out[:2] if isinstance(r, dict))
            line += f" → {len(out)} result(s)" + (f". Top: {tops}" if tops else "")
        return line
    if tool == "fetch_url":
        if isinstance(out, dict):
            return f"📖 Read {out.get('url', args.get('url'))} (HTTP {out.get('status')})" + (f": “{str(out.get('title'))[:90]}”" if out.get("title") else "")
        return f"📖 Read {args.get('url', '')}"
    if tool == "pypi_package":
        line = f"📦 Checked PyPI: {args.get('package')} on Python {args.get('python')}"
        if isinstance(out, dict):
            line += f" → {out.get('verdict', 'not found' if out.get('found') is False else 'checked')}"
        return line
    if tool == "run_transform":
        return f"🧪 Ran mapping code in the sandbox on {len(args.get('cases', []))} message(s) (earlier Atlas version)"
    if tool == "submit_plan":
        return (f"📤 Submitted my plan: {len(args.get('steps', []))} steps, {len(args.get('research', []))} verified facts, "
                f"{len(args.get('risks', []))} risks")
    if tool == "submit_mapping":
        return f"📤 Submitted the mapping: {len(args.get('rows', []))} rows, {len(args.get('samples', []))} worked examples"
    if tool == "submit_design":
        a = args.get("architecture", {})
        shapes = sum(len(a.get(k, [])) for k in ("sources", "path", "destinations", "support"))
        return f"📤 Submitted the design: HLD, LLD, a diagram with {shapes} components, {len(args.get('resources', []))} resources"
    if tool == "submit_infra":
        return f"📤 Submitted the Terraform: {len(args.get('resources', []))} resources"
    if tool == "write_files":
        paths = [str(f.get("path", "")).removeprefix("infra/") for f in args.get("files", []) if isinstance(f, dict)]
        gone = [str(p).removeprefix("infra/") for p in args.get("delete", [])]
        return "✍️ Wrote " + (", ".join(paths) or "nothing") + (f" · removed {', '.join(gone)}" if gone else "")
    if tool == "submit_code":
        n = len(args.get("files", []))
        return "📤 Submitted the code" + (f" ({n} file(s) changed in this step)" if n else "")
    if tool == "submit_qa":
        return f"📤 Submitted QA: {len(args.get('plan', []))} planned tests, {len(args.get('bugs', []))} bug(s)"
    if tool == "read_logs":
        return f"📜 Read the logs of {args.get('log_group', '')}" + (f" ({str(out).count(chr(10)) + 1} line(s))" if isinstance(out, str) else "")
    if tool == "submit_live":
        checks = args.get("checks", [])
        return (f"📤 Submitted live QA: {sum(1 for c in checks if c.get('passed'))}/{len(checks)} checks passed, "
                f"{len(args.get('bugs', []))} new ticket(s), {len(args.get('tickets', []))} ticket(s) retested")
    if tool == "submit_sanity":
        return ("✅ Sanity check passed" if args.get("passed") else "❌ Sanity check failed") + f": {str(args.get('summary', ''))[:200]}"
    if tool == "submit_code_review":
        must = sum(1 for f in args.get("findings", []) if f.get("severity") == "must")
        return (f"🔍 Code review: {'approved' if args.get('verdict') == 'approve' else 'changes needed'}"
                f" · {len(args.get('checks', []))} checks · {must} must-fix")
    if tool == "submit_test_plan":
        cases = args.get("cases", [])
        return f"📝 Submitted the test plan: {len(cases)} scenarios ({sum(1 for c in cases if c.get('priority') == 'high')} high priority)"
    import json as _json

    return f"🔧 {tool}: {_json.dumps(args, ensure_ascii=False)[:220]}"


async def ack(project_id: str, agent: str, version: str) -> None:
    if agent in ACK:
        await say(project_id, agent, "cto", ACK[agent].format(v=version), "ack")


async def history(project_id: str, after: int = 0, limit: int = 2000) -> list[dict]:
    """In time order (rebuilt history is inserted later than it happened, so id order isn't time order)."""
    async with SessionLocal() as db:
        rows = (await db.execute(select(CrewMessage).where(CrewMessage.project_id == project_id, CrewMessage.id > after)
                                 .order_by(CrewMessage.created_at, CrewMessage.id))).scalars().all()
        return [out(m) for m in rows[-limit:]]


BACKFILL_VERSION = 2


def _ts(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _jsonl(project_id: str, name: str) -> list[dict]:
    from app.services.storage import ProjectStore

    path = ProjectStore(project_id).root / "logs" / f"{name}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


async def backfill(project_id: str) -> int:
    """Rebuild the crew room for a project that predates it, from everything that was recorded: the event log, Echo's
    conversation and review rounds, every tool call and model turn in the audit logs, Orion's plan (research, steps,
    risks), change-request triage and the approvals. Runs once per backfill version; it only covers the time before
    the first live crew message, so nothing is duplicated."""
    from app.db.models import Approval, ChangeRequest, Intake

    async with SessionLocal() as db:
        existing = (await db.execute(select(CrewMessage).where(CrewMessage.project_id == project_id))).scalars().all()
        old = [m for m in existing if (m.data or {}).get("backfilled")]
        live = [m for m in existing if not (m.data or {}).get("backfilled")]
        if old and all((m.data or {}).get("backfilled") == BACKFILL_VERSION for m in old):
            return 0
        if not old and live:
            return 0  # the project began after the crew room existed: its history is already live
        cutoff = min((_ts(m.created_at) for m in live), default=None)
        for m in old:
            await db.delete(m)

        items: list[tuple[datetime, str, str, str, str, dict]] = []

        def add(ts, sender: str, to: str, kind: str, text: str, **data) -> None:
            t = _ts(ts)
            if t and text and (cutoff is None or t < cutoff):
                items.append((t, sender, to, kind, text.strip()[:4000], {"backfilled": BACKFILL_VERSION, **data}))

        events = (await db.execute(select(Event).where(Event.project_id == project_id).order_by(Event.id))).scalars().all()
        intake = await db.get(Intake, project_id)
        crs = {c.id: c for c in (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id))).scalars()}
        approvals = {a.id: a for a in (await db.execute(select(Approval).where(Approval.project_id == project_id))).scalars()}
        plan = (intake.plan if intake else None) or {}
        last_plan_ev = max((e for e in events if e.type == "intake.updated" and e.agent == "cto"), key=lambda e: e.id, default=None)

        # 1. Echo ↔ the user, and Orion's messages in Echo's chat
        for m in (intake.chat if intake else None) or []:
            ts, role = m.get("ts"), m.get("role")
            if role == "user" and not m.get("cr"):
                add(ts, "user", "intake", "chat", m.get("text", ""), files=m.get("attachments") or [])
            elif role == "echo":
                add(ts, "intake", "user", "chat", m.get("text", ""))
            elif role == "note":
                add(ts, "intake", "cto", "update", m.get("text", ""))
            elif role == "orion" or m.get("cr"):
                if m.get("kind") == "reply":
                    add(ts, "cto", "intake", "answer", m.get("text", ""))
                else:
                    body = m.get("text", "").replace("📝 Change request ", "").split("\n\n📎 Attached: ")[0]
                    add(ts, "cto", "intake", "handoff", f"Forwarding {m.get('label', 'a change request')} from the user: “{body}”",
                        files=m.get("attachments") or [])
        # 2. Echo's review rounds
        from app.agents.intake import review_line

        for r in (intake.rounds if intake else None) or []:
            add(r.get("at"), "intake", "user", "update", review_line(r))
        # 3. every tool call and model turn from the audit logs
        for r in _jsonl(project_id, "tools"):
            line = describe(r.get("tool", ""), r.get("input") or {})
            if line and r.get("agent") != "intake":
                add(r.get("ts"), r.get("agent", "cto"), "crew", "work", line)
        for r in _jsonl(project_id, "llm"):
            if r.get("agent") == "intake":
                continue  # Echo's turns are her chat replies (above) or whole documents
            if r.get("retry"):
                add(r.get("ts"), r["agent"], "crew", "issue", f"⚠️ Temporary AWS error ({str(r.get('error', ''))[:160]}). Retrying.")
            elif (r.get("text_preview") or "").strip():
                add(r.get("ts"), r["agent"], "crew", "think", r["text_preview"])
        # 4. the event log: hand-offs, decisions, approvals, change requests
        for ev in events:
            msg, t, a, d = ev.message or "", ev.type, ev.agent, ev.data or {}
            if t == "intake.updated" and a == "intake" and msg.startswith("Requirement signed off"):
                add(ev.created_at, "intake", "cto", "handoff", "The user signed off the requirement. 00_requirement.md is frozen. Over to you, Orion.",
                    files=["00_requirement.md"])
            elif t == "intake.updated" and a == "intake" and msg.startswith("Requirement updated to"):
                add(ev.created_at, "intake", "cto", "handoff", msg + ". The diff is saved with the change request.", files=["00_requirement.md"])
            elif t == "intake.updated" and a == "cto" and msg:
                add(ev.created_at, "cto", "crew", "update", msg + ".", files=["plan.md"])
                if last_plan_ev is not None and ev.id == last_plan_ev.id and plan:
                    for f in plan.get("research", []):
                        add(ev.created_at, "cto", "crew", "update", f"Verified **{f['claim']}** → {f['verdict']}: {f['finding']} (source: {f['source']})")
                    if plan.get("risks"):
                        add(ev.created_at, "cto", "crew", "update", "Risks I'm watching:\n" + "\n".join(
                            f"- **{r['risk']}** → {r['mitigation']}" for r in plan["risks"]))
                    for s in plan.get("steps", []):
                        add(ev.created_at, "cto", s["agent"], "assign", f"{NAMES.get(s['agent'], s['agent'])}, your part: {s['task']}")
            elif t == "approval.requested":
                ap = approvals.get(d.get("approval_id"))
                if ap:
                    add(ev.created_at, ap.agent, "user", "question", f"**{ap.title}** is ready for your review.\n\n{ap.summary}",
                        files=ap.artifacts or [])
            elif t == "approval.approved":
                add(ev.created_at, "user", "crew", "decision", msg)
                if d.get("stage") == "plan":
                    step = next((s["task"] for s in plan.get("steps", []) if s["agent"] == "ba"), "")
                    add(ev.created_at, "cto", "ba", "handoff", "The user approved my plan. Atlas, you're up: write the data mapping."
                        + (f" Your task: {step}" if step else ""))
                    add(ev.created_at, "ba", "cto", "ack", "On it. Reading the signed-off requirement and the samples.")
                elif d.get("stage") == "mapping":
                    add(ev.created_at, "cto", "crew", "handoff", "The user approved Atlas's mapping. Archie (TA) is next with the HLD, LLD and "
                        "diagram. He isn't part of this build yet, so the project pauses here. Everything he needs is approved and saved.",
                        files=["01_data_mapping.md"])
            elif t == "approval.changes":
                add(ev.created_at, "user", a or "crew", "decision", msg)
            elif t == "change.created":
                add(ev.created_at, "user", "cto", "request", msg, files=d.get("attachments") or [])
            elif t == "change.triaged":
                add(ev.created_at, "cto", "crew", "decision", msg)
                cr = crs.get(d.get("cr"))
                if cr and cr.triage:
                    label = f"CR-{cr.number:03d}"
                    if cr.route == "requirement":
                        add(ev.created_at, "cto", "intake", "handoff", f"Echo, {label} is yours. {cr.triage.get('brief', '')}", files=cr.attachments or [])
                        add(ev.created_at, "intake", "cto", "ack", ACK["intake"])
                    for x in cr.triage.get("affected_agents", []):
                        add(ev.created_at, "cto", x["agent"], "update", f"{NAMES.get(x['agent'], x['agent'])}, {label} affects you: {x['why']}")
                        if x["agent"] in ACK:
                            add(ev.created_at, x["agent"], "cto", "ack", ACK[x["agent"]].format(v=cr.version_to or "the new version"))
            elif t == "change.informed" and a and a != "intake" and "now" in msg:
                add(ev.created_at, "cto", a, "update", f"{NAMES.get(a, a)}, " + (msg.split(". ", 1)[-1] if ". " in msg else msg))
            elif t == "mapping.updated" and a == "ba":
                add(ev.created_at, "ba", "cto", "issue" if "problem" in msg else "handoff", msg, files=["01_data_mapping.md"])
            elif t == "job.failed":
                owner = a or {"intake": "intake", "cto": "cto", "ba": "ba"}.get(msg.split(".", 1)[0], "cto")
                add(ev.created_at, owner, "cto", "issue", f"My step failed and stopped: {msg[:300]}")

        items.sort(key=lambda x: x[0])
        db.add_all([CrewMessage(project_id=project_id, sender=s, to=to, kind=k, text=tx, data=dd, created_at=ts)
                    for ts, s, to, k, tx, dd in items])
        await db.commit()
        return len(items)
