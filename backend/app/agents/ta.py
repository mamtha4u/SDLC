"""Archie — Technical Architect.

ta.design: from the signed-off requirement and Atlas's approved mapping, writes 02_hld.md and 03_lld.md in the
organisation's structure and describes the architecture; the platform draws it as an editable .drawio in the team's
layout (services/diagram.py). Opens the "design" approval gate.
payload.feedback → revise; payload.user_diagram → the user edited the diagram in draw.io: their drawing stays as is,
and Archie brings the HLD/LLD in line with it.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.intake import load_intake
from app.orchestrator import crewchat, flow
from app.orchestrator.runner import JobContext, handler
from app.services import diagram
from app.services.storage import ProjectStore, StorageError

ARCH_JSON = "diagrams/architecture.json"
DRAWIO = "diagrams/architecture.drawio"
USER_DRAWIO = "diagrams/architecture.upload.drawio"

NODE = obj({"id": {"type": "string"}, "label": {"type": "string"}, "sub": {"type": "string", "description": "short detail line"},
            "icon": {"type": "string", "enum": diagram.ICONS},
            "details": {"type": "array", "items": {"type": "string"}, "description": "cards for a compute step, else []"}})
SUPPORT_NODE = obj({"id": {"type": "string"}, "label": {"type": "string"}, "sub": {"type": "string"},
                    "icon": {"type": "string", "enum": diagram.ICONS}, "under": {"type": "string", "description": "id of the shape it serves"}})
SUBMIT = Tool(
    name="submit_design",
    terminal=True,
    description="Submit the HLD, the LLD and the architecture. Call it exactly once; fix and resubmit if rejected.",
    schema=obj({
        "summary": {"type": "string", "description": "2-3 sentences: the design in plain words"},
        "hld_markdown": {"type": "string", "description": "The complete 02_hld.md, or exactly UNCHANGED to keep the previous one"},
        "lld_markdown": {"type": "string", "description": "The complete 03_lld.md, or exactly UNCHANGED to keep the previous one"},
        "architecture": obj({
            "title": {"type": "string"}, "subtitle": {"type": "string"},
            "network": {"type": "string", "description": "One line on networking, shown in the diagram: e.g. 'No VPC: the Lambda "
                        "isn't attached to one (AWS-managed network); nothing private to reach' or the VPC/subnets used"},
            "sources": {"type": "array", "items": NODE}, "path": {"type": "array", "items": NODE},
            "destinations": {"type": "array", "items": NODE}, "support": {"type": "array", "items": SUPPORT_NODE},
            "edges": {"type": "array", "items": obj({"from": {"type": "string"}, "to": {"type": "string"},
                                                    "label": {"type": "string"}, "kind": {"type": "string", "enum": ["data", "error", "support"]}})},
        }),
        "decisions": {"type": "array", "items": obj({"decision": {"type": "string"}, "why": {"type": "string"},
                                                     "alternatives": {"type": "string"}})},
        "resources": {"type": "array", "description": "Every AWS resource Terra must create",
                      "items": obj({"name": {"type": "string"}, "type": {"type": "string", "description": "e.g. aws_lambda_function"},
                                    "purpose": {"type": "string"}, "key_settings": {"type": "string"}})},
        "open_points": {"type": "array", "items": {"type": "string"}},
        "quality_gates": obj({
            "min_coverage_percent": {"type": "number", "description": "Dev's unit tests must cover at least this % of lines "
                                     "(the requirement's number if it states one, else 70)"},
            "rules": {"type": "array", "items": {"type": "string"}, "description": "other code/test rules for Dev and Quinn"},
        }),
        "changes": {"type": "string", "description": "What changed versus the previous version (if revising), else empty"},
    }),
)
DEFAULT_GATES = {"min_coverage_percent": 70, "rules": []}


def quality_gates(project_id: str) -> dict:
    """Archie's quality gates (the user can edit them on the Design tab). Enforced by the platform on Dev's code."""
    d = load_design(project_id) or {}
    return {**DEFAULT_GATES, **(d.get("quality_gates") or {})}


def stable_order(old: dict, new: dict) -> dict:
    """Keep the previous diagram's layout: in each group, nodes that existed keep their old order; new ones go last."""
    out = dict(new)
    for group in ("sources", "path", "destinations", "support"):
        rank = {n["id"]: i for i, n in enumerate(old.get(group, []))}
        out[group] = sorted(new.get(group, []), key=lambda n: (n["id"] not in rank, rank.get(n["id"], 0)))
    return out


COVERAGE_LINE = re.compile(r"(?i)((?:minimum|min\.?|at least)[^\n%]{0,60}?coverage[^\n%]{0,40}?|coverage[^\n%]{0,40}?"
                           r"(?:minimum|min\.?|at least|gate|≥|>=)[^\n%]{0,20}?)(\d{1,3}(?:\.\d+)?)\s*%")


COVERAGE_FLAG = re.compile(r"(?i)(--cov-fail-under[= ]|fail_under\s*=\s*|minimumCoverage\s*[=:]\s*|coverageThreshold[^\n]{0,40}?lines\D{0,4})(\d{1,3}(?:\.\d+)?)")


def sync_coverage(md: str, pct: float) -> str:
    """The coverage gate is one number the user can change on the Design tab; every place the documents state it
    follows (e.g. 'Minimum line coverage is 70%' → 80%, `--cov-fail-under=70` → 80)."""
    md = COVERAGE_LINE.sub(lambda m: f"{m.group(1)}{pct:g}%", md or "")
    return COVERAGE_FLAG.sub(lambda m: f"{m.group(1)}{pct:g}", md)


def load_design(project_id: str) -> dict | None:
    try:
        return json.loads(ProjectStore(project_id).read("diagrams/design.json"))
    except (StorageError, FileNotFoundError, ValueError):
        return None


def docs_only(project_id: str, previous: dict | None, new: dict) -> bool:
    """A revision of a design that's already built (Terra's infrastructure exists) that changes only documents: the HLD's
    wording or the diagram. The LLD, the resources and the quality gates are exactly the same, so Terra, Dev and Quinn have
    nothing to redo (user, 10-02: a reworded diagram label sent the whole crew round again)."""
    from app.agents.tp import load_preview

    return bool(previous) and bool(load_preview(project_id)) and new["lld_markdown"] == previous.get("lld_markdown") \
        and new["resources"] == previous.get("resources") and new["quality_gates"] == previous.get("quality_gates")


async def _documents_only(ctx: JobContext, pid: str, version: str, data: dict) -> None:
    """Save it as a document update: no gate, no hand-off. The project goes back to where it was."""
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import Approval, ChangeRequest

    store = ProjectStore(pid)
    store.append_changelog(version, ["Documents updated by Archie (HLD/diagram): no functional change, nothing to rebuild"])
    async with SessionLocal() as db:
        pending = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.status == "pending"))).scalars().first()
        done = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.stage == "live",
                                                        Approval.status == "approved"))).scalars().first()
        for cr in (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == pid, ChangeRequest.route.in_(["design", "plan"]),
                                                                ChangeRequest.status.in_(["planning", "in_progress"])))).scalars():
            cr.status = "done"
        await db.commit()
    await ctx.set_agent("ta", "done", "Documents updated (HLD/diagram): no functional change, nothing to rebuild")
    await ctx.set_project(status="waiting" if pending else "completed" if done else "waiting",
                          last_activity="Archie updated the documents (no functional change): nothing to rebuild")
    await ctx.emit("design.updated", "Archie updated the documents: no functional change", agent="ta")
    await crewchat.say(pid, "ta", "user", f"Done: {data.get('changes') or 'the documents are updated'}. This only touches the documents (HLD "
                       "wording, the diagram): the LLD, the AWS resources and the quality gates are exactly the same, so Terra, Dev and "
                       "Quinn have nothing to redo and nothing needs your approval. If you meant a real change, raise a change request.",
                       "decision", files=["02_hld.md", DRAWIO])
    from app.services import tickets as tk

    await tk.close_for_crs(pid, "ta", f"Done in {version}: documents updated (no functional change). {data.get('changes') or ''}".strip())
    await tk.resume_queued(pid)


@handler("ta.design", resumable=False)
async def design(ctx: JobContext) -> None:
    from app.agents.ba import load_mapping
    from app.agents.cto import research_tools

    pid = ctx.project_id
    feedback = (ctx.payload.get("feedback") or "").strip()
    user_diagram = bool(ctx.payload.get("user_diagram"))
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    intake = await load_intake(pid)
    mapping = load_mapping(pid)
    previous = load_design(pid)
    trail: list[dict] = []

    async def verify(a: dict):
        g = a["quality_gates"]["min_coverage_percent"]
        if not 0 <= g <= 100:
            raise AgentError("quality_gates.min_coverage_percent must be between 0 and 100")
        problems = diagram.validate(a["architecture"])
        if problems:
            raise AgentError("The diagram can't be drawn: " + "; ".join(problems))
        for key in ("hld_markdown", "lld_markdown"):
            if a[key].strip() == "UNCHANGED" and not (previous or {}).get(key):
                raise AgentError(f"{key}: there is no previous document to keep; write it in full")
        lld = previous.get("lld_markdown", "") if a["lld_markdown"].strip() == "UNCHANGED" else a["lld_markdown"]
        if "orkestra-" not in lld:
            raise AgentError("Resource names in the LLD must start with `orkestra-` (sandbox rule). Rename them.")

    plan_step = next((s for s in (intake.plan or {}).get("steps", []) if s["agent"] == "ta"), None)
    try:
        mapping_md = store.read("01_data_mapping.md").decode()
    except StorageError:
        mapping_md = "(no data mapping)"
    from app.services.naming import convention_block

    from app.agents.buildkit import talk_notes

    naming = convention_block(pid)
    notes = talk_notes(store, own="ta")  # the technical lead's decisions: architecture, tech stack (agents/talk.py)
    ask = (f"# Project id: {pid}\n# Signed-off requirement ({version})\n{intake.requirement_md}\n\n"
           + (notes + "\n\n" if notes else "")
           + (naming + "\n" if naming else "")
           + f"# Approved data mapping (Atlas)\n{mapping_md[:40000]}\n\n"
           + (f"# Orion's plan for you\n{plan_step['task']}\nApproval: {plan_step['approval']}\n\n" if plan_step else "")
           + "Design the solution and call submit_design.")
    if previous:  # any revision (feedback, the user's drawing, or a change request): build on the approved design
        from app.orchestrator import changes

        cr = await changes.get((intake.plan or {}).get("cr_id", "")) if (intake.plan or {}).get("cr_id") else None
        if cr and cr.diff and cr.version_to == version:
            ask += (f"\n\n---\n# What changed: {changes.label(cr)} ({cr.version_from} → {cr.version_to})\n{cr.text}\n"
                    f"```diff\n{cr.diff[:12000]}\n```")
        ask += ("\n\n---\n# Your previous design (approved). REVISE it: change only what this change needs.\n"
                "- Keep every architecture node (same ids, same groups, same order) and edge unless the change removes it; add new "
                "nodes at the end of their group. The diagram's layout then stays as the user knows it.\n"
                "- Keep the HLD/LLD text as it is except the sections the change touches. If a document doesn't change at all, "
                "send exactly UNCHANGED for it. Describe the change in `changes`.\n"
                + json.dumps({k: previous[k] for k in ("summary", "architecture", "decisions", "resources", "quality_gates")
                              if k in previous}, ensure_ascii=False)
                + f"\n\n## Previous HLD\n{previous.get('hld_markdown', '')[:30000]}\n\n## Previous LLD\n{previous.get('lld_markdown', '')[:30000]}")
    if user_diagram:
        ask += ("\n\n# The user edited the diagram in draw.io. Their drawing is now the source of truth (it stays exactly as "
                f"they drew it). Bring the HLD, LLD, resources and your `architecture` in line with it.\nTheir changes: {feedback}")
    elif feedback:
        ask += f"\n\n# The user's feedback (address every point, describe it in `changes`)\n{feedback}"

    await ctx.set_agent("ta", "working", f"Revising the design for {version} (only what changes)" if previous
                        else f"📐 Reading requirement {version} and Atlas's mapping")
    await ctx.set_project(status="running", last_activity="Archie is revising the design" if previous else "Archie is designing the solution")
    await crewchat.say(pid, "ta", "cto", (f"Revising the design for {version}: I change only what this change needs, and the diagram "
                                          "keeps its layout." if previous else
                                          f"Starting the HLD, LLD and diagram from requirement {version} and Atlas's approved mapping"
                                          + (f" ({len(mapping['rows'])} fields)" if mapping else "") + ". No code from me: designs and exact settings."),
                       "ack")
    try:
        res = await run_loop(project_id=pid, agent="ta",
                             system=[{"type": "text", "text": llm.prompt("ta.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}],
                             tools=[*research_tools(ctx, trail, agent="ta"), replace(SUBMIT, handler=verify)],
                             max_turns=10, purpose="design-revise" if (feedback or user_diagram) else "design")
        data = res.terminal.get("submit_design")
        if not data:
            raise AgentError("Archie finished without submitting a design.")
        for key in ("hld_markdown", "lld_markdown"):
            if data[key].strip() == "UNCHANGED":
                data[key] = previous[key]
        if previous and previous.get("architecture"):
            data["architecture"] = stable_order(previous["architecture"], data["architecture"])
        footer = f"Orkestra · Archie (TA) · {version} · editable in draw.io"
        if user_diagram:
            xml = store.read(USER_DRAWIO).decode()
        else:
            xml = diagram.build(data["architecture"], footer)
        data["research_trail"] = trail
        if previous and previous.get("quality_gates", {}).get("set_by") == "user":
            data["quality_gates"] = previous["quality_gates"]  # a number the user set by hand wins over a redesign
        pct = data["quality_gates"]["min_coverage_percent"]
        data["lld_markdown"], data["hld_markdown"] = sync_coverage(data["lld_markdown"], pct), sync_coverage(data["hld_markdown"], pct)
        store.write("02_hld.md", data["hld_markdown"])
        store.write("03_lld.md", data["lld_markdown"])
        store.write(ARCH_JSON, json.dumps(data["architecture"], indent=2, ensure_ascii=False))
        store.write(DRAWIO, xml)
        store.write("diagrams/design.json", json.dumps(data, indent=2, ensure_ascii=False))
        store.append_changelog(version, [f"Design {'revised' if previous else 'written'} by Archie: HLD, LLD, diagram "
                                         f"({len(data['resources'])} resources)"])
        arch = data["architecture"]
        shapes = sum(len(arch.get(k, [])) for k in ("sources", "path", "destinations", "support"))
        line = f"HLD + LLD · {shapes} components · {len(data['resources'])} AWS resources · {len(data['decisions'])} decisions"
        if docs_only(pid, previous, data):
            await _documents_only(ctx, pid, version, data)
            return
        await ctx.set_agent("ta", "needs_approval", f"Design ready: {line}")
        await ctx.set_project(progress=round(3 / 6, 3))
        await ctx.emit("design.updated", f"Archie {'revised' if previous else 'wrote'} the design: {line}", agent="ta")
        await crewchat.say(pid, "ta", "cto", f"Design ready for {version}: {line}. {data['summary']}", "handoff",
                           files=["02_hld.md", "03_lld.md", DRAWIO])
        for dcs in data["decisions"][:6]:
            await crewchat.say(pid, "ta", "crew", f"Decision: **{dcs['decision']}**. Why: {dcs['why']}"
                               + (f" (considered: {dcs['alternatives']})" if dcs.get("alternatives") else ""), "update")
        qg = data["quality_gates"]
        await crewchat.say(pid, "ta", "de", f"Dev, quality gates for your code: at least {qg['min_coverage_percent']:.0f}% line coverage"
                           + "".join(f"; {r}" for r in qg.get("rules", [])) + ". The platform checks the coverage on every submission.", "update")
        await crewchat.say(pid, "ta", "tp", f"Terra, the LLD lists {len(data['resources'])} resources for you to build: "
                           + ", ".join(r["name"] for r in data["resources"][:12]) + ". Exact settings are in 03_lld.md.", "update")
        await flow.request_approval(pid, "design", "Archie's design (HLD, LLD, diagram)", data["summary"], ["02_hld.md", "03_lld.md", DRAWIO])
    except Exception as exc:
        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("ta", "blocked" if blocked else "failed", str(exc)[:240])
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Archie: {str(exc)[:200]}")
        await ctx.emit("design.updated", f"Archie hit a problem: {str(exc)[:200]}", agent="ta")
        await crewchat.say(pid, "ta", "cto", f"I'm stuck: {str(exc)[:300]}", "issue")
        raise
