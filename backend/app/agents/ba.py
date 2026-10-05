"""Atlas — Business / Data Analyst.

ba.map: reads the signed-off requirement (+ uploads) and writes the data mapping DOCUMENT: field-by-field rows,
validation rules and worked examples (happy path + error cases). Atlas writes no code: the platform checks the
examples against his own mapping table (services/mapping_check.py) and sends any mismatch back for him to fix.
Then it renders 01_data_mapping.md in the team's format and opens the "mapping" approval gate.
Dev (DE) later writes the code and turns the examples into unit tests; Quinn (QA) uses them as test cases.
payload.feedback → revise the previous mapping.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.intake import load_intake
from app.orchestrator import crewchat, flow
from app.orchestrator.runner import JobContext, handler
from app.services import mapping_check
from app.services.storage import ProjectStore, StorageError

MAPPING_JSON = "mapping/01_data_mapping.json"

ROW = obj({
    "target": {"type": "string", "description": "Target field path, e.g. customer_name or order.customer.name"},
    "target_sample": {"type": "string", "description": "Sample value (for a constant: the constant itself)"},
    "target_moc": {"type": "string", "enum": ["M", "C", "O"]},
    "target_type": {"type": "string", "description": "e.g. string, integer, date"},
    "logic": {"type": "string", "description": "Precise transformation logic in mapping language (no code)"},
    "rule_kind": {"type": "string", "enum": ["copy", "constant", "derived"],
                  "description": "copy = trimmed source value; constant = fixed value; derived = anything else"},
    "source_path": {"type": "string", "description": "/Root/Child (XML), a.b.c (JSON), or N/A"},
    "source_sample": {"type": "string"},
    "source_moc": {"type": "string", "enum": ["M", "C", "O", "N/A"]},
    "source_type": {"type": "string"},
    "pii": {"type": "boolean"},
    "comments": {"type": "string"},
})
SAMPLE = obj({
    "name": {"type": "string", "description": "Short id, e.g. happy_path, missing_order_id"},
    "description": {"type": "string"},
    "input": {"type": "string", "description": "The raw input message"},
    "expect": {"type": "string", "enum": ["output", "reject"]},
    "expected": {"type": "string", "description": "Expected output message (exact), or the exact rejection reason"},
})

SUBMIT = Tool(
    name="submit_mapping",
    terminal=True,
    description=("Submit the finished mapping document. The platform checks every example against your mapping table; "
                 "any disagreement rejects the submission and tells you exactly what to fix."),
    schema=obj({
        "title": {"type": "string"},
        "summary": {"type": "string", "description": "2-3 sentences"},
        "direction": {"type": "string", "enum": ["Direct", "Split", "Aggregate"]},
        "source": obj({"system": {"type": "string"}, "message_name": {"type": "string"},
                       "format": {"type": "string"}, "description": {"type": "string"}}),
        "target": obj({"system": {"type": "string"}, "message_name": {"type": "string"},
                       "format": {"type": "string"}, "description": {"type": "string"}}),
        "rows": {"type": "array", "items": ROW},
        "rules": {"type": "array", "description": "Validation / filter rules", "items": obj({
            "rule": {"type": "string"}, "condition": {"type": "string"}, "action": {"type": "string"}})},
        "samples": {"type": "array", "items": SAMPLE},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "queries": {"type": "array", "items": obj({"question": {"type": "string"}, "owner": {"type": "string"}})},
        "changes": {"type": "string", "description": "What changed versus the previous version (if revising), else empty"},
    }),
)


def render(m: dict, version: str) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    proof = m.get("proof", [])
    agreed = sum(p["pass"] for p in proof)
    out = [f"# 01_data_mapping.md: {m['title']}", "",
           f"_Atlas (BA/DA) · {version} · {now} · Examples check: **{agreed}/{len(proof)} examples agree with the mapping table**_", "",
           m["summary"], "",
           "## 1. References", "", "| Document | Use |", "|---|---|",
           "| 00_requirement.md | Signed-off requirement (source of truth) |", "",
           "## 2. Interface summary", "", "| | Source | Target |", "|---|---|---|",
           f"| System | {esc(m['source']['system'])} | {esc(m['target']['system'])} |",
           f"| Message | {esc(m['source']['message_name'])} | {esc(m['target']['message_name'])} |",
           f"| Format | {esc(m['source']['format'])} | {esc(m['target']['format'])} |",
           f"| Description | {esc(m['source']['description'])} | {esc(m['target']['description'])} |", "",
           f"**Split / Aggregate / Direct:** {m['direction']}", "",
           "## 3. Data mapping", "",
           "| # | Target element | Sample | M/C/O | Datatype | Transformation logic | Source element | Source sample | M/C/O | Datatype | PII | Comments |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(m["rows"], 1):
        out.append(f"| {i} | `{esc(r['target'])}` | {esc(r['target_sample'])} | {r['target_moc']} | {esc(r['target_type'])} | "
                   f"{esc(r['logic'])} | `{esc(r['source_path'])}` | {esc(r['source_sample'])} | {r['source_moc']} | "
                   f"{esc(r['source_type'])} | {'Yes' if r['pii'] else 'No'} | {esc(r['comments'])} |")
    out += ["", "## 4. Validation & filter rules", "", "| Rule | Condition | Action |", "|---|---|---|"]
    out += [f"| {esc(r['rule'])} | {esc(r['condition'])} | {esc(r['action'])} |" for r in m["rules"]]
    out += ["", "## 5. Worked examples", "",
            "Dev turns each example into a unit test; Quinn uses them as end-to-end test cases.", ""]
    by = {p["name"]: p for p in proof}
    for s in m["samples"]:
        p = by.get(s["name"], {})
        out += [f"### {s['name']} {'✅' if p.get('pass') else '❌'}", "", s["description"], "", "Input:", "", "```", s["input"].strip(), "```", ""]
        if s["expect"] == "output":
            out += ["Expected output:", "", "```json" if s["expected"].strip().startswith(("{", "[")) else "```", s["expected"].strip(), "```", ""]
        else:
            out += [f"Expected: **rejected** with reason `{s['expected']}`", ""]
    out += ["## 6. Examples check", "",
            "The platform compared every example with the mapping table above (no code was written or run): copied fields "
            "must equal their source values, constants their value, and every rejection must break a rule.", "",
            "| Example | Expectation | Result | Detail |", "|---|---|---|---|"]
    out += [f"| {p['name']} | {p['expect']} | {'✅ agrees' if p['pass'] and p.get('verified', True) else '🔎 for Dev/Quinn' if p['pass'] else '❌ disagrees'} "
            f"| {esc(p['actual'])[:200]} |" for p in proof]
    out += ["", "## 7. Assumptions", ""] + [f"- {a}" for a in m["assumptions"]]
    out += ["", "## 8. Queries", "", "| # | Question | Owner |", "|---|---|---|"]
    out += [f"| {i} | {esc(q['question'])} | {esc(q['owner'])} |" for i, q in enumerate(m["queries"], 1)] or ["| – | None | – |"]
    out += ["", "## 9. Change history", "", "| Version | Date | Change |", "|---|---|---|",
            f"| {version} | {now} | {esc(m.get('changes') or 'Initial mapping')} |", ""]
    return "\n".join(out)


def load_mapping(project_id: str) -> dict | None:
    try:
        return json.loads(ProjectStore(project_id).read(MAPPING_JSON))
    except (StorageError, FileNotFoundError, ValueError):
        return None


@handler("ba.map", resumable=False)
async def map_data(ctx: JobContext) -> None:
    pid = ctx.project_id
    feedback = (ctx.payload.get("feedback") or "").strip()
    intake = await load_intake(pid)
    previous = load_mapping(pid)
    version = ProjectStore(pid).manifest()["current_version"]
    attempts = {"n": 0}
    proof_box: dict = {}

    async def verify(a: dict):
        attempts["n"] += 1
        await ctx.set_agent("ba", "working", f"🔎 Checking {len(a['samples'])} examples against {len(a['rows'])} mapping rows "
                                             f"(attempt {attempts['n']})")
        proof = mapping_check.check(a)
        bad = [p for p in proof if not p["pass"]]
        if bad:  # the engine posts the rejection (and the later fix) to the crew room
            raise AgentError(f"{len(bad)} example(s) don't agree with your mapping table: "
                             + "; ".join(f"{p['name']} → {p['actual'][:300]}" for p in bad))
        proof_box["proof"] = proof
        await crewchat.say(pid, "ba", "crew", f"🔎 Examples check: {len(proof)} examples agree with my table "
                           f"({sum(p.get('checked', 0) for p in proof)} field values compared, no code run).", "work")

    from app.agents.buildkit import talk_notes
    from app.agents.talk import load as load_talk

    talk = await load_talk(pid, "ba")
    files = list(intake.uploads or []) + [u for u in ((talk.uploads if talk else None) or []) if u["name"] not in {x["name"] for x in intake.uploads or []}]
    uploads = "\n\n".join(f"# Uploaded: {u['name']}\n{u['text']}" for u in files)
    notes = talk_notes(ProjectStore(pid), own="ba")  # the data analyst's answers: samples, mapping, rules (agents/talk.py)
    plan_step = next((s for s in (intake.plan or {}).get("steps", []) if s["agent"] == "ba"), None)
    ask = (f"# Signed-off requirement ({version})\n{intake.requirement_md}\n\n" + (notes + "\n\n" if notes else "") + f"{uploads}\n\n"
           + (f"# Orion's plan for you\n{plan_step['task']}\nApproval: {plan_step['approval']}\n\n" if plan_step else "")
           + "Write the data mapping document, then call submit_mapping.")
    if feedback and previous:
        ask += ("\n\n---\n# Your previous mapping\n" + json.dumps({k: v for k, v in previous.items()
                                                                   if k not in ("proof", "transform_code", "sandbox_runs")}, ensure_ascii=False)
                + f"\n\n# The user's feedback (address every point, describe it in `changes`)\n{feedback}")

    await ctx.set_agent("ba", "working", "Revising the mapping with your feedback" if feedback else f"📚 Reading requirement {version} and the samples")
    await ctx.set_project(status="running", last_activity="Atlas is writing the data mapping")
    await crewchat.say(pid, "ba", "cto", f"Starting the mapping from requirement {version}"
                       + (f" with the user's feedback: {feedback[:300]}" if feedback else f" and {len(intake.uploads or [])} uploaded file(s)")
                       + ". No code from me: rows, rules and worked examples.", "ack")
    try:
        res = await run_loop(project_id=pid, agent="ba",
                             system=[{"type": "text", "text": llm.prompt("ba.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}], tools=[replace(SUBMIT, handler=verify)],
                             max_turns=6, purpose="mapping-revise" if feedback else "mapping")
        data = res.terminal.get("submit_mapping")
        if not data:
            raise AgentError("Atlas finished without submitting a mapping.")
        data["proof"] = proof_box.get("proof", [])
        data["check_attempts"] = attempts["n"]
        store = ProjectStore(pid)
        store.write(MAPPING_JSON, json.dumps(data, indent=2, ensure_ascii=False))
        store.write("01_data_mapping.md", render(data, version))
        for s in data["samples"]:
            store.write(f"mapping/examples/{s['name']}.input.txt", s["input"])
        store.append_changelog(version, [f"Data mapping {'revised' if previous else 'written'} by Atlas: "
                                         f"{len(data['rows'])} fields, {len(data['samples'])} worked examples"])
        agreed = sum(p["pass"] and p.get("verified", True) for p in data["proof"])
        manual = sum(p["pass"] and not p.get("verified", True) for p in data["proof"])
        line = f"{len(data['rows'])} fields · {len(data['samples'])} examples ({agreed} checked" + (f", {manual} for Dev/Quinn)" if manual else ")")
        await ctx.set_agent("ba", "needs_approval", f"Mapping ready: {line}")
        await ctx.set_project(progress=round(2 / 6, 3))
        await ctx.emit("mapping.updated", f"Atlas {'revised' if previous else 'wrote'} the data mapping: {line}", agent="ba")
        await crewchat.say(pid, "ba", "cto", f"Mapping ready for {version}: {line}. Waiting for the user's approval.", "handoff",
                           files=["01_data_mapping.md"])
        await flow.request_approval(pid, "mapping", "Atlas's data mapping", data["summary"], ["01_data_mapping.md"])
    except Exception as exc:
        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("ba", "blocked" if blocked else "failed", str(exc)[:240])
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Atlas: {str(exc)[:200]}")
        await ctx.emit("mapping.updated", f"Atlas hit a problem: {str(exc)[:200]}", agent="ba")
        await crewchat.say(pid, "ba", "cto", f"I'm stuck: {str(exc)[:300]}", "issue")
        raise
