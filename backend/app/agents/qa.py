"""Quinn — QA Engineer, working like the team's manual tester.

qa.plan: once Dev's code is deployed and sanity-checked, Quinn writes the test plan (every scenario: happy path,
negative, edge cases, error handling, logging, security; each with steps, data and the expected result) → the
"test_plan" gate: the user, as test lead, approves it or asks for changes. No test runs before that. The plan is redone
when the requirement or the mapping changes.

qa.live: Quinn runs every approved scenario on the real flow in AWS with his own role (HTTPS calls to the project's own
endpoints, its queues, its functions and logs). Every failed scenario becomes a ticket (TKT-00N) for Dev (code) or
Terra (infrastructure); tickets that come back resolved are retested and closed, or reopened back to the fixer. Each run
is recorded; the test sign-off report (services/signoff) is rebuilt after every run. Open tickets → "live_bugs" gate
(approve → Orion sends each to its fixer); everything green → Quinn's sign-off statement → "live" (the final gate: the
user signs off the testing). Quinn never edits code or infrastructure.

qa.test (older projects): component-level tests (qa/) of the handler in the sandbox with moto, bugs BUG-00N → "qa_bugs"
or "qa".
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.buildkit import CHANGED_FILES, DELETE, WorkingSet, context, examples_fixture, files_under, read
from app.agents.intake import load_intake
from app.orchestrator import crewchat, flow
from app.orchestrator.runner import JobContext, handler
from app.services.storage import ProjectStore
from app.tools import pytest_runner, sandbox, terraform

OWN = ("qa/",)
QA_JSON = "reports/qa.json"
RUN_TESTS = Tool(
    name="run_tests",
    description="Run your qa/ tests against Dev's code in the sandbox (moto for AWS, no network). Send only the qa/ files you "
                "added or changed since your last run; the platform keeps the others.",
    schema=obj({"files": CHANGED_FILES, "delete": DELETE}, required=["files"]),  # delete is optional
)
SUBMIT = Tool(
    name="submit_qa",
    terminal=True,
    description="Submit the test plan, your qa/ tests as they stand (plus any last changes in `files`) and a bug for every test "
                "that still fails. The platform reruns and checks.",
    schema=obj({
        "summary": {"type": "string", "description": "2-3 sentences: what you tested and the verdict"},
        "files": CHANGED_FILES, "delete": DELETE,
        "plan": {"type": "array", "items": obj({
            "id": {"type": "string", "description": "QA-001…"}, "title": {"type": "string"},
            "type": {"type": "string", "enum": ["positive", "negative", "edge", "failure", "non-functional"]},
            "expected": {"type": "string"}})},
        "bugs": {"type": "array", "description": "One per still-failing test (empty when all pass)", "items": obj({
            "test_id": {"type": "string", "description": "the failing test's name"}, "title": {"type": "string"},
            "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
            "steps": {"type": "string"}, "expected": {"type": "string"}, "actual": {"type": "string"}})},
    }),
)


def load_qa(project_id: str) -> dict | None:
    raw = read(ProjectStore(project_id), QA_JSON)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _match(test_id: str, results: list[dict]) -> dict | None:
    key = test_id.split("::")[-1].split("[")[0].strip().lower()
    return next((t for t in results if key and key in t["id"].lower()), None)


@handler("qa.test", resumable=False)
async def test(ctx: JobContext) -> None:
    from app.agents.de import FIXTURE, summarise

    pid = ctx.project_id
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    intake = await load_intake(pid)
    previous = load_qa(pid) or {}
    old_bugs = previous.get("bugs") or []
    code_files = {**files_under(store, ("src/", "layers/", "tests/")), FIXTURE: examples_fixture(pid)}
    box: dict = {"runs": 0}
    prev_files = files_under(store, OWN)
    work = WorkingSet(prev_files, OWN, "Quinn")  # the current qa/ tests are shown to Quinn below

    async def run_tests(a: dict):
        files = work.apply(a)
        box["runs"] += 1
        await ctx.set_agent("qa", "working", f"🧪 Running the QA suite (run {box['runs']}, {len(files)} files)")
        r = await pytest_runner.run({**code_files, **files}, ["qa"])
        await crewchat.say(pid, "qa", "crew", (f"⚠️ QA run {box['runs']}: no tests ran" if not r["total"] else
                           f"🧪 QA run {box['runs']}: {r['passed']}/{r['total']} passed"
                           + (f", {r['failed'] + r['error']} failing" if r["failed"] + r["error"] else " ✅")), "work")
        return summarise(r, 20)

    async def verify(a: dict):
        files = work.apply(a)
        await ctx.set_agent("qa", "working", "✅ Final QA run")
        r = await pytest_runner.run({**code_files, **files}, ["qa"])
        if not r["tests"]:
            raise AgentError("No QA tests ran: " + r["output"][-1500:])
        failing = [t for t in r["tests"] if t["outcome"] in ("failed", "error")]
        reported = {id(m) for b in a["bugs"] if (m := _match(b["test_id"], failing))}
        missing = [t["id"] for t in failing if id(t) not in reported]
        if missing:
            raise AgentError(f"These tests fail but have no bug: {missing}. Either fix your test (if the test is wrong) or file a bug.")
        wrong = [b["test_id"] for b in a["bugs"] if not _match(b["test_id"], failing)]
        if wrong:
            raise AgentError(f"Bugs filed for tests that pass or don't exist: {wrong}. Only failing tests get bugs.")
        ids = " ".join(t["id"] for t in r["tests"]).lower()
        uncovered = [p["id"] for p in a["plan"] if p["id"].lower().replace("-", "_") not in ids]
        if uncovered:
            raise AgentError(f"Put each plan id in a test name (e.g. test_qa_001_...): no test for {uncovered}")
        box["files"], box["result"] = files, r

    ask = (f"# Project id: {pid}\n" + context(store, intake.requirement_md, code=True, own="qa")
           + f"\n\n# Dev's unit tests (for reference: how they import the handler)\n"
           + "\n\n".join(f"## {p}\n```python\n{c[:8000]}\n```" for p, c in code_files.items() if p.startswith("tests/") and p.endswith(".py"))
           + f"\n\n# Atlas's worked examples ({FIXTURE})\n```json\n{code_files[FIXTURE][:20000]}\n```")
    if old_bugs:
        ask += "\n\n# Bugs from your last round (retest them; their tests must stay in the suite)\n" + "\n".join(
            f"- {b['id']} [{b['status']}] {b['title']} (test {b['test_id']})" for b in old_bugs)
    if prev_files:
        ask += ("\n\n# Your current qa/ tests (already in your files: send only what you change)\n"
                + "\n\n".join(f"## {p}\n```python\n{c[:15000]}\n```" for p, c in prev_files.items()))
    ask += "\n\nWrite the test plan and tests, run them, then call submit_qa."

    retest = bool(old_bugs)
    await ctx.set_agent("qa", "working", f"🔁 Retesting {version}" if retest else f"🧭 Planning the tests for {version}")
    await ctx.set_project(status="running", last_activity="Quinn is testing the flow")
    await crewchat.say(pid, "qa", "de" if retest else "cto", f"Retesting {version}, including the {len(old_bugs)} earlier bug(s)." if retest else
                       f"Testing {version} end to end like API Gateway calls it, with moto as AWS. Any defect becomes a bug for Dev.", "ack")
    try:
        if not sandbox.available():
            raise AgentError("The code sandbox (Docker) isn't available on this host")
        res = await run_loop(project_id=pid, agent="qa",
                             system=[{"type": "text", "text": llm.prompt("qa.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}],
                             tools=[replace(RUN_TESTS, handler=run_tests), replace(SUBMIT, handler=verify)],
                             max_turns=16, purpose="qa-retest" if retest else "qa")
        data = res.terminal.get("submit_qa")
        if not data or "files" not in box:
            raise AgentError("Quinn finished without submitting a checked QA run.")
        r = box["result"]
        store.replace_files(OWN, box["files"])

        # bug ledger: earlier bugs whose tests now pass are fixed; new failures get the next ids
        bugs = []
        for b in old_bugs:
            t = _match(b["test_id"], r["tests"])
            if b["status"] == "open" and t and t["outcome"] == "passed":
                b = {**b, "status": "fixed", "fixed_in": version}
            bugs.append(b)
        n = len(bugs)
        for nb in data["bugs"]:
            existing = next((b for b in bugs if b["status"] == "open" and b["test_id"].lower() in nb["test_id"].lower()), None)
            if existing:
                existing.update({k: nb[k] for k in ("actual", "steps")})
                continue
            n += 1
            bugs.append({"id": f"BUG-{n:03d}", "status": "open", "found_in": version, **nb})
        open_bugs = [b for b in bugs if b["status"] == "open"]
        results = {p["id"]: next(({"outcome": t["outcome"], "test": t["id"]} for t in r["tests"]
                                  if p["id"].lower().replace("-", "_") in t["id"].lower()), None) for p in data["plan"]}
        report = {"summary": data["summary"], "plan": data["plan"], "results": results, "bugs": bugs, "version": version,
                  "tests": r, "runs": box["runs"]}
        store.write(QA_JSON, json.dumps(report, indent=2, ensure_ascii=False))
        for b in bugs:
            store.write(f"bugs/{b['id']}.md", f"# {b['id']}: {b['title']}\n\n| | |\n|---|---|\n| Status | **{b['status']}** |\n"
                        f"| Severity | {b['severity']} |\n| Found in | {b.get('found_in', '')} |\n| Fixed in | {b.get('fixed_in', '-')} |\n"
                        f"| Test | `{b['test_id']}` |\n\n## Steps to reproduce\n{b['steps']}\n\n## Expected\n{b['expected']}\n\n## Actual\n{b['actual']}\n")
        store.write("reports/qa_report.md", qa_markdown(report))
        store.append_changelog(version, [f"QA by Quinn: {r['passed']}/{r['total']} tests passed, {len(open_bugs)} open bug(s)"])
        fixed = [b["id"] for b in bugs if b.get("fixed_in") == version]
        line = f"{r['passed']}/{r['total']} QA tests passed · {len(open_bugs)} open bug(s)" + (f" · {len(fixed)} fixed" if fixed else "")
        await ctx.set_agent("qa", "needs_approval", line)
        await ctx.set_project(progress=round(5.2 / 6, 3))
        await ctx.emit("build.updated", f"Quinn: {line}", agent="qa")
        if fixed:
            await crewchat.say(pid, "qa", "de", f"Confirmed fixed in {version}: {', '.join(fixed)}. Thanks Dev.", "fix")
        for b in open_bugs:
            await crewchat.say(pid, "qa", "de", f"🐞 **{b['id']}** ({b['severity']}): {b['title']}. Expected: {b['expected']} Actual: {b['actual']}",
                               "issue", files=[f"bugs/{b['id']}.md"])
        await crewchat.say(pid, "qa", "cto", f"QA done for {version}: {line}. {data['summary']}", "handoff", files=["reports/qa_report.md"])
        if open_bugs:
            await flow.request_approval(pid, "qa_bugs", f"Quinn found {len(open_bugs)} bug(s): let Dev fix them", data["summary"],
                                        ["reports/qa_report.md", *[f"bugs/{b['id']}.md" for b in open_bugs]])
        else:
            await flow.request_approval(pid, "qa", f"QA passed ({r['passed']}/{r['total']} tests, {version})", data["summary"], ["reports/qa_report.md"])
    except Exception as exc:
        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("qa", "blocked" if blocked else "failed", str(exc)[:240])
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Quinn: {str(exc)[:200]}")
        await ctx.emit("build.updated", f"Quinn hit a problem: {str(exc)[:200]}", agent="qa")
        await crewchat.say(pid, "qa", "cto", f"I'm stuck: {str(exc)[:300]}", "issue")
        raise


# ── live end-to-end tests in AWS (Quinn's own role, made by Orion); failures are tickets ────────────────────────────
LIVE_JSON = "reports/live_qa.json"
PROGRESS = "qa_progress"  # deploy/qa_progress.json: the live run so far, scenario by scenario (the Testing tab shows it)
# One scenario at a time (10-05: Quinn tested all 27 scenarios but couldn't hand them in as one 27-item list: the model kept
# dropping it, 9 tries, out of turns). The platform builds the report from these records, and the user sees the run live.
RECORD = Tool(
    name="record_scenario",
    description="Record a scenario as you go: status 'running' when you start it, then 'passed' or 'failed' with what you did "
                "and saw, or 'not_run' with why. Call it in the same turn as your other calls. The report is built from these.",
    schema=obj({
        "id": {"type": "string", "description": "the plan's scenario id (TC-01…), or EX-01… for an extra check outside the plan"},
        "status": {"type": "string", "enum": ["running", "passed", "failed", "not_run"]},
        "title": {"type": "string", "description": "the scenario's title (short)"},
        "did": {"type": "string", "description": "what you did, in plain words (empty for 'running')"},
        "saw": {"type": "string", "description": "what came back: the result, the file or message, the log line (evidence); for "
                                                 "'not_run' why it couldn't run; empty for 'running'"},
    }),
)
SUBMIT_LIVE = Tool(
    name="submit_live",
    terminal=True,
    description="Finish the live test run. Every scenario must already be recorded with record_scenario (the platform has them "
                "all: don't repeat them here). Give the summary, a new ticket for every failed scenario that no open ticket "
                "covers yet, and a verdict for every ticket waiting for your retest.",
    schema=obj({
        "summary": {"type": "string", "description": "2-3 sentences: what you checked live and the verdict"},
        "bugs": {"type": "array", "description": "A new ticket per failed check not already covered by an open ticket (empty when "
                 "all pass)", "items": obj({
                     "check_id": {"type": "string"}, "title": {"type": "string"},
                     "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                     "area": {"type": "string", "enum": ["code", "infra"], "description": "code → Dev (the function's behaviour, "
                              "its output, its logs); infra → Terra (permissions, triggers, timeouts, memory, queue or API settings)"},
                     "steps": {"type": "string"}, "expected": {"type": "string"}, "actual": {"type": "string"}})},
        "tickets": {"type": "array", "description": "One verdict per ticket waiting for your retest (empty if none)", "items": obj({
            "ticket": {"type": "string", "description": "TKT-001…"}, "passed": {"type": "boolean"},
            "evidence": {"type": "string"}})},
        "left_for_user": {"type": "string", "description": "which sample result you left where for the user, or why none"},
        "risks": {"type": "array", "items": {"type": "string"},
                  "description": "residual risks the user should know (what couldn't be proven live and why); empty if none"},
        "signoff": {"type": "string", "description": "your sign-off statement as the tester, only when every scenario passed and no "
                    "ticket is open: what you tested, on which version, and that it is fit to go live. Empty otherwise."},
    }),
)


def load_live(project_id: str) -> dict | None:
    raw = read(ProjectStore(project_id), LIVE_JSON)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


# ── the test plan: Quinn writes the scenarios, the user (the test lead) approves them before any test runs ─────────────
PLAN_JSON, PLAN_MD = "reports/test_plan.json", "reports/test_plan.md"
CATEGORIES = ["happy path", "negative", "edge case", "error handling", "logging", "security", "non-functional"]
CASE = obj({
    "id": {"type": "string", "description": "TC-01, TC-02 … (keep an id when you revise the plan; new scenarios get new ids)"},
    "title": {"type": "string"},
    "category": {"type": "string", "enum": CATEGORIES},
    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
    "requirement_ref": {"type": "string", "description": "where it comes from: the requirement section, the mapping row, or Atlas's example name"},
    "preconditions": {"type": "string", "description": "what must be true before (or empty)"},
    # plain words first (user, 10-05: "a bit confusing when reading… show the flow and the expected output with one example,
    # so the user understands what the tester is doing")
    "flow": {"type": "string", "description": "what you'll do, in plain words a non-developer follows: 2-5 short numbered steps, "
                                             "e.g. '1. Note the newest result file (count 5). 2. Run the function once. 3. Check a new file appeared.'"},
    "example": {"type": "string", "description": "one concrete example: this input or starting state → this exact result, e.g. "
                                                "'newest file ..._count-0005_Mon.txt → new file ..._count-0006_Mon.txt containing \"count: 6\\nday: Mon\"'"},
    "steps": {"type": "string", "description": "exactly how you will run it (for the record): the call, the input, what you read afterwards"},
    "test_data": {"type": "string", "description": "the exact input, or the name of Atlas's worked example"},
    "expected": {"type": "string", "description": "what must happen: the response, what lands where (or nothing), the log line"},
})
SUBMIT_TEST_PLAN = Tool(
    name="submit_test_plan",
    terminal=True,
    description="Submit the test plan for the test lead (the user) to approve. No test runs before it is approved.",
    schema=obj({
        "summary": {"type": "string", "description": "2-3 sentences: what the plan covers"},
        "scope": {"type": "string", "description": "what is tested, end to end"},
        "approach": {"type": "string", "description": "how: from the outside like the real caller, on the live flow in AWS, with your own role"},
        "out_of_scope": {"type": "array", "description": "what you can't or won't test, each with why (empty if none)",
                         "items": obj({"what": {"type": "string"}, "why": {"type": "string"}})},
        "entry_criteria": {"type": "array", "items": {"type": "string"}},
        "exit_criteria": {"type": "array", "items": {"type": "string"}},
        "cases": {"type": "array", "items": CASE},
        "changes": {"type": "string", "description": "on a revision: what you changed and why (the lead's comments); else empty"},
    }),
)


def load_plan(project_id: str) -> dict | None:
    raw = read(ProjectStore(project_id), PLAN_JSON)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def plan_basis(project_id: str) -> str:
    """What a test plan is written against: the requirement and the mapping. If either changes, the plan is redone."""
    import hashlib

    store = ProjectStore(project_id)
    return hashlib.sha256((read(store, "00_requirement.md") + read(store, "mapping/01_data_mapping.json")).encode()).hexdigest()[:16]


def plan_ready(project_id: str, plan: dict | None = None) -> bool:
    plan = plan if plan is not None else load_plan(project_id)
    return bool(plan) and plan.get("status") == "approved" and plan.get("basis") == plan_basis(project_id)


def mark_plan_approved(project_id: str, approval) -> None:
    plan = load_plan(project_id)
    if not plan:
        return
    when = approval.decided_at.strftime("%Y-%m-%d %H:%M UTC") if approval.decided_at else ""
    plan.update(status="approved", approved_at=when, approval_comment=approval.comment or "")
    store = ProjectStore(project_id)
    store.write(PLAN_JSON, json.dumps(plan, indent=2, ensure_ascii=False))
    store.write(PLAN_MD, plan_markdown(plan))


def plan_markdown(p: dict) -> str:
    esc = lambda s: str(s or "").replace("|", "\\|").replace("\n", " ")  # noqa: E731
    status = f"✅ Approved by the test lead on {p['approved_at']}" if p.get("status") == "approved" else "⏳ Waiting for the test lead's approval"
    lines = [f"# Test plan · {p['version']}", "", f"**Quinn (QA Engineer)** · {status}", "", p["summary"], "",
             "## Scope", "", p["scope"], "", "## Approach", "", p["approach"], ""]
    if p.get("changes"):
        lines += ["## What changed in this revision", "", p["changes"], ""]
    if p.get("out_of_scope"):
        lines += ["## Not in scope", "", *[f"- **{x['what']}**: {x['why']}" for x in p["out_of_scope"]], ""]
    lines += ["## Entry criteria", "", *[f"- {c}" for c in p.get("entry_criteria", [])], "",
              "## Exit criteria", "", *[f"- {c}" for c in p.get("exit_criteria", [])], ""]
    cats: dict[str, int] = {}
    for c in p["cases"]:
        cats[c["category"]] = cats.get(c["category"], 0) + 1
    lines += [f"## Scenarios ({len(p['cases'])}: {', '.join(f'{n} {k}' for k, n in cats.items())})", "",
              "| ID | Scenario | Category | Priority | From | Expected |", "|---|---|---|---|---|---|"]
    lines += [f"| {c['id']} | {esc(c['title'])} | {c['category']} | {c['priority']} | {esc(c['requirement_ref'])} | {esc(c['expected'])} |"
              for c in p["cases"]]
    for c in p["cases"]:
        lines += ["", f"### {c['id']}: {c['title']}", "", f"- **Category / priority:** {c['category']} · {c['priority']}",
                  f"- **From:** {c['requirement_ref']}"]
        if c.get("flow"):
            lines += [f"- **What Quinn does:** {c['flow']}"]
        if c.get("example"):
            lines += [f"- **Example:** {c['example']}"]
        if c.get("preconditions"):
            lines.append(f"- **Preconditions:** {c['preconditions']}")
        lines += [f"- **Exact steps:** {c['steps']}", f"- **Test data:** {c['test_data']}", f"- **Expected:** {c['expected']}"]
    return "\n".join(lines) + "\n"


def load_runs(project_id: str) -> list[dict]:
    from app.services import aws_access

    return (aws_access.load(project_id, "qa_runs") or {}).get("runs", [])


def _save_run(project_id: str, run: dict) -> None:
    from app.services import aws_access

    runs = load_runs(project_id)
    aws_access.save(project_id, {"runs": [*runs, {**run, "run": len(runs) + 1}]}, "qa_runs")


@handler("qa.plan", resumable=False)
async def plan(ctx: JobContext) -> None:
    """Quinn writes the test plan (every scenario: happy path, negative, edge cases, error handling, logging, security)
    → the test_plan gate: the user, as test lead, approves it (or asks for changes) before any test runs."""
    import re

    from app.agents import livekit
    from app.agents.ba import load_mapping
    from app.agents.de import FIXTURE
    from app.services import aws_access, inventory
    from app.services import tickets as tk

    pid = ctx.project_id
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    intake = await load_intake(pid)
    previous = load_plan(pid)
    feedback = ctx.payload.get("feedback") or ""
    examples = [s["name"] for s in (load_mapping(pid) or {}).get("samples", [])]
    code = aws_access.load(pid, "code") or {}

    async def verify(a: dict):
        ids = [c["id"] for c in a["cases"]]
        bad = [i for i in ids if not re.fullmatch(r"TC-\d{2,3}", i)]
        if bad:
            raise AgentError(f"Scenario ids are TC-01, TC-02 …: {bad}")
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise AgentError(f"Each scenario needs its own id: {dupes} used twice")
        if len(ids) < 5:
            raise AgentError("A test plan covers every scenario: happy path, negative, edge cases, error handling, logging. Only "
                             f"{len(ids)} so far.")
        cats = {c["category"] for c in a["cases"]}
        for need in ("happy path", "negative"):
            if need not in cats:
                raise AgentError(f"No '{need}' scenario yet: every plan has at least one.")
        text = " ".join(f"{c['test_data']} {c['requirement_ref']} {c['steps']} {c['title']}" for c in a["cases"]).lower()
        missing = [n for n in examples if n.lower() not in text]
        if missing:
            raise AgentError(f"Every worked example Atlas wrote is a scenario (name it in test_data). Not covered yet: {missing}")

    ask = (f"# Project id: {pid}\n" + context(store, intake.requirement_md, own="qa")
           + f"\n\n# The live deployment\n" + livekit.deployment_brief(aws_access.load(pid, "deploy") or {}, inventory.load(pid))
           + f"\n\n# Atlas's worked examples ({FIXTURE}): each one is a scenario\n```json\n{examples_fixture(pid)[:15000]}\n```")
    if (code.get("sanity") or {}).get("summary"):
        ask += f"\n\n# Dev's sanity check after his deploy\n{code['sanity']['summary']}"
    rows = await tk.listing(pid, with_comments=False)
    if rows:
        ask += "\n\n# Tickets so far (their scenarios belong in the plan)\n" + "\n".join(f"- {t['label']} [{t['status']}] {t['title']}" for t in rows)
    if previous:
        ask += "\n\n# Your previous plan (keep the ids of scenarios you keep)\n```json\n" + json.dumps(previous.get("cases", []), ensure_ascii=False)[:20000] + "\n```"
    if feedback:
        ask += f"\n\n# The test lead's comments on your plan (address every point, say how in `changes`)\n{feedback}"
    ask += "\n\nWrite the test plan, then call submit_test_plan."

    await ctx.set_agent("qa", "working", f"📝 Writing the test plan for {version}" if not feedback else "📝 Revising the test plan")
    await ctx.set_project(status="running", last_activity="Quinn is writing the test plan")
    await crewchat.say(pid, "qa", "cto", ("Revising the test plan with the lead's comments." if feedback else
                                          f"Writing the test plan for {version}: every scenario, for the user (test lead) to approve before I test."), "ack")
    try:
        res = await run_loop(project_id=pid, agent="qa",
                             system=[{"type": "text", "text": llm.prompt("qa_plan.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}],
                             tools=[replace(SUBMIT_TEST_PLAN, handler=verify)], max_turns=5, purpose="qa-plan", effort="medium")
        data = res.terminal.get("submit_test_plan")
        if not data:
            raise AgentError("Quinn finished without submitting the test plan.")
        p = {**data, "version": version, "basis": plan_basis(pid), "status": "proposed",
             "written_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
        store.write(PLAN_JSON, json.dumps(p, indent=2, ensure_ascii=False))
        store.write(PLAN_MD, plan_markdown(p))
        cats: dict[str, int] = {}
        for c in data["cases"]:
            cats[c["category"]] = cats.get(c["category"], 0) + 1
        line = f"{len(data['cases'])} scenarios ({', '.join(f'{n} {k}' for k, n in cats.items())})"
        await ctx.set_agent("qa", "needs_approval", f"Test plan ready: {line}")
        await ctx.emit("build.updated", f"Quinn's test plan: {line}", agent="qa")
        await crewchat.say(pid, "qa", "user", f"My test plan for {version} is ready: {line}. Please review it as test lead: approve it and "
                           "I start testing live, or tell me what to add or change.", "handoff", files=[PLAN_MD])
        await flow.request_approval(pid, "test_plan", f"Quinn's test plan: {line}", data["summary"]
                                    + (f" Changes: {data['changes']}" if data.get("changes") else "")
                                    + " Approving lets Quinn run every scenario live in AWS.", [PLAN_MD])
    except Exception as exc:
        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("qa", "blocked" if blocked else "failed", str(exc)[:240])
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Quinn (test plan): {str(exc)[:200]}")
        await crewchat.say(pid, "qa", "cto", f"I couldn't finish the test plan: {str(exc)[:300]}", "issue")
        raise


@handler("qa.live", resumable=False)
async def live(ctx: JobContext) -> None:
    import asyncio

    from app.agents import livekit
    from app.agents.de import FIXTURE
    from app.services import aws_access, inventory
    from app.services import tickets as tk

    pid = ctx.project_id
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    intake = await load_intake(pid)
    deployed = aws_access.load(pid, "deploy") or {}
    previous = load_live(pid) or {}
    role = aws_access.role_name(pid, "qa")
    rows = await tk.listing(pid)
    waiting = [t for t in rows if t["assignee"] == "qa" and t["status"] in ("resolved", "open", "reopened", "in_progress")]
    elsewhere = [t for t in rows if t["assignee"] != "qa" and t["status"] in tk.ACTIVE]
    approved = load_plan(pid)
    cases = approved["cases"] if plan_ready(pid, approved) else []
    by_case = {c["id"]: c for c in cases}
    box: dict = {}
    results: dict[str, dict] = {}  # id → {id, title, status, did, saw}: what record_scenario got, in recording order
    current: dict = {"id": None}

    def save_progress() -> None:
        done = [r for r in results.values() if r["status"] != "running"]
        aws_access.save(pid, {"version": version, "total": len(cases) or len(results), "current": current["id"],
                              "done": len(done), "passed": sum(r["status"] == "passed" for r in done),
                              "failed": sum(r["status"] == "failed" for r in done), "results": list(results.values()),
                              "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, PROGRESS)

    async def record(a: dict):
        sid = a["id"].strip().upper()
        if cases and sid not in by_case and not sid.startswith("EX-"):
            raise AgentError(f"{sid} isn't in the approved plan: use its ids ({', '.join(list(by_case)[:5])}…), or EX-01… for an extra check")
        title = a["title"] or (by_case.get(sid) or {}).get("title", sid)
        if a["status"] == "running":
            current["id"] = sid
            results.setdefault(sid, {"id": sid, "title": title, "status": "running", "did": "", "saw": ""})
        else:
            if not a["saw"].strip():
                raise AgentError("Say what you saw (the evidence), or for not_run why it couldn't run.")
            results[sid] = {"id": sid, "title": title, "status": a["status"], "did": a["did"].strip(), "saw": a["saw"].strip()}
            if current["id"] == sid:
                current["id"] = None
        save_progress()
        done = sum(r["status"] != "running" for r in results.values())
        total = len(cases) or len(results)
        if a["status"] == "running":
            await ctx.set_agent("qa", "working", f"🧪 {sid} · {title[:70]} ({done}/{total} done)")
        else:
            mark = {"passed": "✅", "failed": "❌", "not_run": "⏭"}[a["status"]]
            await ctx.set_agent("qa", "working", f"{mark} {sid} {a['status'].replace('_', ' ')} ({done}/{total} done)")
            await crewchat.say(pid, "qa", "crew", f"{mark} **{sid}** {title}: {a['saw'][:300]}", "work")
        await ctx.emit("testing.progress", "", agent="qa", done=done, total=total)
        return f"Recorded {sid}: {a['status']}. {done}/{total} scenarios done."

    async def verify(a: dict):
        if not box.get("calls"):
            raise AgentError("Run the checks against the live flow first: evidence must come from real calls.")
        finished = [r for r in results.values() if r["status"] != "running"]
        a["checks"] = [{"id": r["id"], "title": r["title"], "passed": r["status"] == "passed", "evidence": r["saw"], "did": r["did"],
                        "expected": (by_case.get(r["id"]) or {}).get("expected", ""), "flow": (by_case.get(r["id"]) or {}).get("flow", "")}
                       for r in finished if r["status"] in ("passed", "failed")]
        a["not_run"] = [{"id": r["id"], "why": r["saw"]} for r in finished if r["status"] == "not_run"]
        if not finished:
            raise AgentError("Record every scenario with record_scenario as you test it (passed/failed with what you saw, or not_run "
                             "with why); then call submit_live.")
        if cases:
            planned = set(by_case)
            given = {r["id"] for r in finished}
            if planned - given:
                raise AgentError(f"Record every scenario of the approved plan with record_scenario (passed/failed, or not_run with "
                                 f"why) before submitting: missing {sorted(planned - given)}")
        failing = [c["id"] for c in a["checks"] if not c["passed"]]
        if not failing and not elsewhere and not a["signoff"].strip():
            raise AgentError("Every scenario passed and no ticket is open: write your sign-off statement as the tester in `signoff`.")
        if failing and a["signoff"].strip():
            raise AgentError(f"You can't sign off with failing scenarios ({failing}): leave `signoff` empty.")
        ids = {t["label"] for t in waiting}
        given = {x["ticket"].upper() for x in a["tickets"]}
        if ids - given:
            raise AgentError(f"Give a verdict (passed + evidence) for every ticket waiting for your retest: missing {sorted(ids - given)}")
        if given - ids:
            raise AgentError(f"These tickets aren't waiting for you: {sorted(given - ids)}. Only retest {sorted(ids) or 'none'}.")
        failed = {c["id"] for c in a["checks"] if not c["passed"]}
        covered = {b["check_id"] for b in a["bugs"]} | {t["check_id"] for t in rows if t["status"] in tk.ACTIVE and t.get("check_id")} \
            | {t["check_id"] for t in waiting if t.get("check_id")}
        if failed - covered:
            raise AgentError(f"These checks failed but no ticket covers them: {sorted(failed - covered)}")
        wrong = {b["check_id"] for b in a["bugs"]} - failed
        if wrong:
            raise AgentError(f"Tickets filed for checks that passed or don't exist: {sorted(wrong)}")

    ask = (f"# Project id: {pid}\n" + context(store, intake.requirement_md, code=True, own="qa")
           + f"\n\n# The live deployment ({deployed.get('version')}, region eu-west-1)\n" + livekit.deployment_brief(deployed, inventory.load(pid))
           + f"\n\n# Atlas's worked examples ({FIXTURE})\n```json\n{examples_fixture(pid)[:15000]}\n```")
    if waiting:
        ask += "\n\n# Tickets waiting for your retest (a verdict for each in `tickets`)\n" + tk.prompt_block(waiting)
    if elsewhere:
        ask += ("\n\n# Tickets still open with Dev or Terra (don't file them again; their checks may still fail)\n"
                + "\n".join(f"- {t['label']} ({t['assignee']}, check {t.get('check_id') or '-'}): {t['title']}" for t in elsewhere))
    if cases:
        ask += ("\n\n# The approved test plan: run every scenario, record each with record_scenario by its id (TC-…)\n```json\n"
                + json.dumps([{k: c.get(k, "") for k in ("id", "title", "category", "priority", "steps", "test_data", "expected")} for c in cases],
                             ensure_ascii=False)[:30000] + "\n```")
    elif previous.get("checks"):
        ask += "\n\n# Your previous checks (reuse their ids)\n" + "\n".join(f"- {c['id']}: {c['title']}" for c in previous["checks"])
    if ctx.payload.get("feedback"):
        ask += f"\n\n# The test lead's comments on your last results (address them)\n{ctx.payload['feedback']}"
    ask += ("\n\nTest the live flow scenario by scenario: record_scenario 'running' as you start each one, then 'passed' / "
            "'failed' with what you did and saw (or 'not_run' with why). When every scenario is recorded, call submit_live.")

    retest = bool(waiting)
    aws_access.save(pid, {"version": version, "total": len(cases), "current": None, "done": 0, "passed": 0, "failed": 0, "results": [],
                          "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, PROGRESS)
    await ctx.set_agent("qa", "working", f"🔁 Retesting {', '.join(t['label'] for t in waiting)} live" if retest else f"🛰 Testing the live flow in AWS ({version})")
    await ctx.set_project(status="running", last_activity="Quinn is testing the live flow in AWS")
    await crewchat.say(pid, "qa", "cto", f"Testing {deployed.get('version')} live in AWS as `{role}`: real calls, real queues, real logs."
                       + (f" Retesting {', '.join(t['label'] for t in waiting)}." if retest else ""), "ack")
    try:
        creds = await asyncio.to_thread(aws_access.assume, pid, "qa")
        res = await run_loop(project_id=pid, agent="qa",
                             system=[{"type": "text", "text": llm.prompt("qa_live.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}],
                             tools=[*livekit.tools(pid, "qa", creds, deployed.get("outputs") or {}, box), replace(RECORD, handler=record),
                                    replace(SUBMIT_LIVE, handler=verify)],
                             max_turns=60, purpose="qa-live-retest" if retest else "qa-live")
        data = res.terminal.get("submit_live")
        if not data:
            raise AgentError("Quinn finished without submitting live results.")
        by_label = {t["label"]: t for t in waiting}
        closed, reopened, created = [], [], []
        for v in data["tickets"]:
            t = by_label.get(v["ticket"].upper())
            if not t:
                continue
            if v["passed"]:
                await tk.change(t["id"], "qa", f"Retested live in {version}: fixed ✅. {v['evidence'][:800]}", status="closed")
                closed.append(t["label"])
            else:
                back = tk.FIXER.get(t["area"]) or (t["reporter"] if t["reporter"] in tk.AGENTS else "de")
                await tk.change(t["id"], "qa", f"Retested live in {version}: still fails ❌. {v['evidence'][:800]}", status="reopened",
                                assignee=back)
                reopened.append(t["label"])
        for b in data["bugs"]:
            t = await tk.create(pid, title=b["title"], steps=b["steps"], expected=b["expected"], actual=b["actual"],
                                severity=b["severity"], area=b["area"], reporter="qa", check_id=b["check_id"], version=version)
            created.append(tk.label(t))
        rows = await tk.listing(pid)
        active = [t for t in rows if t["status"] in tk.ACTIVE or (t["status"] == "resolved")]
        ok = sum(c["passed"] for c in data["checks"])
        report = {"summary": data["summary"], "checks": data["checks"], "version": version, "calls": box.get("calls", 0),
                  "left_for_user": data.get("left_for_user") or "", "not_run": data.get("not_run") or [], "risks": data.get("risks") or [],
                  "signoff": (data.get("signoff") or "").strip(), "plan_version": approved.get("version") if cases else None,
                  "deployed_version": deployed.get("version"), "role": role, "closed": closed, "reopened": reopened, "created": created,
                  "open": [{"label": t["label"], "title": t["title"], "assignee": t["assignee"], "severity": t["severity"],
                            "status": t["status"]} for t in active]}
        store.write(LIVE_JSON, json.dumps(report, indent=2, ensure_ascii=False))
        store.write("reports/live_qa.md", live_markdown(report))
        total = len(cases) or len(data["checks"])
        _save_run(pid, {"at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "version": version, "deployed_version": deployed.get("version"),
                        "kind": "retest" if retest else "full", "passed": ok, "failed": len(data["checks"]) - ok,
                        "not_run": len(report["not_run"]), "created": created, "closed": closed, "reopened": reopened,
                        "checks": [{"id": c["id"], "passed": c["passed"]} for c in data["checks"]], "calls": box.get("calls", 0)})
        from app.services import signoff

        await signoff.write_test_signoff(pid)  # the report grows with every run; the user's approval completes it
        line = (f"{ok}/{total} scenarios passed" + (f" · {len(created)} new ticket(s)" if created else "")
                + (f" · closed {', '.join(closed)}" if closed else "") + (f" · reopened {', '.join(reopened)}" if reopened else ""))
        store.append_changelog(version, [f"Live QA by Quinn in AWS: {line}"])
        await ctx.set_agent("qa", "needs_approval", line)
        await ctx.set_project(progress=round(6 / 7, 3))
        await ctx.emit("build.updated", f"Quinn (live): {line}", agent="qa")
        await crewchat.say(pid, "qa", "cto", f"Live QA done: {line}. {data['summary']}", "handoff", files=["reports/live_qa.md", signoff.TEST_SIGNOFF])
        if active:
            who = "; ".join(f"{t['label']} → {tk.name(t['assignee'])}" for t in active)
            await flow.request_approval(pid, "live_bugs", f"Quinn: {len(active)} ticket(s) open ({who})", data["summary"]
                                        + " Approving sends each ticket to its fixer; they fix, deploy and hand it back to Quinn.",
                                        ["reports/live_qa.md", signoff.TEST_SIGNOFF])
        else:
            code = aws_access.load(pid, "code") or {}
            aws_access.save(pid, {**code, "live_ok": code.get("deploys", 0), "live_ok_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, "code")
            await crewchat.say(pid, "qa", "user", f"✅ Test sign-off for {version}: {report['signoff'] or data['summary']}", "handoff",
                               files=[signoff.TEST_SIGNOFF])
            await flow.request_approval(pid, "live", f"Quinn's test sign-off: {ok}/{total} scenarios passed ({version})",
                                        (report["signoff"] or data["summary"]) + " Approving signs off the testing: the flow is live and tested.",
                                        [signoff.TEST_SIGNOFF, *([PLAN_MD] if cases else []), "reports/live_qa.md", "reports/code_deploy.md"])
    except Exception as exc:
        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("qa", "blocked" if blocked else "failed", str(exc)[:240])
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Quinn (live): {str(exc)[:200]}")
        await ctx.emit("build.updated", f"Quinn hit a problem: {str(exc)[:200]}", agent="qa")
        await crewchat.say(pid, "qa", "cto", f"I'm stuck on the live tests: {str(exc)[:300]}", "issue")
        raise


def live_markdown(rep: dict) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    ok = sum(c["passed"] for c in rep["checks"])
    lines = [f"# Live QA in AWS · {rep['version']}", "", rep["summary"], "",
             f"**{ok}/{len(rep['checks'])} checks passed** against the deployed flow, as `{rep['role']}` ({rep['calls']} real calls).", "",
             "| Check | Scenario | Result | Evidence |", "|---|---|---|---|"]
    lines += [f"| {c['id']} | {esc(c['title'])} | {'✅ pass' if c['passed'] else '❌ fail'} | {esc(c['evidence'])[:400]} |" for c in rep["checks"]]
    if any(c.get("did") for c in rep["checks"]):  # 10-05: each scenario in plain words, so anyone can follow what was tested
        lines += ["", "## Scenario by scenario", ""]
        for c in rep["checks"]:
            lines += [f"### {'✅' if c['passed'] else '❌'} {c['id']}: {c['title']}", ""]
            if c.get("did"):
                lines.append(f"- **What Quinn did:** {c['did']}")
            if c.get("expected"):
                lines.append(f"- **Expected:** {c['expected']}")
            lines += [f"- **What came back:** {c['evidence']}", ""]
    if rep.get("not_run"):
        lines += ["", "**Not run:**", *[f"- {x['id']}: {x['why']}" for x in rep["not_run"]]]
    if rep.get("risks"):
        lines += ["", "**Residual risks:**", *[f"- {r}" for r in rep["risks"]]]
    if rep.get("signoff"):
        lines += ["", f"> **Quinn's sign-off:** {rep['signoff']}"]
    if rep.get("left_for_user"):
        lines += ["", f"**Left for you to see:** {rep['left_for_user']} (SQS console → the queue → Send and receive messages → Poll for messages)."]
    lines += ["", "## Tickets", ""]
    for key, label in (("created", "Opened"), ("closed", "Closed after retest"), ("reopened", "Reopened (still failing)")):
        if rep.get(key):
            lines.append(f"- {label}: {', '.join(rep[key])}")
    lines += ["", "| Ticket | Severity | Status | With | Title |", "|---|---|---|---|---|"]
    lines += [f"| {t['label']} | {t['severity']} | {t['status']} | {t['assignee']} | {esc(t['title'])} |" for t in rep.get("open", [])] \
        or ["| – | – | – | – | No open tickets |"]
    return "\n".join(lines) + "\n"


def qa_markdown(rep: dict) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    r = rep["tests"]
    lines = [f"# QA report · {rep['version']}", "", rep["summary"], "",
             f"**{r['passed']}/{r['total']} tests passed** in the no-network sandbox (handler driven like API Gateway, moto as AWS).", "",
             "## Test plan and results", "", "| Id | Scenario | Type | Expected | Result |", "|---|---|---|---|---|"]
    for p in rep["plan"]:
        res = rep["results"].get(p["id"])
        mark = "✅ pass" if res and res["outcome"] == "passed" else "❌ fail" if res else "–"
        lines.append(f"| {p['id']} | {esc(p['title'])} | {p['type']} | {esc(p['expected'])} | {mark} |")
    lines += ["", "## Bugs", "", "| Bug | Severity | Status | Title |", "|---|---|---|---|"]
    lines += [f"| {b['id']} | {b['severity']} | {b['status']} | {esc(b['title'])} |" for b in rep["bugs"]] or ["| – | – | – | No bugs |"]
    return "\n".join(lines) + "\n"
