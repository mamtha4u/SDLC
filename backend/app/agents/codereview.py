"""Archie reviews Dev's code before it goes anywhere near AWS (user, 10-02: "once the codebase is created, inform TA; TA
asks the user to check everything is OK; only then deploy").

ta.code_review  after every de.code (when the infrastructure is live): Archie reads his LLD, the mapping, Dev's code, the
                unit tests and the coverage, and checks it like a lead reviewing a pull request: built to the design,
                every mapping rule, error handling, logging rules, security, tests, packaging. `submit_code_review`:
                verdict, checklist, findings (must / should / nice, with the file and the fix), what the user should
                look at. "changes" with must-fix points → straight back to Dev (up to two rounds, like a real review);
                then the "code_review" gate: the user checks the code with Archie's review in hand. Approve → Dev
                deploys and tests the whole flow; request changes → Dev revises, Archie reviews again.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.buildkit import context, files_under, read
from app.agents.intake import load_intake
from app.orchestrator import crewchat, flow
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import aws_access
from app.services.storage import ProjectStore

REVIEW_JSON, REVIEW_MD = "reports/code_review.json", "reports/code_review.md"
STATE = "code_review"  # deploy/code_review.json: the round and what the deploy carries (tickets, changes)
MAX_ROUNDS = 2  # Archie sends it back to Dev at most twice; then you decide with the open points in front of you
AREAS = ["design", "mapping", "error handling", "logging", "security", "tests", "packaging", "performance", "structure", "readability"]
# what the user wants a lead's opinion on (user, 10-02: "vulnerabilities, DSA or normal code, class or function based, comments or docstrings…")
TOPICS = ["security", "structure: classes or functions", "comments and docstrings", "data structures and efficiency", "error handling style",
          "dependencies"]
SUBMIT = Tool(
    name="submit_code_review",
    terminal=True,
    description="Submit your review of Dev's code.",
    schema=obj({
        "verdict": {"type": "string", "enum": ["approve", "changes"], "description": "approve: fit to deploy; changes: must-fix points first"},
        "summary": {"type": "string", "description": "2-3 sentences for the user: what the code does, whether it's built to the LLD, ready to deploy"},
        "checks": {"type": "array", "description": "your checklist, one line per thing you verified", "items": obj({
            "area": {"type": "string", "enum": AREAS}, "item": {"type": "string", "description": "what you checked"},
            "ok": {"type": "boolean"}, "note": {"type": "string", "description": "where and how (file, function, test), or what's wrong"}})},
        "findings": {"type": "array", "description": "what should change (empty if nothing)", "items": obj({
            "severity": {"type": "string", "enum": ["must", "should", "nice"], "description": "must: blocks the deploy; should: fix soon; nice: optional"},
            "file": {"type": "string", "description": "the file (e.g. src/transform/handler.py), or empty"},
            "issue": {"type": "string"}, "fix": {"type": "string", "description": "what Dev should do"}})},
        "design_notes": {"type": "array", "description": "your recommendation on each topic, for the user to agree or change", "items": obj({
            "topic": {"type": "string", "enum": TOPICS}, "now": {"type": "string", "description": "what the code does now"},
            "recommendation": {"type": "string", "description": "keep it, or what to change to"}, "why": {"type": "string"}})},
        "for_user": {"type": "array", "items": {"type": "string"},
                     "description": "what the user should look at before approving, and where (file, report, test): 3-6 short points"},
        # user, 10-05: "TA needs to ask the user: is the code fine, function-based or class-based… and give suggestions,
        # e.g. for a Lambda, globals at the top so the cold start loads them once"
        "choices": {"type": "array", "description": "2-4 questions for the user where the code could go either way (always structure: "
                                                    "functions or classes)", "items": obj({
            "topic": {"type": "string", "description": "short, e.g. structure"},
            "question": {"type": "string", "description": "plain words, e.g. Plain functions or classes?"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "2-4 short options"},
            "current": {"type": "string", "description": "the option the code follows now (one of options)"},
            "recommended": {"type": "string", "description": "your pick (one of options)"},
            "why": {"type": "string", "description": "one line"}})},
        "suggestions": {"type": "array", "description": "0-5 improvements the user can accept or skip", "items": obj({
            "title": {"type": "string"}, "detail": {"type": "string", "description": "what and where, 1-2 sentences"},
            "example": {"type": "string", "description": "a tiny code example (a few lines), or empty"},
            "benefit": {"type": "string", "description": "why it's worth it, one line"}})},
    }),
)
ANSWER_LIMIT = 6000


def load_review(project_id: str) -> dict | None:
    raw = read(ProjectStore(project_id), REVIEW_JSON)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def review_markdown(r: dict) -> str:
    esc = lambda s: str(s or "").replace("|", "\\|").replace("\n", " ")  # noqa: E731
    verdict = "✅ Approved: fit to deploy" if r["verdict"] == "approve" else f"✏️ Changes needed ({sum(f['severity'] == 'must' for f in r['findings'])} must-fix)"
    t = r.get("tests") or {}
    lines = [f"# Code review (Archie) · {r['version']}", "", f"**{verdict}** · round {r['round']} · reviewed {r['at']}", "", r["summary"], "",
             "## The evidence", "", f"- Unit tests: **{t.get('passed', '?')}/{t.get('total', '?')}** passing",
             f"- Line coverage: **{t.get('coverage', '?')}%** (gate {t.get('gate', '?')}%): `reports/coverage.html` shows every tested and untested line",
             f"- Files reviewed: {', '.join(f'`{p}`' for p in r.get('files', []))}", ""]
    lines += ["## What Archie checked", "", "| Area | Check | Result | Where / note |", "|---|---|---|---|"]
    lines += [f"| {c['area']} | {esc(c['item'])} | {'✅' if c['ok'] else '❌'} | {esc(c['note'])} |" for c in r["checks"]]
    if r["findings"]:
        lines += ["", "## Findings", "", "| Severity | File | Issue | Fix |", "|---|---|---|---|"]
        order = {"must": 0, "should": 1, "nice": 2}
        lines += [f"| {f['severity']} | {('`' + f['file'] + '`') if f['file'] else '-'} | {esc(f['issue'])} | {esc(f['fix'])} |"
                  for f in sorted(r["findings"], key=lambda x: order[x["severity"]])]
    if r.get("design_notes"):
        lines += ["", "## Archie's recommendations", "", "| Topic | Now | Recommendation | Why |", "|---|---|---|---|"]
        lines += [f"| {n['topic']} | {esc(n['now'])} | {esc(n['recommendation'])} | {esc(n['why'])} |" for n in r["design_notes"]]
    lines += ["", "## Before you approve, look at", "", *[f"- {p}" for p in r.get("for_user", [])], "",
              "Ask Archie anything about this code on the Build tab (Code & deploy → Code review): a deeper security review, "
              "classes or functions, comments or docstrings… When you're happy, approve.", "",
              "## What happens next", "",
              "Approve, and Dev hands this code (and his layers) to Terra as packages; Terra deploys them with Terraform in a plan "
              "you approve, replacing the placeholder. Then Dev tests the whole flow (API → Lambda → queue → logs) and shows you "
              "how to repeat it in the AWS console. Request changes, and Dev revises; Archie reviews again.", ""]
    if r.get("history"):
        lines += ["## Earlier rounds", "", *[f"- Round {h['round']} ({h['at']}): {h['verdict']}, {h['must']} must-fix: {h['summary']}" for h in r["history"]], ""]
    return "\n".join(lines)


async def _material(pid: str) -> tuple[dict, dict, dict, dict, dict, str]:
    """What Archie reviews from: the requirement, mapping and his LLD, the quality gates, where the code goes, Dev's code,
    tests and coverage. Returns (code files, Dev's meta, tests, coverage, gates, the prompt text)."""
    from app.agents.de import FIXTURE, code_deploy_map, load_code
    from app.agents.ta import quality_gates

    store = ProjectStore(pid)
    intake = await load_intake(pid)
    code = files_under(store, ("src/", "layers/", "tests/"))
    meta = load_code(pid) or {}
    tests = meta.get("tests") or {}
    cov = tests.get("coverage") or {}
    gates = quality_gates(pid)
    files_block = "\n\n".join(f"## {p}\n```\n{c[:20000]}\n```" for p, c in sorted(code.items()) if not p.startswith("tests/"))
    tests_block = "\n\n".join(f"## {p}\n```python\n{c[:6000]}\n```" for p, c in sorted(code.items()) if p.startswith("tests/") and p != FIXTURE)
    text = (f"# Project id: {pid} · {store.manifest()['current_version']}\n" + context(store, intake.requirement_md, limit=40000)
            + f"\n\n# Quality gates (yours)\n- line coverage ≥ {gates['min_coverage_percent']}%\n" + "\n".join(f"- {r}" for r in gates.get("rules", []))
            + f"\n\n# Terra's functions (where this code goes)\n```json\n{json.dumps(code_deploy_map(pid), indent=2)[:4000]}\n```"
            + f"\n\n# Dev's notes\n{meta.get('summary', '')}\n" + "\n".join(f"- {n}" for n in meta.get("notes", []))
            + (f"\n\nWhat changed in this version: {meta['changes']}" if meta.get("changes") else "")
            + f"\n\n# Unit tests: {tests.get('passed', '?')}/{tests.get('total', '?')} passing · coverage {cov.get('percent', '?')}%\n"
            + "\n".join(f"- {p}: {f['percent']}% (untested lines {', '.join(map(str, f.get('missing', [])[:25])) or 'none'})"
                        for p, f in sorted((cov.get("files") or {}).items()))
            + f"\n\n# Dev's code\n{files_block}\n\n# Dev's tests (excerpt)\n{tests_block[:40000]}")
    return code, meta, tests, cov, gates, text


@handler("ta.code_review", resumable=False)
async def code_review(ctx: JobContext) -> None:
    pid = ctx.project_id
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    state = aws_access.load(pid, STATE) or {}
    if state.get("version") != version:
        state = {"version": version, "round": 0, "history": []}
    state.update(round=state["round"] + 1, tickets=ctx.payload.get("tickets") or state.get("tickets") or [],
                 changes=ctx.payload.get("changes") or "", attempt=int(ctx.payload.get("attempt") or 0))
    code, meta, tests, cov, gates, material = await _material(pid)
    real = set(code)

    async def verify(a: dict):
        must = [f for f in a["findings"] if f["severity"] == "must"]
        if a["verdict"] == "approve" and must:
            raise AgentError("You approved but listed must-fix findings: either ask for changes, or make them 'should'.")
        if a["verdict"] == "changes" and not must:
            raise AgentError("Asking for changes needs at least one must-fix finding (what blocks the deploy, and the fix).")
        missing = {"design", "mapping", "error handling", "logging", "tests"} - {c["area"] for c in a["checks"]}
        if missing:
            raise AgentError(f"Your checklist must cover: {sorted(missing)}")
        wrong = [f["file"] for f in a["findings"] if f["file"] and f["file"] not in real]
        if wrong:
            raise AgentError(f"These files aren't in Dev's code: {wrong}. Use the exact paths.")
        if len(a["for_user"]) < 2:
            raise AgentError("Tell the user what to look at before approving (2-6 points, with where).")
        need = set(TOPICS[:4]) - {n["topic"] for n in a["design_notes"]}
        if need:
            raise AgentError(f"Give your recommendation (now / recommendation / why) on: {sorted(need)}")
        if len(a["choices"]) < 2 or not any("struct" in c["topic"].lower() or "class" in c["question"].lower() for c in a["choices"]):
            raise AgentError("Ask the user 2-4 `choices`, one of them about structure (plain functions or classes).")
        odd = [c["question"] for c in a["choices"] if len(c["options"]) < 2 or c["current"] not in c["options"] or c["recommended"] not in c["options"]]
        if odd:
            raise AgentError(f"Each choice needs 2-4 options, and `current` and `recommended` must be two of them: {odd}")
        if len(a["suggestions"]) > 5:
            raise AgentError("At most 5 suggestions: keep the ones that matter most.")

    ask = f"# Review round {state['round']}\n{material}"
    if state.get("history"):
        ask += "\n\n# Your earlier rounds on this version (check the must-fix points are done)\n" + "\n".join(
            f"- Round {h['round']}: {h['verdict']}: {h['summary']} Must: {'; '.join(h.get('must_points', []))}" for h in state["history"])
    if ctx.payload.get("user_feedback"):
        ask += f"\n\n# The user's comments that Dev just addressed\n{ctx.payload['user_feedback']}"
    ask += "\n\nReview the code, then call submit_code_review."

    await ctx.set_agent("ta", "working", f"🔍 Reviewing Dev's code for {version} (round {state['round']})")
    await ctx.set_project(status="running", last_activity="Archie is reviewing Dev's code")
    await crewchat.say(pid, "ta", "de", f"Thanks Dev, reviewing {version} against my LLD now (round {state['round']}).", "ack")
    try:
        res = await run_loop(project_id=pid, agent="ta",
                             system=[{"type": "text", "text": llm.prompt("ta_review.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}], tools=[replace(SUBMIT, handler=verify)],
                             max_turns=4, purpose="code-review", effort="medium")
        data = res.terminal.get("submit_code_review")
        if not data:
            raise AgentError("Archie finished without submitting the review.")
    except Exception as exc:
        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("ta", "blocked" if blocked else "failed", f"Code review: {str(exc)[:200]}")
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Archie's code review: {str(exc)[:160]}")
        await crewchat.say(pid, "ta", "cto", f"I couldn't finish the code review: {str(exc)[:300]}", "issue")
        raise
    must = [f for f in data["findings"] if f["severity"] == "must"]
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    review = {**data, "version": version, "round": state["round"], "at": now, "history": state.get("history", []),
              "files": sorted(p for p in code if not p.startswith("tests/")),
              "tests": {"passed": tests.get("passed"), "total": tests.get("total"), "coverage": cov.get("percent"), "gate": gates["min_coverage_percent"]}}
    store.write(REVIEW_JSON, json.dumps(review, indent=2, ensure_ascii=False))
    store.write(REVIEW_MD, review_markdown(review))
    state["history"] = [*state.get("history", []), {"round": state["round"], "at": now, "verdict": data["verdict"], "must": len(must),
                                                     "summary": data["summary"], "must_points": [f"{f['file']}: {f['issue']}" for f in must]}]
    aws_access.save(pid, state, STATE)
    await ctx.emit("build.updated", f"Archie's code review: {data['verdict']}", agent="ta")

    if ctx.payload.get("on_demand"):  # you asked for a review of the code as it is: no gate, nothing redeployed
        await ctx.set_agent("ta", "done", f"Reviewed the code on your request: {data['verdict']}")
        await crewchat.say(pid, "ta", "user", f"I reviewed the current code ({version}) as you asked: {data['summary']} "
                           + (f"{len(must)} must-fix point(s). " if must else "") + "Ask me anything about it on the Build tab (Code review); "
                           "if you want something changed, send it to Dev from there.", "answer", files=[REVIEW_MD])
        return
    if data["verdict"] == "changes" and state["round"] <= MAX_ROUNDS:  # like a real review: back to Dev first
        points = "\n".join(f"- **{f['file'] or 'general'}**: {f['issue']} → {f['fix']}" for f in must)
        await crewchat.say(pid, "ta", "de", f"Dev, {len(must)} must-fix point(s) before this can go to the user:\n{points}", "issue", files=[REVIEW_MD])
        await ctx.set_agent("ta", "done", f"Review round {state['round']}: {len(must)} must-fix point(s) back to Dev")
        await runner_mod.runner.enqueue("de.code", project_id=pid, tickets=state["tickets"], attempt=state["attempt"],
                                        feedback=f"Archie's code review (round {state['round']}) found must-fix points. Fix each one, keep "
                                                 f"everything else as it is, and say what you changed in `changes`:\n{points}")
        return

    line = (f"approved, {len(data['findings'])} minor point(s)" if data["verdict"] == "approve"
            else f"{len(must)} must-fix point(s) still open after {MAX_ROUNDS} rounds")
    await ctx.set_agent("ta", "needs_approval", f"Code review {version}: {line}")
    await crewchat.say(pid, "ta", "user", f"I reviewed Dev's code for {version}: {data['summary']} Please check it too before it's deployed: "
                       + " ".join(f"({i}) {p}" for i, p in enumerate(data["for_user"], 1)), "question", files=[REVIEW_MD, "reports/coverage.html"])
    title = (f"Archie approved Dev's code ({version}): your check before the deploy" if data["verdict"] == "approve"
             else f"Archie's review of Dev's code ({version}): {len(must)} point(s) still open, your call")
    await flow.request_approval(pid, "code_review", title, data["summary"] + " Approving lets Dev deploy it and test the whole flow.",
                                [REVIEW_MD, "reports/coverage.html", "reports/pytest.md", *review["files"][:8]])


async def ask(project_id: str, question: str, by: str) -> dict:
    """You ask Archie about the code under review (any time a review exists for the current version)."""
    store = ProjectStore(project_id)
    version = store.manifest()["current_version"]
    state = aws_access.load(project_id, STATE) or {}
    if state.get("version") != version or not load_review(project_id):
        raise AgentError("Archie hasn't reviewed this version's code yet: ask him once his review is in.")
    chat = state.get("chat") or []
    if chat and chat[-1]["role"] == "user":
        raise AgentError("Archie is still answering your last question.")
    chat.append({"role": "user", "by": by, "text": question.strip()[:4000], "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})
    aws_access.save(project_id, {**state, "chat": chat}, STATE)
    await crewchat.say(project_id, "user", "ta", f"Question about the code: {question.strip()[:600]}", "question")
    await runner_mod.runner.enqueue("ta.review_chat", project_id=project_id)
    return {**state, "chat": chat}


@handler("ta.review_chat", resumable=False)
async def review_chat(ctx: JobContext) -> None:
    """Archie answers the user's question about Dev's code, from the code itself, his LLD and his review. The user
    decides at the gate: approve when happy, or ask for changes (the conversation goes to Dev with them)."""
    pid = ctx.project_id
    state = aws_access.load(pid, STATE) or {}
    chat = state.get("chat") or []
    if not chat or chat[-1]["role"] != "user":
        return
    _code, _meta, _tests, _cov, _gates, material = await _material(pid)
    review = load_review(pid) or {}
    messages: list[dict] = [
        {"role": "user", "content": material + "\n\n# Your review of this code\n```json\n"
         + json.dumps({k: review.get(k) for k in ("verdict", "summary", "checks", "findings", "design_notes")}, ensure_ascii=False)[:20000]
         + "\n```\n\nThe user, who owns this project and approves the deploy, will now ask you about this code."},
        {"role": "assistant", "content": "Understood. I have the code, the tests, my LLD and my review in front of me."}]
    for m in chat:
        messages.append({"role": "user" if m["role"] == "user" else "assistant", "content": m["text"]})
    await ctx.set_agent("ta", "working", "💬 Reviewing your question about the code")
    try:
        res = await run_loop(project_id=pid, agent="ta",
                             system=[{"type": "text", "text": llm.prompt("ta_review_chat.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=messages, max_turns=1, purpose="code-review-chat", effort="medium", narrate=False)
        answer = res.text.strip() or "(no answer)"
    except Exception as exc:  # the question stays; the user can ask again
        chat.append({"role": "archie", "text": f"Sorry, I couldn't answer that just now ({str(exc)[:200]}). Please ask again.",
                     "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "error": True})
        aws_access.save(pid, {**state, "chat": chat}, STATE)
        await ctx.set_agent("ta", "needs_approval", "Code review: waiting for you")
        raise
    chat.append({"role": "archie", "text": answer[:ANSWER_LIMIT * 2], "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})
    aws_access.save(pid, {**state, "chat": chat}, STATE)
    await crewchat.say(pid, "ta", "user", answer[:1500], "answer")
    await ctx.set_agent("ta", "needs_approval", "Answered your question: the code review waits for your decision")
    await ctx.emit("build.updated", "Archie answered your question about the code", agent="ta")


def discussion(project_id: str) -> str:
    """The user's conversation with Archie about this version's code, for Dev when the user asks for changes."""
    state = aws_access.load(project_id, STATE) or {}
    if state.get("version") != ProjectStore(project_id).manifest()["current_version"]:
        return ""
    return "\n\n".join(f"{'The user' if m['role'] == 'user' else 'Archie'}: {m['text'][:3000]}" for m in state.get("chat") or [] if not m.get("error"))
