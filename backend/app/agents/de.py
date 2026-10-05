"""Dev — Data Engineer: the only agent that writes application code. Terra deploys it (user, 10-03).

de.code    from the requirement, Atlas's mapping (worked examples), Archie's LLD and Terra's live infrastructure writes
           src/ (Lambda code), layers/ and tests/ (pytest). Tests run in the no-network sandbox (moto for AWS). The
           submission is accepted only when the whole suite passes, coverage meets Archie's gate and every worked example
           is a test case. With tickets (from Quinn's live tests or the user), Dev works in a new minor version, fixes
           them and adds a regression test per ticket. Infrastructure live → Archie's code review (ta.code_review).
de.deploy  after the review: builds the layers and packs each function's code; whatever changed goes to Terra
           (hand_over → tp.deploy: a plan the user approves, then apply, which sends the job back). Then, with Dev's
           read-only role, a check that every function runs exactly his package, and a sanity check: one test message
           through the live flow, the response, the output and the logs. A failed sanity check goes straight back to
           Dev (once); then the "code" gate. Tickets he fixed are resolved back to Quinn with his comment. Older
           projects (no platform packages.tf): Dev uploads the code himself (push_code).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import replace
from urllib.parse import unquote, urlparse

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.buildkit import CHANGED_FILES, DELETE, WorkingSet, context, examples_fixture, files_under, new_minor_version, read
from app.agents.intake import load_intake
from app.orchestrator import crewchat, flow
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import aws_access
from app.services.storage import ProjectStore
from app.tools import pytest_runner, sandbox, terraform

OWN = ("src/", "layers/", "tests/")
DRAFT = "de_draft"  # deploy/de_draft.json: Dev's files of an attempt that hasn't submitted yet (resume after a stop)
FIXTURE = "tests/fixtures/examples.json"
# Dev writes a few files per call (like Terra): one call carrying the whole codebase took 5-10 minutes of silence on
# 10-02 (the tool input only arrives when it's complete), and a huge call can hit the output limit.
WRITE_FILES = Tool(
    name="write_files",
    description="Write or replace files under src/, layers/ or tests/ (full content), 2-4 files per call; files you wrote earlier "
                "are kept. Use `delete` to remove a file. When the code and its tests are written, call run_tests.",
    schema=obj({"files": CHANGED_FILES, "delete": DELETE}, required=["files"]),  # delete is optional
)
RUN_TESTS = Tool(
    name="run_tests",
    description="Run pytest on your files in the sandbox (Python 3.14, lxml, boto3, moto; no network). Files you wrote with "
                "write_files are already there: send only last changes here (or none).",
    schema=obj({"files": CHANGED_FILES, "delete": DELETE}, required=["files"]),  # delete is optional
)
SUBMIT = Tool(
    name="submit_code",
    terminal=True,
    description="Submit the code (src/, layers/, tests/): your files as they stand, plus any last changes in `files`. The "
                "platform reruns all tests; fix and resubmit if rejected.",
    schema=obj({
        "summary": {"type": "string", "description": "2-3 sentences: what the code does"},
        "files": CHANGED_FILES, "delete": DELETE,
        "notes": {"type": "array", "items": {"type": "string"}, "description": "decisions, allowed fixes applied to user files, limits"},
        "changes": {"type": "string", "description": "What changed versus the previous version (fixes, feedback), else empty"},
    }),
)


def open_bugs(project_id: str) -> list[dict]:
    """Quinn's open bugs from the (older) component tests, reports/qa.json. Live problems are tickets now."""
    from app.agents.qa import load_qa

    return [b for b in ((load_qa(project_id) or {}).get("bugs") or []) if b.get("status") == "open"]


async def dev_tickets(project_id: str, ids: list[str] | None, version: str, redo: bool) -> list[dict]:
    """The tickets Dev works on: the ones handed to him (or every active one assigned to him), plus, when the user
    sends his fix back for changes, the ones he resolved in this version."""
    from app.services import tickets as tk

    rows = await tk.listing(project_id)
    if ids:
        return [t for t in rows if t["id"] in ids]
    mine = [t for t in rows if t["assignee"] == "de" and t["status"] in tk.ACTIVE]
    if redo:
        mine += [t for t in rows if t["status"] == "resolved" and t["area"] == "code" and t.get("version_fixed") == version]
    return mine


async def live_error_logs(project_id: str) -> str:
    """The deployed functions' recent errors, read with Dev's own role ("" when nothing is deployed)."""
    acc, dep = aws_access.load(project_id), aws_access.load(project_id, "deploy")
    if not acc or acc.get("status") != "active" or not dep or dep.get("status") != "deployed":
        return ""
    creds = await asyncio.to_thread(aws_access.assume, project_id, "de")

    def go() -> str:
        logs = aws_access.session_for(creds).client("logs")
        groups = logs.describe_log_groups(logGroupNamePrefix=f"/aws/lambda/{acc['prefix']}")["logGroups"][:6]
        parts = []
        for g in groups:
            ev = logs.filter_log_events(logGroupName=g["logGroupName"], startTime=int((time.time() - 6 * 3600) * 1000), limit=60,
                                        filterPattern="?ERROR ?Error ?error ?Exception ?Traceback").get("events", [])
            aws_access.audit(project_id, "de", "logs:FilterLogEvents", g["logGroupName"], True, f"{len(ev)} error event(s)")
            if ev:
                parts.append(f"## {g['logGroupName']}\n" + "\n".join(e["message"][:800].rstrip() for e in ev[-25:]))
        return "\n\n".join(parts)
    return await asyncio.to_thread(go)


def log_tests(store: ProjectStore, agent: str, run: int | str, r: dict) -> None:
    """One test run, structured, for the workbench (pass/fail per test, coverage per file)."""
    cov = r.get("coverage") or {}
    store.append_log("work", {"agent": agent, "phase": "tests", "run": run, "total": r.get("total", 0), "passed": r.get("passed", 0),
                              "failed": r.get("failed", 0), "error": r.get("error", 0), "coverage": cov.get("percent"),
                              "files": {p: {"percent": f.get("percent"), "missing": (f.get("missing") or [])[:40]} for p, f in (cov.get("files") or {}).items()},
                              "tests": [{"id": t["id"], "outcome": t["outcome"], "message": (t.get("message") or "")[:600]} for t in (r.get("tests") or [])[:300]],
                              "output": (r.get("output") or "")[-3000:] if not r.get("tests") else ""})


def summarise(r: dict, limit: int = 12) -> str:
    bad = [t for t in r["tests"] if t["outcome"] in ("failed", "error")]
    head = f"{r['passed']}/{r['total']} passed, {r['failed']} failed, {r['error']} errors."
    if not r["tests"]:
        return head + " No tests ran. pytest output:\n" + r["output"][-3000:]
    return head + "".join(f"\n- {t['id']}: {t['message'][:700]}" for t in bad[:limit])


def infra_live(project_id: str) -> bool:
    return (aws_access.load(project_id, "deploy") or {}).get("status") == "deployed"


def code_deploy_map(project_id: str) -> dict:
    return ((aws_access.load(project_id, "deploy") or {}).get("outputs") or {}).get("code_deploy") or {}


@handler("de.code", resumable=False)
async def code(ctx: JobContext) -> None:
    from app.services import tickets as tk

    pid = ctx.project_id
    feedback = (ctx.payload.get("feedback") or "").strip()
    store = ProjectStore(pid)
    tix = await dev_tickets(pid, ctx.payload.get("tickets"), store.manifest()["current_version"], bool(feedback))
    legacy = open_bugs(pid)
    if legacy or any(t["status"] in ("open", "reopened") for t in tix):  # fixes go into a new minor version (not again on a retry)
        await new_minor_version(pid, "Fixing " + ", ".join([b["id"] for b in legacy] + [t["label"] for t in tix]))
    for t in tix:
        if t["status"] in ("open", "reopened"):
            await tk.change(t["id"], "de", "On it.", status="in_progress")
    bugs = legacy + [{"id": t["label"], "title": t["title"], "severity": t["severity"], "steps": t["steps"] or t["description"],
                      "expected": t["expected"], "actual": t["actual"], "live": True, "ticket": t["id"]} for t in tix]
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    intake = await load_intake(pid)
    fixture = examples_fixture(pid)
    examples = [e["name"] for e in json.loads(fixture)]
    previous = files_under(store, OWN)
    from app.agents.ta import quality_gates

    gates = quality_gates(pid)
    min_cov = float(gates["min_coverage_percent"])
    box: dict = {"runs": 0}
    # the code the model is shown is its starting set; otherwise it starts empty
    work = WorkingSet({p: c for p, c in previous.items() if p != FIXTURE} if previous and (bugs or feedback) else {}, OWN, "Dev")
    # An attempt that stopped before submitting (budget pause, restart, failure) left its files as a draft: carry on
    # from them instead of rewriting everything (user, 10-02: a budget stop threw away 48/50 passing tests).
    draft = aws_access.load(pid, DRAFT) or {}
    resumed = draft.get("version") == version and bool(draft.get("files"))
    if resumed:
        work.files.update({p: c for p, c in draft["files"].items() if p.startswith(OWN) and p != FIXTURE})

    def keep_draft(last_tests: str | None = None) -> None:
        aws_access.save(pid, {"version": version, "files": work.files, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                              "last_tests": last_tests or draft.get("last_tests") if resumed else last_tests}, DRAFT)

    async def write_files(a: dict):
        before = set(work.files)
        files = work.apply(a)
        keep_draft()
        written = [f["path"] for f in a.get("files") or []]
        await ctx.set_agent("de", "working", f"✍️ Writing {', '.join(p.split('/')[-1] for p in written[:4])}")
        return (f"Saved {len(written)} file(s); removed {len(before - set(files))}. Your files now:\n" + work.listing()
                + "\nWrite the rest (2-4 per call), then call run_tests.")

    async def run_tests(a: dict):
        files = {**work.apply(a), FIXTURE: fixture}
        box["runs"] += 1
        await ctx.set_agent("de", "working", f"🧪 Running pytest in the sandbox (run {box['runs']}, {sum(p.startswith('tests/') for p in files)} test files)")
        r = await pytest_runner.run(files, ["tests"], coverage=True)
        log_tests(store, "de", box["runs"], r)
        box["red"] = box.get("red", 0) + 1 if not r.get("ok") else 0
        if box["red"] == 3:  # three red runs in a row: Archie looks over his shoulder (agents/watch.py), Dev carries on
            from app.agents import watch

            await watch.request_advice(pid, "de", "3 test runs in a row still fail", summarise(r))
        cov = (r.get("coverage") or {}).get("percent")
        keep_draft(f"{r['passed']}/{r['total']} passed" + (f", coverage {cov}%" if cov is not None else ""))
        await crewchat.say(pid, "de", "crew", (f"⚠️ Test run {box['runs']}: no tests ran" if not r["total"] else
                           f"🧪 Test run {box['runs']}: {r['passed']}/{r['total']} passed"
                           + (f", {r['failed'] + r['error']} failing" if r["failed"] + r["error"] else " ✅")
                           + (f" · coverage {cov}% (gate {min_cov:.0f}%)" if cov is not None else "")), "work")
        return summarise(r) + (f"\nLine coverage: {cov}% (gate {min_cov:.0f}%)." + "".join(
            f"\n- {p}: {f['percent']}%, untested lines {f['missing'][:25]}" for p, f in (r.get("coverage") or {}).get("files", {}).items()
            if f["missing"]) if cov is not None else "")

    cd = code_deploy_map(pid)
    from app.services import inventory

    handlers = {r["name"]: next((i["value"] for i in r["set"] + r["defaults"] if i["key"] == "handler"), None)
                for r in (inventory.load(pid) or {}).get("resources", []) if r["type"] == "aws_lambda_function"}

    async def verify(a: dict):
        files = {**work.apply(a), FIXTURE: fixture}
        missing_dirs = [f"{f['source_dir'].strip('/')}/" for f in (cd.get("functions") or {}).values()
                        if isinstance(f, dict) and f.get("source_dir") and not any(p.startswith(f["source_dir"].strip("/") + "/") for p in files)]
        if missing_dirs:
            raise AgentError(f"Terra's functions expect your code in {missing_dirs} (output code_deploy): put each function's code there.")
        no_dockerfile = [f"{str(i.get('source_dir', '')).strip('/')}/Dockerfile" for i in (cd.get("images") or {}).values()
                         if isinstance(i, dict) and f"{str(i.get('source_dir', '')).strip('/')}/Dockerfile" not in files]
        if no_dockerfile:
            raise AgentError(f"Terra deploys container images from {no_dockerfile} (output code_deploy.images): write each Dockerfile, "
                             "copying only the runtime code (no tests), with the base image the LLD names.")
        missing_layers = [k for k in (cd.get("layers") or {}) if not any(p.startswith(f"layers/{k}/") for p in files)]
        if missing_layers:
            raise AgentError(f"Terra's functions use the layers {missing_layers}: provide layers/<key>/requirements.txt (package layer) "
                             "or layers/<key>/python/... (code layer) for each.")
        wrong = []
        for f in (cd.get("functions") or {}).values():
            handler_ = handlers.get(f.get("function_name"))
            if handler_ and "." in handler_:
                module = f"{str(f.get('source_dir', '')).strip('/')}/{handler_.rsplit('.', 1)[0].replace('.', '/')}.py"
                if module not in files:
                    wrong.append(f"{f['function_name']} runs handler {handler_}, so {module} must exist")
        if wrong:
            raise AgentError("Match the live functions' handler settings: " + "; ".join(wrong))
        await ctx.set_agent("de", "working", f"✅ Final test run with coverage (gate {min_cov:.0f}%)")
        r = await pytest_runner.run(files, ["tests"], coverage=True)
        log_tests(store, "de", "final", r)
        if not r["ok"]:
            raise AgentError("The test suite isn't green: " + summarise(r))
        cov = r.get("coverage")
        if cov is None:
            raise AgentError("Coverage couldn't be measured (pytest-cov). Output: " + r["output"][-1200:])
        if cov["percent"] < min_cov:
            gaps = "; ".join(f"{p} {f['percent']}% (untested lines {', '.join(map(str, f['missing'][:25]))})"
                             for p, f in sorted(cov["files"].items(), key=lambda kv: kv[1]["percent"]) if f["missing"])
            raise AgentError(f"Line coverage is {cov['percent']}%, below Archie's quality gate of {min_cov:.0f}%. "
                             f"Add tests for the untested lines (don't delete code to game it): {gaps}")
        ids = " ".join(t["id"] for t in r["tests"]).lower()
        missing = [e for e in examples if e.lower() not in ids]
        if missing:
            raise AgentError(f"Every worked example must be a test case (parametrize over {FIXTURE} with ids=name). Missing: {missing}")
        unfixed = [b["id"] for b in bugs if b["id"].lower().replace("-", "_") not in ids]
        if unfixed:
            raise AgentError(f"Add a regression test per ticket or bug, with its id in the name (e.g. test_tkt_001_...): missing {unfixed}")
        box["files"], box["result"] = files, r

    ask = (f"# Project id: {pid}\n" + context(store, intake.requirement_md, infra=True, own="de")
           + f"\n\n# Atlas's worked examples ({FIXTURE}, provided by the platform)\n```json\n{fixture[:30000]}\n```"
           + f"\n\n# Archie's quality gates (enforced on submit)\n- Line coverage of src/ and layers/ ≥ {min_cov:.0f}%"
           + "".join(f"\n- {rule}" for rule in gates.get("rules", [])))
    if cd:
        ask += ("\n\n# Where your code goes (Terra's output code_deploy, live in AWS)\n```json\n" + json.dumps(cd, indent=2)[:6000] + "\n```")
    if legacy:
        ask += "\n\n# Open bugs from Quinn's component tests: fix every one, add a regression test per bug\n" + "\n".join(
            f"- **{b['id']}** ({b['severity']}) {b['title']}\n  Steps: {b['steps']}\n  Expected: {b['expected']}\n  Actual: {b['actual']}" for b in legacy)
    if tix:
        ask += "\n\n# Tickets assigned to you: fix every one, add a regression test per ticket (its id in the test name)\n" + tk.prompt_block(tix)
    if tix:
        try:
            logs = await live_error_logs(pid)
            await crewchat.say(pid, "de", "crew", f"🔎 Read the live error logs as `{aws_access.role_name(pid, 'de')}`: "
                               + (f"{logs.count(chr(10)) + 1} line(s)" if logs else "no errors logged"), "work")
            if logs:
                ask += f"\n\n# Live error logs from AWS (last 6 hours, read with your role)\n```\n{logs[:12000]}\n```"
        except Exception as exc:  # the logs help, but never block a fix
            await crewchat.say(pid, "de", "cto", f"Couldn't read the live logs ({str(exc)[:160]}); fixing from the tickets.", "issue")
    if resumed:
        ask += ("\n\n# Your files from your previous attempt (it stopped before you submitted"
                + (f"; its last test run: {draft['last_tests']}" if draft.get("last_tests") else "") + "). They're already in your files: "
                "don't rewrite them. Run the tests, fix only what fails or what the gates need, then submit.\n"
                + "\n\n".join(f"## {p}\n```\n{c[:20000]}\n```" for p, c in sorted(work.files.items())))
    elif previous and (bugs or feedback):
        ask += ("\n\n# Your current code (already in your files: send only what you change)\n"
                + "\n\n".join(f"## {p}\n```\n{c[:20000]}\n```" for p, c in previous.items() if p != FIXTURE))
    if feedback:
        ask += f"\n\n# Feedback to address (describe it in `changes`)\n{feedback}"
        from app.agents.codereview import discussion

        talk = discussion(pid)
        if talk:  # the user's conversation with Archie about this code: what they agreed is part of the request
            ask += f"\n\n# The user's discussion with Archie about this code (implement what they agreed on)\n{talk[:20000]}"
    ask += "\n\nWrite the code and tests, run them, then call submit_code." if not resumed else "\n\nRun the tests now (run_tests), fix, then submit_code."

    labels = ", ".join(b["id"] for b in bugs)
    await ctx.set_agent("de", "working", (f"🎫 Fixing {labels} in {version}" if bugs else
                                          "Revising the code" if feedback else f"💻 Reading the LLD, mapping and Terra's infra ({version})"))
    await ctx.set_project(status="running", last_activity=f"Dev is fixing {labels}" if bugs else "Dev is writing the code")
    await crewchat.say(pid, "de", "qa" if bugs else "ta",
                       (f"Picking up where I stopped: {len(work.files)} files from my previous attempt"
                        + (f" ({draft['last_tests']})" if draft.get("last_tests") else "") + ". Finishing them, not starting over.") if resumed else
                       (f"Quinn, on it: fixing {labels} in {version}, with a regression test for each." if bugs else
                        f"Thanks Archie, on it. Writing the code for {version} into Terra's functions; every one of Atlas's "
                        f"{len(examples)} examples becomes a test case (moto, no real AWS) before I deploy."), "ack")
    try:
        if not sandbox.available():
            raise AgentError("The code sandbox (Docker) isn't available on this host")
        res = await run_loop(project_id=pid, agent="de",
                             system=[{"type": "text", "text": llm.prompt("de.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}],
                             tools=[replace(WRITE_FILES, handler=write_files), replace(RUN_TESTS, handler=run_tests), replace(SUBMIT, handler=verify)],
                             max_turns=16, purpose="code-resume" if resumed else "code-fix" if bugs else "code-revise" if feedback else "code",
                             log_first=[{"phase": "call", "tool": "write_files", "input": {"files": [{"path": p, "content": c} for p, c in sorted(work.files.items())]},
                                         "resumed": True}] if resumed else None)
        data = res.terminal.get("submit_code")
        if not data or "files" not in box:
            raise AgentError("Dev finished without submitting passing code.")
        r = box["result"]
        stale = store.replace_files(OWN, box["files"])
        aws_access.drop(pid, DRAFT)  # submitted: nothing to resume
        store.write("reports/pytest.md", pytest_runner.report_md(f"Unit tests · {version}", r))
        store.write("reports/coverage.html", pytest_runner.coverage_html(f"Coverage report · {version}", box["files"], r, min_cov))
        store.write("reports/pytest.json", json.dumps(r, indent=2, ensure_ascii=False))
        store.write("reports/code.json", json.dumps({"summary": data["summary"], "notes": data["notes"], "changes": data["changes"],
                                                     "files": sorted(box["files"]), "version": version, "test_runs": box["runs"],
                                                     "coverage": r["coverage"]["percent"], "coverage_gate": min_cov,
                                                     "fixed_bugs": [b["id"] for b in bugs]}, indent=2, ensure_ascii=False))
        store.append_changelog(version, [f"Code {'fixed' if bugs else 'revised' if previous else 'written'} by Dev: {len(box['files'])} files, "
                                         f"{r['passed']}/{r['total']} tests passing" + (f"; fixed {labels}" if bugs else "")
                                         + (f"; removed {', '.join(stale)}" if stale else "")])
        line = (f"{sum(p.startswith(('src/', 'layers/')) for p in box['files'])} code files · {r['passed']}/{r['total']} tests passing"
                f" · coverage {r['coverage']['percent']}%")
        await ctx.emit("build.updated", f"Dev {'fixed ' + labels if bugs else 'wrote the code'}: {line}", agent="de")
        await crewchat.say(pid, "de", "cto", f"Code ready for {version}: {line}. {data['summary']}", "handoff",
                           files=["reports/pytest.md", *[p for p in sorted(box["files"]) if p.startswith("src/")][:4]])
        if infra_live(pid):
            # like the team: the code goes to Archie (TA) for review, then to the user, and only then into AWS (user, 10-02)
            await ctx.set_agent("de", "done", f"Code ready: {line}. With Archie for review")
            await crewchat.say(pid, "de", "ta", f"Archie, {version} is ready for your review: {line}. "
                               + (f"What changed: {data['changes']}. " if data.get("changes") else "") + "Coverage report and test results are in reports/.",
                               "handoff", files=["reports/coverage.html", "reports/pytest.md", *[p for p in sorted(box["files"]) if p.startswith("src/")][:4]])
            await runner_mod.runner.enqueue("ta.code_review", project_id=pid, tickets=[t["id"] for t in tix], attempt=int(ctx.payload.get("attempt") or 0),
                                            changes=data.get("changes") or "", user_feedback=feedback if not ctx.payload.get("tickets") else "")
            return
        await ctx.set_agent("de", "needs_approval", f"Code ready: {line}" + (f" · fixed {labels}" if bugs else ""))
        await ctx.set_project(progress=round(5 / 7, 3))
        title = f"Dev's fixes for {labels} ({version})" if bugs else f"Dev's code and unit tests ({version})"
        await flow.request_approval(pid, "code", title, data["summary"], ["reports/pytest.md", *sorted(p for p in box["files"] if p.startswith("src/"))])
    except Exception as exc:
        from app.agents import unblock

        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("de", "blocked" if blocked else "failed", str(exc)[:240])
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Dev: {str(exc)[:200]}")
        await ctx.emit("build.updated", f"Dev hit a problem: {str(exc)[:200]}", agent="de")
        await crewchat.say(pid, "de", "cto", f"I'm stuck: {str(exc)[:300]}", "issue")
        if not blocked:
            await unblock.escalate(ctx, "de", "de.code", "Writing code that passes the gates failed", exc)
        raise


# ── deploying the code into Terra's functions (Dev's own role) ──────────────────────────────────────────────────────
CODE_JSON = "code"  # deploy/code.json: what's deployed where (not versioned: it spans versions)
SUBMIT_SANITY = Tool(
    name="submit_sanity",
    terminal=True,
    description="Submit your sanity check of the live deploy.",
    schema=obj({"passed": {"type": "boolean"}, "summary": {"type": "string", "description": "one line"},
                "failure_area": {"type": "string", "enum": ["none", "code", "infra"],
                                 "description": "when it failed: 'code' if your code is wrong, 'infra' if the infrastructure is (a permission, "
                                                "logs not reaching CloudWatch, a trigger, a timeout, a missing resource); 'none' when it passed"},
                "steps": {"type": "array", "description": "one per hop you proved, in flow order", "items": obj({
                    "what": {"type": "string", "description": "the hop, e.g. 'API Gateway → Lambda'"},
                    "how": {"type": "string", "description": "exactly how you tested it: the call and its input (e.g. POST <url> with "
                            "Content-Type: application/xml and the example body; sqs_receive on <queue>; read_logs on <group>)"},
                    "observed": {"type": "string"}, "ok": {"type": "boolean"}})},
                "try_it": {"type": "array", "description": "A walkthrough for the user to repeat your test in the AWS console, one item "
                           "per hop in flow order", "items": obj({
                               "title": {"type": "string", "description": "e.g. '1. Call the API (API Gateway → Lambda)'"},
                               "link": {"type": "string", "description": "the console page from the brief (or the API URL), exactly as given"},
                               "steps": {"type": "string", "description": "what to click and type there, in plain words"},
                               "input": {"type": "string", "description": "what to paste (headers, body, event JSON, or a curl command), "
                                         "or empty"},
                               "expect": {"type": "string", "description": "what the user should see when it works"}})},
                "sent": {"type": "string", "description": "how you started the flow and with what: the HTTP call, the queue message, "
                         "the event, the uploaded object, or the direct invocation"},
                "received": {"type": "string", "description": "what arrived where the result lands (the queue message, the S3 object's "
                             "key and content, the DynamoDB item, or the HTTP response), and where; empty if nothing arrived"},
                "test_event": {"type": "string", "description": "the JSON event you invoked the function with, ready to paste into "
                               "the Lambda console's Test tab; empty if the flow has no function"},
                "left_for_user": {"type": "string", "description": "which sample message is waiting in which queue for the user, or "
                                  "why none was left"}}),
)


def _sha(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


PACKAGES = "packages"  # deploy/packages.json: what Dev handed to Terra {"code": {key: {fingerprint, kb, applied, …}}, "layers": {…}}


class NotDevsCode(AgentError):
    """A function in AWS doesn't run the package Dev handed over: Terra's wiring (or a console edit), not Dev's code."""


def load_packages(pid: str) -> dict:
    rec = aws_access.load(pid, PACKAGES)
    if rec is None:  # 10-02: only layers were handed over, in a flat record
        rec = {"code": {}, "layers": aws_access.load(pid, "layer_packages") or {}}
    return {"code": rec.get("code") or {}, "layers": rec.get("layers") or {}, "images": rec.get("images") or {}}


def folder_fingerprint(folder) -> str:
    """Everything that goes into an image build (tests and caches aside): same folder → same fingerprint → no rebuild."""
    h = hashlib.sha256()
    for f in sorted(p for p in folder.rglob("*") if p.is_file() and not {"__pycache__", ".pytest_cache"} & set(p.parts)):
        h.update(f.relative_to(folder).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def _docker(args: list[str], cwd=None, env=None, stdin: str | None = None, timeout: int = 1500) -> tuple[int, str]:
    import subprocess

    try:
        p = subprocess.run(["docker", *args], cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"docker {args[0]} timed out after {timeout}s"
    return p.returncode, (p.stdout + "\n" + p.stderr)[-6000:]


def push_images(pid: str, creds: dict, work, cd: dict, version: str) -> list[dict]:
    """Dev's container images (Lambda images, ECS tasks, EC2): each code_deploy `images` entry is built from
    <source_dir>/Dockerfile and pushed to the project's ECR repository with Dev's own role, only when the folder changed.
    The digest (repository@sha256:…) is what Terra deploys (var.image_packages). Blocking. Returns [{key, uri, built}]."""
    import os
    import re as _re
    import shutil
    import tempfile

    images = cd.get("images") or {}
    if not images:
        return []
    root = aws_access.deploy_dir(pid) / "work" / terraform.PACKAGES_DIR
    root.mkdir(parents=True, exist_ok=True)
    rec = load_packages(pid)
    index = terraform.handed_over(root)["images"]
    ecr = aws_access.session_for(creds).client("ecr")
    cfg = tempfile.mkdtemp(prefix="docker-cfg-")  # Dev's ECR login lives only for this build
    env = {**os.environ, "DOCKER_CONFIG": cfg}
    logged, out = set(), []
    try:
        for key, img in images.items():
            src = str(img.get("source_dir") or "").strip("/")
            folder = work / src
            if not src or not (folder / "Dockerfile").exists():
                raise AgentError(f"Image {key}: Terra expects {src or '<source_dir>'}/Dockerfile in Dev's code")
            fp = folder_fingerprint(folder)
            cur = rec["images"].get(key) or {}
            if cur.get("fingerprint") == fp and index.get(key):
                out.append({"key": key, "uri": index[key], "built": False})
                continue
            repo = str(img.get("repository_url") or "")
            if not repo:
                raise AgentError(f"Image {key}: Terra's code_deploy output has no repository_url for it")
            registry = repo.split("/")[0]
            if registry not in logged:
                auth = ecr.get_authorization_token()["authorizationData"][0]
                user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
                code, log = _docker(["login", "--username", user, "--password-stdin", registry], env=env, stdin=password)
                if code:
                    raise AgentError(f"docker login to ECR failed: {log[-400:]}")
                logged.add(registry)
            tag = f"{repo}:{version.replace('.', '-')}-{fp[:12]}"
            args = ["build", "--platform", str(img.get("platform") or "linux/amd64"), "--provenance=false", "-t", tag, "."]
            code, log = _docker(args, cwd=folder, env=env)
            if code and "provenance" in log:  # an older Docker without buildx: plain build (Lambda accepts its manifest)
                code, log = _docker([a for a in args if a != "--provenance=false"], cwd=folder, env=env)
            if code:
                raise AgentError(f"Image {key}: docker build failed:\n{log[-2500:]}")
            code, log = _docker(["push", tag], env=env)
            if code:
                raise AgentError(f"Image {key}: docker push to ECR failed: {log[-800:]}")
            m = _re.search(r"digest: (sha256:[0-9a-f]{64})", log)
            if not m:
                raise AgentError(f"Image {key}: pushed, but the digest isn't in docker's output")
            uri = f"{repo}@{m.group(1)}"
            index[key] = uri
            rec["images"][key] = {**{k: cur.get(k) for k in ("applied", "applied_version", "applied_at")}, "fingerprint": fp, "uri": uri,
                                  "version": version, "handed_at": _now(), "source_dir": src, "function_name": img.get("function_name"),
                                  "kb": 0, "tag": tag}
            aws_access.audit(pid, "de", "ecr:PutImage", repo, True, f"{tag} → {m.group(1)[:19]}…")
            out.append({"key": key, "uri": uri, "built": True})
            _docker(["rmi", tag], env=env, timeout=120)  # keep the host's disk tidy (the image lives in ECR)
    finally:
        shutil.rmtree(cfg, ignore_errors=True)
    (root / terraform.IMAGES_FILE).write_text(json.dumps(index, indent=2), encoding="utf-8")
    aws_access.save(pid, rec, PACKAGES)
    return out


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def hand_over(pid: str, work, built: list[dict], cd: dict, version: str, code: bool = True) -> dict:
    """Dev's half of a deploy (user, 10-03: "the dev code and layers all need to be given to Terra by Dev, then Terra
    deploys them"): every package that's new or changed goes into Terra's workspace for the platform's packages.tf:
    each function's code from its src/ folder (only runtime code, reproducible) and each layer Dev built. Returns
    {"code": [...], "layers": [...]}: what Terra must still deploy (empty: everything is live already)."""
    import shutil

    root = aws_access.deploy_dir(pid) / "work" / terraform.PACKAGES_DIR
    for kind in ("code", "layers"):
        (root / kind).mkdir(parents=True, exist_ok=True)
    for flat in root.glob("*.zip"):  # the 10-02 layout kept layers at the top
        flat.replace(root / "layers" / flat.name)
    rec = load_packages(pid)
    pending: dict[str, list] = {"code": [], "layers": [], "images": []}

    def put(kind: str, key: str, fingerprint: str, kb: int, write, **extra) -> None:
        cur = rec[kind].get(key) or {}
        target = root / kind / f"{key}.zip"
        if cur.get("applied") == fingerprint and target.exists():
            return
        if cur.get("fingerprint") != fingerprint or not target.exists():
            write(target)
            cur = {**{k: cur.get(k) for k in ("applied", "applied_version", "applied_at")}, "fingerprint": fingerprint, "kb": kb,
                   "handed_at": _now(), "version": version, **extra}
            rec[kind][key] = cur
        pending[kind].append({"key": key, "kb": kb, "version": version, "from": cur.get("applied_version") or "placeholder", **extra})

    if code:
        for fkey, f in (cd.get("functions") or {}).items():
            src = str(f.get("source_dir") or "").strip("/")
            folder = work / src
            if not src or not folder.is_dir():
                raise AgentError(f"{f.get('function_name')}: Terra expects the code in {src}/, which isn't in Dev's code")
            data = terraform.zip_folder(folder)
            if len(data) > 50 * 1024 * 1024:
                raise AgentError(f"{f.get('function_name')}: the code zip is over the 50 MB direct-upload limit")
            put("code", fkey, _sha(data), max(1, len(data) // 1024), lambda t, d=data: t.write_bytes(d),
                function_name=f.get("function_name"), source_dir=src)
    names = cd.get("layers") or {}
    for b in built:
        put("layers", b["layer"], b["key"], b["kb"], lambda t, z=work / b["zip"]: shutil.copyfile(z, t), name=names.get(b["layer"], b["layer"]))
    for key, img in (cd.get("images") or {}).items():  # built and pushed by push_images: here only "is it live yet?"
        cur = rec["images"].get(key) or {}
        if cur.get("fingerprint") and cur.get("applied") != cur.get("fingerprint"):
            pending["images"].append({"key": key, "kb": 0, "version": cur.get("version") or version, "from": cur.get("applied_version") or "placeholder",
                                      "uri": cur.get("uri"), "source_dir": cur.get("source_dir"),
                                      "function_name": img.get("function_name") or img.get("ecs_service") or key})
    aws_access.save(pid, rec, PACKAGES)
    return pending


def check_fit(creds: dict, cd: dict, work) -> None:
    """Before handing over: every function's folder is in Dev's code and holds the module its live handler names
    (read-only). Blocking."""
    lam = aws_access.session_for(creds).client("lambda")
    for f in (cd.get("functions") or {}).values():
        fname, src = f["function_name"], str(f.get("source_dir") or "").strip("/")
        if not src or not (work / src).is_dir():
            raise AgentError(f"{fname}: Terra expects the code in {src}/, which isn't in Dev's code")
        handler_name = lam.get_function_configuration(FunctionName=fname)["Handler"]
        module = handler_name.rsplit(".", 1)[0].replace(".", "/") + ".py"
        if not (work / src / module).exists():
            raise AgentError(f"{fname}: its handler is {handler_name}, so {src}/{module} must exist in Dev's code")


def record_live(pid: str, creds: dict, cd: dict, version: str) -> dict:
    """After Terra's apply: what's live, read with Dev's role (read-only), and proof that every function runs exactly
    the package Dev handed over (CodeSha256 = the package's hash). Same shape as push_code. Blocking."""
    lam = aws_access.session_for(creds).client("lambda")
    prev = aws_access.load(pid, CODE_JSON) or {}
    rec = load_packages(pid)
    functions: dict = {}
    layers_rec: dict = {}
    changed, unchanged, wrong = [], [], []
    for fkey, f in (cd.get("functions") or {}).items():
        fname = f["function_name"]
        lam.get_waiter("function_updated").wait(FunctionName=fname)
        cfg = lam.get_function_configuration(FunctionName=fname)
        pk = rec["code"].get(fkey) or {}
        if cfg["CodeSha256"] != pk.get("fingerprint"):
            wrong.append(f"{fname} runs code {cfg['CodeSha256'][:12]}…, not Dev's package {str(pk.get('fingerprint'))[:12]}… ({pk.get('version') or 'never handed over'})")
            continue
        arns = [x["Arn"] for x in cfg.get("Layers") or []]
        for arn in arns:
            lname, ver = arn.split(":")[-2], int(arn.split(":")[-1])
            key = next((k for k, n in (cd.get("layers") or {}).items() if n == lname), None)
            lp = rec["layers"].get(key) or {}
            layers_rec[lname] = {"key": lp.get("fingerprint", ""), "folder": key, "arn": arn, "version": ver, "kb": lp.get("kb", 0),
                                 "code_version": lp.get("applied_version") or lp.get("version"), "by": "terra"}
            if arn not in ((prev.get("functions") or {}).get(fname) or {}).get("layers", []):
                changed.append(f"📦 layer **{lname}** v{ver} on {fname} (Terra)")
        old = (prev.get("functions") or {}).get(fname) or {}
        functions[fname] = {"key": fkey, "source_dir": pk.get("source_dir") or str(f.get("source_dir") or "").strip("/"),
                            "sha": cfg["CodeSha256"], "kb": pk.get("kb", 0), "layers": arns, "handler": cfg["Handler"],
                            "runtime": cfg.get("Runtime"), "code_version": pk.get("applied_version") or pk.get("version") or version,
                            "deployed_at": pk.get("applied_at") or old.get("deployed_at"), "by": "terra"}
        if old.get("sha") != cfg["CodeSha256"]:
            changed.append(f"⚡ **{fname}**: Dev's code {functions[fname]['code_version']} ({pk.get('kb', 0)} KB), deployed by Terra")
        else:
            unchanged.append(fname)
    images: dict = {}
    for key, img in (cd.get("images") or {}).items():  # container images: the function / ECS service must run Dev's exact digest
        pk = rec["images"].get(key) or {}
        uri, where = pk.get("uri"), ""
        if not uri:
            continue
        if img.get("function_name"):
            where = img["function_name"]
            live = lam.get_function(FunctionName=where)["Code"].get("ImageUri", "")
            if live != uri:
                wrong.append(f"{where} runs image {live.rsplit('@', 1)[-1][:19]}…, not Dev's {uri.rsplit('@', 1)[-1][:19]}…")
        elif img.get("ecs_cluster") and img.get("ecs_service"):
            ecs = aws_access.session_for(creds).client("ecs")
            where = f"{img['ecs_cluster']}/{img['ecs_service']}"
            svc = ecs.describe_services(cluster=img["ecs_cluster"], services=[img["ecs_service"]]).get("services") or []
            td = svc[0]["taskDefinition"] if svc else None
            running = [c["image"] for c in ecs.describe_task_definition(taskDefinition=td)["taskDefinition"]["containerDefinitions"]] if td else []
            if uri not in running:
                wrong.append(f"ECS service {where} doesn't run Dev's image {uri.rsplit('@', 1)[-1][:19]}…")
        images[key] = {"uri": uri, "where": where, "source_dir": pk.get("source_dir"), "code_version": pk.get("applied_version") or pk.get("version"),
                       "deployed_at": pk.get("applied_at"), "by": "terra"}
        if ((prev.get("images") or {}).get(key) or {}).get("uri") != uri:
            changed.append(f"🐳 **{key}**: Dev's image {images[key]['code_version']}" + (f" in {where}" if where else "") + ", deployed by Terra")
        else:
            unchanged.append(key)
    if wrong:
        raise NotDevsCode("Not Dev's code in AWS: " + "; ".join(wrong))
    return {"functions": functions, "layers": layers_rec, "images": images, "changed": changed, "unchanged": unchanged}


def push_code(pid: str, creds: dict, cd: dict, work, layers_built: list[dict], version: str, managed: bool = False) -> dict:
    """Upload changed function code (and, for older projects where Dev still owns the layers, publish and attach them).
    Blocking (run in a thread). Returns {functions: {name: {...}}, layers: {name: {...}}, changed: [lines], unchanged: [names]}.
    `managed`: Terra's Terraform publishes and attaches the layers; Dev only uploads code."""
    lam = aws_access.session_for(creds).client("lambda")
    prev = aws_access.load(pid, CODE_JSON) or {}
    layers_rec: dict = dict(prev.get("layers") or {}) if not managed else {}
    arns: dict[str, str] = {}
    changed, unchanged = [], []
    for key, lname in ({} if managed else cd.get("layers") or {}).items():
        b = next((x for x in layers_built if x["layer"] == key), None)
        if not b:
            raise AgentError(f"Layer {key}: there's no layers/{key}/ (requirements.txt or python/) in Dev's code")
        old = layers_rec.get(lname) or {}
        if old.get("key") == b["key"] and old.get("arn"):
            arns[key] = old["arn"]
            layers_rec[lname] = {**old, "folder": key}
            continue
        data = (work / b["zip"]).read_bytes()
        if len(data) > 50 * 1024 * 1024:
            raise AgentError(f"Layer {key} is {len(data) // 1048576} MB zipped: over the 50 MB direct-upload limit")
        r = lam.publish_layer_version(LayerName=lname, Content={"ZipFile": data}, Description=f"Orkestra {version} (Dev)",
                                      CompatibleRuntimes=[f"python{b['python']}"],
                                      CompatibleArchitectures=["arm64" if b["arch"] == "aarch64" else "x86_64"])
        aws_access.audit(pid, "de", "lambda:PublishLayerVersion", r["LayerVersionArn"], True, f"{len(data) // 1024} KB")
        arns[key] = r["LayerVersionArn"]
        layers_rec[lname] = {"key": b["key"], "folder": key, "arn": r["LayerVersionArn"], "version": r["Version"], "kb": len(data) // 1024,
                             "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "code_version": version}
        changed.append(f"📦 layer **{lname}** v{r['Version']} ({len(data) // 1024} KB)")
    functions: dict = dict(prev.get("functions") or {})
    for fkey, f in (cd.get("functions") or {}).items():
        fname, src = f["function_name"], str(f.get("source_dir") or "").strip("/")
        folder = work / src
        if not src or not folder.is_dir():
            raise AgentError(f"{fname}: Terra expects the code in {src}/, which isn't in Dev's code")
        lam.get_waiter("function_active").wait(FunctionName=fname)
        cfg = lam.get_function_configuration(FunctionName=fname)
        module = cfg["Handler"].rsplit(".", 1)[0].replace(".", "/") + ".py"
        if not (folder / module).exists():
            raise AgentError(f"{fname}: its handler is {cfg['Handler']}, so {src}/{module} must exist in Dev's code")
        data = terraform.zip_folder(folder)
        if len(data) > 50 * 1024 * 1024:
            raise AgentError(f"{fname}: the code zip is over the 50 MB direct-upload limit")
        want = [x["Arn"] for x in cfg.get("Layers") or []] if managed else [arns[k] for k in (f.get("layers") or []) if k in arns]
        what = []
        if cfg["CodeSha256"] != _sha(data):
            lam.update_function_code(FunctionName=fname, ZipFile=data)
            lam.get_waiter("function_updated").wait(FunctionName=fname)
            aws_access.audit(pid, "de", "lambda:UpdateFunctionCode", fname, True, f"{len(data) // 1024} KB")
            what.append(f"code {len(data) // 1024} KB")
        if managed:  # the layers Terra attached: recorded for the report, never touched by Dev
            sizes = load_packages(pid)["layers"]
            for arn in want:
                lname, ver = arn.split(":")[-2], int(arn.split(":")[-1])
                key = next((k for k, n in (cd.get("layers") or {}).items() if n == lname), None)
                layers_rec[lname] = {"key": (sizes.get(key) or {}).get("fingerprint", ""), "folder": key, "arn": arn, "version": ver,
                                     "kb": (sizes.get(key) or {}).get("kb", 0), "code_version": version, "by": "terra"}
        elif [x["Arn"] for x in cfg.get("Layers") or []] != want:
            lam.update_function_configuration(FunctionName=fname, Layers=want)
            lam.get_waiter("function_updated").wait(FunctionName=fname)
            aws_access.audit(pid, "de", "lambda:UpdateFunctionConfiguration", fname, True, f"layers: {len(want)}")
            what.append(f"{len(want)} layer(s)")
        functions[fname] = {"key": fkey, "source_dir": src, "sha": _sha(data), "kb": len(data) // 1024, "layers": want,
                            "handler": cfg["Handler"], "runtime": cfg.get("Runtime"), "code_version": version,
                            "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if what else
                            (functions.get(fname) or {}).get("deployed_at")}
        (changed.append(f"⚡ **{fname}**: {', '.join(what)}") if what else unchanged.append(fname))
    return {"functions": functions, "layers": layers_rec, "changed": changed, "unchanged": unchanged}


async def sanity_check(ctx: JobContext, pid: str, creds: dict, version: str) -> dict:
    from app.agents import livekit
    from app.services import inventory

    dep = aws_access.load(pid, "deploy") or {}
    intake = await load_intake(pid)
    box: dict = {}

    hosts = livekit.allowed_hosts(dep.get("outputs") or {})
    prefix = (aws_access.load(pid) or {}).get("prefix") or "orkestra-"
    # the console pages in Dev's brief; some name the resource by id, not by the prefix (API Gateway: /apis/<id>/…)
    known = [r["console"] for r in (inventory.load(pid) or {}).get("resources", []) if r.get("console")]

    has_function = any(r["type"] == "aws_lambda_function" for r in (inventory.load(pid) or {}).get("resources", []))

    async def verify(a: dict):
        """Any flow shape (10-05: an EventBridge schedule → Lambda → S3 flow was refused for having no API and no queue):
        the flow started the way it really starts, the result read back from wherever it lands, and the logs in CloudWatch."""
        log = box.get("log", [])
        if box.get("calls", 0) < 2:
            raise AgentError("Run the check first: start the flow once and read what it produced and the logs.")
        triggered = any(x.startswith(livekit.TRIGGERS) for x in log)
        missing = [step for step, seen in (
            ("call the endpoint like the real caller (http_request): this flow has an HTTP front door",
             not livekit.front_doors(hosts) or any(x.startswith("HTTP ") for x in log)),
            ("start the flow the way it really starts (sqs_send, sns_publish, events_put, s3_put; invoke_lambda for a schedule or "
             "a direct call)", triggered),
            ("read where the result lands (sqs_receive, s3_list/s3_get, dynamodb_read, or the HTTP response)",
             any(x.endswith(livekit.DESTINATION_READS) or x.startswith("HTTP ") for x in log)),
            ("read the function's logs (read_logs)", any(x.startswith("logs ") for x in log))) if not seen]
        if missing:
            raise AgentError("Test the whole flow hop by hop first. Still missing: " + "; ".join(missing))
        if len(a["try_it"]) < 2:
            raise AgentError("try_it: give the user a walkthrough of every hop (start the flow, see the result where it lands, "
                             "the logs), so they can repeat your test in the AWS console.")
        for t in a["try_it"]:
            u = urlparse(t["link"])
            ours = u.hostname in hosts or (str(u.hostname).endswith("console.aws.amazon.com") and (
                prefix in unquote(t["link"]) or any(t["link"].startswith(k) for k in known)))
            if u.scheme != "https" or not ours:
                raise AgentError(f"try_it '{t['title']}': the link must be one of this project's console pages from the brief, "
                                 f"or the API URL, exactly as given (got {t['link'][:200]}).")
        landed = any(x.endswith(livekit.DESTINATION_READS) and not x.endswith((": 0 message(s)", ": 0 object(s)", ": 0 item(s)"))
                     for x in log) or any(x.startswith("HTTP ") and "→ 2" in x for x in log)
        if a["passed"] and not (a["received"].strip() and landed):
            raise AgentError("A pass needs evidence from where the result lands: read it back (the queue, the S3 object, the "
                             "DynamoDB item, or the HTTP response) and put what you found in `received`.")
        if a["passed"] and has_function and not any(x.startswith("logs ") and not x.endswith(": 0 event(s)") for x in log):
            raise AgentError("The function ran but no log events showed up in CloudWatch. Wait ~20 s and read_logs again; if there are "
                             "still none, the logs aren't reaching CloudWatch (usually the role's log permission): report passed=false "
                             "with that as the problem.")
        if any(x.startswith("invoked ") for x in log) or a["test_event"].strip():
            try:
                json.loads(a["test_event"])
            except ValueError as exc:
                raise AgentError("test_event must be the exact JSON event you invoked the function with.") from exc

    examples = json.loads(examples_fixture(pid) or "[]")
    ask = (f"# Requirement (excerpt)\n{(intake.requirement_md or '')[:6000]}\n\n# What's live ({version})\n"
           + livekit.deployment_brief(dep, inventory.load(pid))
           + "\n\n# Atlas's worked examples (use the first \"output\" one)\n```json\n"
           + json.dumps([e for e in examples if e.get("expect") == "output"][:2] or examples[:1], indent=2, ensure_ascii=False)[:8000]
           + "\n```\n\nRun the sanity check, then call submit_sanity.")
    res = await run_loop(project_id=pid, agent="de",
                         system=[{"type": "text", "text": llm.prompt("de_sanity.md") + "\n\n" + llm.prompt("org_context.md"),
                                  "cache_control": {"type": "ephemeral"}}],
                         messages=[{"role": "user", "content": ask}],
                         tools=[*livekit.tools(pid, "de", creds, dep.get("outputs") or {}, box), replace(SUBMIT_SANITY, handler=verify)],
                         max_turns=16, purpose="sanity")
    data = res.terminal.get("submit_sanity")
    if not data:
        raise AgentError("Dev finished the sanity check without a verdict.")
    return {**data, "calls": box.get("calls", 0), "log": box.get("log", [])}


def lambda_console(function: str | None = None, layer: str | None = None, version: int | None = None) -> str:
    from app.services.inventory import CONSOLE, R

    if layer:
        return f"{CONSOLE}/lambda/home?region={R}#/layers/{layer}/versions/{version}"
    return f"{CONSOLE}/lambda/home?region={R}#/functions/{function}?tab=code"


def _layer_names(pushed: dict, arns: list[str]) -> list[str]:
    by_arn = {l["arn"]: f"{n} v{l['version']}" for n, l in pushed["layers"].items()}
    return [by_arn.get(a, a.rsplit(":", 2)[-2] + " v" + a.rsplit(":", 1)[-1]) for a in arns]


def live_summary(version: str, pushed: dict) -> str:
    """Plain words for the code gate: what is in AWS now, who put it there, where to look."""
    fns = "; ".join(f"{n} (code from {f['source_dir']}/, {f['kb']} KB"
                    + (f", layers {', '.join(_layer_names(pushed, f.get('layers') or []))}" if f.get("layers") else "") + ")"
                    for n, f in pushed["functions"].items())
    lay = ", ".join(f"{n} v{l['version']}" for n, l in pushed["layers"].items())
    by_terra = any(l.get("by") == "terra" for l in pushed["layers"].values())
    imgs = "; ".join(f"image {k} ({i.get('code_version')}){' in ' + i['where'] if i.get('where') else ''}" for k, i in (pushed.get("images") or {}).items())
    if imgs:
        fns = "; ".join(x for x in (fns, imgs) if x)
    if any(f.get("by") == "terra" for f in [*pushed["functions"].values(), *(pushed.get("images") or {}).values()]):  # Terra deploys everything (10-03)
        return (f"Live in AWS now ({version}): {fns}. Dev built the packages (each function's src/ folder"
                + (", his container images from their Dockerfiles (pushed to ECR)" if imgs else "")
                + (f", and the layers {lay} from layers/" if lay else "") + ") and handed them to Terra, who deployed them with "
                "Terraform in a plan you approved: the placeholder code is gone, and code, layers and infrastructure are all in one "
                "Terraform state. Dev checked each function runs exactly his package. Each Lambda holds only its src/ folder (no tests "
                "or docs). Check it in the Lambda console (links on the Build tab).")
    return (f"Live in AWS now ({version}): {fns}. "
            + ((f"Dev built the layer packages from layers/; Terra published them ({lay}) and attached them, in a plan you approved. " if by_terra
                else f"Dev built the layers from layers/, published them ({lay}) and attached them to the functions. ") if lay else "")
            + "Each Lambda holds only its src/ folder (no tests or docs). Check it in the Lambda console (links on the Build tab).")


def deploy_report(version: str, role: str, pushed: dict, sanity: dict | None) -> str:
    by_terra = any(l.get("by") == "terra" for l in pushed["layers"].values())
    terra_code = any(f.get("by") == "terra" for f in [*pushed["functions"].values(), *(pushed.get("images") or {}).values()])
    who = ("Who did what: Terra created the functions with placeholder code. Dev wrote the code, Archie reviewed it and you approved "
           "it; Dev built the packages (each function's `src/` folder, each layer from `layers/`) and handed them to Terra. Terra "
           "deployed them with Terraform in a plan you approved, replacing the placeholder: code, layers and infrastructure are in "
           "one Terraform state, so any change made outside it (e.g. in the console) shows up as drift. Dev then checked every "
           "function runs exactly his package and tested the whole flow." if terra_code else
           "Who did what: Terra created the functions (placeholder code); Dev built each layer package from `layers/` and handed it "
           "to Terra, who published it and attached it to the functions in a plan you approved; then Dev uploaded each function's code."
           if by_terra else "Who did what: Terra created the functions (placeholder code) and Dev's layer permission; Dev built each layer zip "
           "from `layers/`, published it as a new layer version, attached it to the functions, and uploaded each function's code.")
    lines = [f"# Code deploy · {version}", "", ("Deployed by Terra (Terraform) from Dev's packages; checked and tested by Dev with "
             f"`{role}`." if terra_code else f"Deployed with `{role}` into Terra's functions.") + " **This code is live in AWS now.**", "",
             who, "",
             "Each Lambda zip holds only the function's `src/` folder: no tests, docs, reports or caches.", "", "## Functions", "",
             "| Function | Code | Layers attached | Deployed | Console |", "|---|---|---|---|---|"]
    lines += [f"| {n} | {f['source_dir']}/ ({f['kb']} KB) | {', '.join(_layer_names(pushed, f.get('layers') or [])) or '-'} | "
              f"{f.get('deployed_at') or '-'} | [open]({lambda_console(n)}) |" for n, f in pushed["functions"].items()]
    if pushed.get("images"):
        lines += ["", "## Container images (built by Dev, pushed to ECR, deployed by Terra)", "", "| Image | Built from | Runs in | Digest |", "|---|---|---|---|"]
        lines += [f"| {k} | `{i.get('source_dir')}/Dockerfile` | {i.get('where') or '-'} | `{str(i.get('uri')).rsplit('@', 1)[-1][:19]}…` |"
                  for k, i in pushed["images"].items()]
    if pushed["layers"]:
        lines += ["", "## Layers (built by Dev, published and attached by Terra)" if by_terra or terra_code else "## Layers (built, published and attached by Dev)", "",
                  "| Layer | Version | Built from | Size | Console |", "|---|---|---|---|---|"]
        lines += [f"| {n} | v{l['version']} | {('layers/' + l['folder'] + '/') if l.get('folder') else '-'} | {l['kb']} KB | "
                  f"[open]({lambda_console(layer=n, version=l['version'])}) |" for n, l in pushed["layers"].items()]
    if sanity:
        lines += ["", f"## Sanity check: {'✅ passed' if sanity['passed'] else '❌ failed'}", "", sanity["summary"], ""]
        for s in sanity.get("steps", []):
            lines.append(f"- {'✅' if s['ok'] else '❌'} **{s['what']}**: {s['observed']}")
            if s.get("how"):
                lines.append(f"  - how: {s['how']}")
        if sanity.get("try_it"):
            lines += ["", "## Test it yourself in the AWS console", "", "Repeat Dev's test hop by hop before you approve:"]
            for t in sanity["try_it"]:
                where = f"[Open in AWS]({t['link']})" if "console.aws.amazon.com" in t["link"] else f"URL: `{t['link']}`"
                lines += ["", f"### {t['title']}", "", where, "", t["steps"]]
                if t.get("input"):
                    lines += ["", "```", t["input"], "```"]
                lines += ["", f"**You should see:** {t['expect']}"]
        if sanity.get("sent"):
            lines += ["", "### Sent (the real caller's way)", "", "```", sanity["sent"], "```"]
        if sanity.get("received"):
            lines += ["", "### Received at the destination", "", "```", sanity["received"], "```"]
        if sanity.get("left_for_user"):
            lines += ["", f"**Left for you to see:** {sanity['left_for_user']} (SQS console → the queue → Send and receive messages → "
                      "Poll for messages)."]
        if sanity.get("test_event"):
            lines += ["", "### Try it yourself in the Lambda console", "",
                      "Open the function → **Test** → *Create new test event* → paste this event → **Test** (also in "
                      "`reports/lambda_test_event.json`):", "", "```json", sanity["test_event"], "```"]
    return "\n".join(lines) + "\n"


async def _hand_to_terra(ctx: JobContext, pid: str, cd: dict, handed: dict, built: list[dict], version: str, tickets: list,
                         changes: str, attempt: int, reviewed: bool) -> None:
    """Dev → (Archie) → Terra: the packages wait in Terra's workspace; Terra plans them, the user approves, Terra applies,
    then Dev checks and tests the live flow (tp.apply sends the job back)."""
    store = ProjectStore(pid)
    lines = [f"- ⚡ code `{h['source_dir']}/` → **{h['function_name']}** ({h['kb']} KB): replaces "
             + ("the placeholder" if h["from"] == "placeholder" else f"my {h['from']} code") for h in handed["code"]]
    lines += [f"- 📦 layer **{h['key']}** → `{h['name']}` ({h['kb']} KB, built from layers/{h['key']}/)" for h in handed["layers"]]
    lines += [f"- 🐳 image **{h['key']}** (built from `{h['source_dir']}/Dockerfile`, pushed to ECR as `…{str(h.get('uri'))[-19:]}`) → "
              f"**{h['function_name']}**: replaces " + ("the placeholder" if h["from"] == "placeholder" else f"my {h['from']} image")
              for h in handed.get("images") or []]
    dep = aws_access.load(pid, "deploy") or {}
    aws_access.save(pid, {**dep, "intent": {"reason": "packages", "code": [h["key"] for h in handed["code"]], "layers": [h["key"] for h in handed["layers"]],
                                            "images": [h["key"] for h in handed.get("images") or []],
                                            "handover": handed, "tickets": tickets, "changes": changes, "attempt": attempt, "version": version}}, "deploy")
    store.append_log("work", {"agent": "de", "phase": "deploy", "changed": [x.removeprefix("- ") for x in lines], "unchanged": [],
                              "layers": [{"layer": b["layer"], "kb": b["kb"], "cached": b["cached"]} for b in built]})
    if reviewed:
        await crewchat.say(pid, "ta", "tp", f"Terra, I reviewed Dev's {version} and the user approved it: it's yours to deploy.", "handoff")
    parts = [f"{len(handed['code'])} code package(s)" if handed["code"] else "", f"{len(handed['layers'])} layer package(s)" if handed["layers"] else "",
             f"{len(handed.get('images') or [])} container image(s)" if handed.get("images") else ""]
    what = " and ".join(p for p in parts if p)
    await crewchat.say(pid, "de", "tp", f"Terra, here's {version} for AWS, packed and ready in your workspace:\n" + "\n".join(lines)
                       + "\nPlease deploy them with Terraform. I check them and test the whole flow as soon as they're live.", "handoff")
    await ctx.set_agent("de", "waiting", f"Handed {what} to Terra: he deploys them after you approve his plan")
    await ctx.set_project(last_activity=f"Dev handed {what} to Terra")
    await ctx.emit("build.updated", f"Dev handed {what} to Terra", agent="de")
    await runner_mod.runner.enqueue("tp.deploy", project_id=pid)


@handler("de.deploy", resumable=False)
async def deploy(ctx: JobContext) -> None:
    """Dev's deploy. Terra deploys everything (the platform's packages.tf): Dev packs each function's code and each layer,
    hands what changed to Terra (a plan the user approves), and once it's live checks every function runs exactly his
    package, then sanity-checks the whole flow. Older projects: Dev uploads the code himself (and the layers, before
    10-02). `sync`: after an infrastructure change, only make sure the code is still in place."""
    from app.services import tickets as tk

    pid = ctx.project_id
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    sync = bool(ctx.payload.get("sync"))
    # after the code-review gate the job carries only the user's comment: tickets, changes and the attempt come from
    # Archie's review record for this version
    rv = aws_access.load(pid, "code_review") or {}
    rv = rv if rv.get("version") == version and not sync else {}
    tickets = ctx.payload.get("tickets") or rv.get("tickets") or []
    changes = ctx.payload.get("changes") or rv.get("changes") or ""
    attempt = int(ctx.payload.get("attempt") or rv.get("attempt") or 0)
    acc = aws_access.load(pid)
    cd = code_deploy_map(pid)
    role = aws_access.role_name(pid, "de")
    await ctx.set_agent("de", "working", f"🔑 Acting as {role}")
    await ctx.set_project(status="running", last_activity="Dev is deploying his code into AWS")
    try:
        if not acc or acc.get("status") != "active":
            raise AgentError("there's no AWS role for Dev yet")
        if not cd.get("functions") and not cd.get("images"):
            await crewchat.say(pid, "de", "tp", "Terra, your outputs have no `code_deploy` map, so I don't know which folder goes into "
                               "which function or image. Please add it.", "issue")
            raise AgentError("Terra's outputs have no code_deploy map")
        creds = await asyncio.to_thread(aws_access.assume, pid, "de")
        infra = files_under(store, ("infra/",))
        managed = terraform.manages_layers(infra)
        terra_code = terraform.manages_code(infra)
        work = terraform.prepare(aws_access.deploy_dir(pid) / "code_work", files_under(store, ("infra/", "src/", "layers/")))
        await ctx.set_agent("de", "working", "📦 Building the layer packages (Linux wheels for the Lambda runtime)")
        built = await terraform.build_layers(work)
        handed: dict = {"code": [], "layers": [], "images": []}
        if terra_code:
            await asyncio.to_thread(check_fit, creds, cd, work)
            if cd.get("images"):  # ECS tasks, EC2, container-image Lambdas: build and push only what changed
                await ctx.set_agent("de", "working", "🐳 Building and pushing my container images to ECR")
                await asyncio.to_thread(push_images, pid, creds, work, cd, version)
            await ctx.set_agent("de", "working", "📦 Packing each function's code for Terra")
            handed = hand_over(pid, work, built, cd, version)
        elif managed and not sync:
            handed = hand_over(pid, work, built, cd, version, code=False)
        if not (handed["code"] or handed["layers"] or handed["images"]):
            if terra_code:
                await ctx.set_agent("de", "working", "🔎 Checking every function runs exactly my package")
                pushed = await asyncio.to_thread(record_live, pid, creds, cd, version)
            else:
                await ctx.set_agent("de", "working", "🚀 Uploading the code into the functions")
                pushed = await asyncio.to_thread(push_code, pid, creds, cd, work, built, version, managed)
    except NotDevsCode as exc:  # Terra's wiring or a console edit: not something Dev's code can fix
        await ctx.set_agent("de", "failed", str(exc)[:240])
        await ctx.set_project(status="failed", last_activity=str(exc)[:200])
        await ctx.emit("build.updated", "A function in AWS isn't running Dev's package", agent="de")
        await crewchat.say(pid, "de", "tp", f"Terra, {exc}. Your Terraform should deploy my package there (lookup(var.code_packages, …)); "
                           "if someone changed it in the console, the AWS tab's drift check can put it back.", "issue")
        from app.agents import unblock

        await unblock.escalate(ctx, "de", "de.deploy", "A function in AWS doesn't run Dev's package", exc)
        raise
    except AgentError as exc:
        if attempt < 1 and not sync and "code_deploy map" not in str(exc) and "no AWS role" not in str(exc):
            # the code doesn't fit Terra's functions (folder, handler, layer): Dev fixes it before anyone else sees it
            await crewchat.say(pid, "de", "crew", f"My deploy didn't fit Terra's functions: {str(exc)[:300]} Fixing it.", "issue")
            await runner_mod.runner.enqueue("de.code", project_id=pid, tickets=tickets, attempt=attempt + 1,
                                            feedback=f"Deploying {version} into Terra's functions failed: {exc}")
            return
        await ctx.set_agent("de", "failed", f"Deploy failed: {str(exc)[:200]}")
        await ctx.set_project(status="failed", last_activity=f"Dev's deploy failed: {str(exc)[:160]}")
        await ctx.emit("build.updated", "Dev's deploy failed", agent="de")
        await crewchat.say(pid, "de", "cto", f"My deploy failed: {str(exc)[:500]}", "issue")
        from app.agents import unblock

        await unblock.escalate(ctx, "de", "de.deploy", "Dev's deploy failed", exc)
        raise
    except Exception as exc:
        await ctx.set_agent("de", "failed", f"Deploy failed: {str(exc)[:200]}")
        await ctx.set_project(status="failed", last_activity=f"Dev's deploy failed: {str(exc)[:160]}")
        await ctx.emit("build.updated", "Dev's deploy failed", agent="de")
        await crewchat.say(pid, "de", "cto", f"My deploy failed: {str(exc)[:500]}", "issue")
        from app.agents import unblock

        await unblock.escalate(ctx, "de", "de.deploy", "Dev's deploy failed", exc)
        raise
    if handed["code"] or handed["layers"] or handed["images"]:  # Terra deploys the packages first (a plan you approve), then Dev checks and tests
        await _hand_to_terra(ctx, pid, cd, handed, built, version, tickets, changes, attempt, bool(rv))
        return
    store.append_log("work", {"agent": "de", "phase": "deploy", "changed": pushed["changed"], "unchanged": pushed["unchanged"],
                              "layers": [{"layer": b["layer"], "kb": b["kb"], "cached": b["cached"]} for b in built]})
    prev = aws_access.load(pid, CODE_JSON) or {}
    n = int(prev.get("deploys") or 0) + (1 if pushed["changed"] else 0)
    rec = {**prev, "status": "deployed", "version": version, "role": role, "functions": pushed["functions"], "layers": pushed["layers"],
           "images": pushed.get("images") or {}, "layers_by": "terra" if managed else "dev", "code_by": "terra" if terra_code else "dev", "deploys": n,
           "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    aws_access.save(pid, rec, CODE_JSON)
    if pushed["changed"]:
        await crewchat.say(pid, "de", "crew", (f"Checked what Terra deployed for {version}: every function runs exactly my package." if terra_code
                                               else f"Deployed {version} with `{role}`:") + "\n" + "\n".join(f"- {c}" for c in pushed["changed"])
                           + (f"\nUnchanged: {', '.join(pushed['unchanged'])}" if pushed["unchanged"] else ""), "work")
    if sync and not pushed["changed"]:
        await crewchat.say(pid, "de", "cto", f"Checked after the infrastructure change: my code is in place on all {len(pushed['functions'])} "
                           "function(s); nothing to redeploy.", "update")
        await ctx.set_agent("de", "done", "Code in place: nothing to redeploy")
        await runner_mod.runner.enqueue("cto.tickets", project_id=pid)
        return
    try:
        await ctx.set_agent("de", "working", "🩺 Sanity check: one test message through the live flow")
        sanity = await sanity_check(ctx, pid, creds, version)
    except Exception as exc:
        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("de", "blocked" if blocked else "failed", f"Sanity check: {str(exc)[:200]}")
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Dev's sanity check: {str(exc)[:160]}")
        await crewchat.say(pid, "de", "cto", f"Deployed, but my sanity check couldn't run: {str(exc)[:300]}", "issue")
        if not blocked:
            from app.agents import unblock

            await unblock.escalate(ctx, "de", "de.deploy", "Dev's live check of the whole flow couldn't run", exc)
        raise
    rec["sanity"] = {k: sanity.get(k, "") for k in ("passed", "summary", "steps", "calls", "sent", "received", "left_for_user", "try_it")} | {"version": version}
    aws_access.save(pid, rec, CODE_JSON)
    if sanity.get("test_event"):  # the user can replay it in the Lambda console's Test tab
        try:
            store.write("reports/lambda_test_event.json", json.dumps(json.loads(sanity["test_event"]), indent=2, ensure_ascii=False))
        except ValueError:
            pass
    store.write("reports/code_deploy.md", deploy_report(version, role, pushed, sanity))
    store.append_changelog(version, [("Dev's packages deployed to AWS by Terra" if terra_code else "Code deployed to AWS by Dev")
                                     + f" ({len(pushed['changed'])} change(s)); Dev's sanity check {'passed' if sanity['passed'] else 'failed'}"])
    if not sanity["passed"] and sanity.get("failure_area") == "infra":
        # not Dev's code: Archie diagnoses, Orion routes the fix to Terra (agents/unblock.py); the user approves Terra's plan
        from app.agents import unblock

        evidence = "\n".join(f"- {s['what']}: {s['observed']}" for s in sanity.get("steps", []))
        await crewchat.say(pid, "de", "ta", f"My live check found an infrastructure problem, not my code: {sanity['summary']}", "issue")
        if await unblock.escalate(ctx, "de", "de.deploy", "Dev's live check found an infrastructure problem",
                                  AgentError(f"{sanity['summary']}\n{evidence}")):
            return
    if not sanity["passed"] and attempt < 1:  # a real developer fixes it before anyone else sees it
        evidence = "\n".join(f"- {s['what']}: {s['observed']}" for s in sanity.get("steps", []))
        await crewchat.say(pid, "de", "crew", f"❌ My sanity check failed: {sanity['summary']} Fixing it before handing over.", "issue")
        await runner_mod.runner.enqueue("de.code", project_id=pid, tickets=tickets, attempt=attempt + 1,
                                        feedback=f"Your live sanity check after deploying {version} failed: {sanity['summary']}\n{evidence}")
        return
    tix = await tk.rows_for(pid, tickets)
    for t in tix:
        if t["status"] in tk.ACTIVE:
            await tk.change(t["id"], "de", f"Fixed in {version} with a regression test, deployed to AWS"
                            + (f" (sanity check: {sanity['summary']})" if sanity["passed"] else "") + "."
                            + (f" {changes}" if changes else "") + " Quinn, please retest.",
                            status="resolved", assignee="qa", version_fixed=version)
    ok = sanity["passed"]
    if ok:
        from app.agents import unblock

        unblock.clear(pid, "de")  # live and checked: a later problem gets fresh help from Archie and Orion
    line = f"deployed {len(pushed['functions'])} function(s) · sanity check {'passed' if ok else 'FAILED'}"
    await crewchat.say(pid, "de", "cto", f"{'✅' if ok else '⚠️'} {version} is deployed and {('sanity-checked: ' + sanity['summary']) if ok else 'still fails my sanity check: ' + sanity['summary']}\n"
                       + live_summary(version, pushed), "handoff", files=["reports/code_deploy.md", "reports/coverage.html", "reports/pytest.md"])
    await ctx.set_agent("de", "needs_approval", f"Code {line}")
    await ctx.set_project(progress=round(5 / 7, 3))
    await ctx.emit("build.updated", f"Dev {line}", agent="de")
    labels = ", ".join(t["label"] for t in tix)
    title = (f"Dev's fix for {labels} ({version}), deployed and tested live" if labels else
             f"{version} is live (Terra deployed Dev's packages) and Dev tested the whole flow: your check" if terra_code else
             f"Dev deployed {version} and tested the whole flow: your check") + ("" if ok else " ⚠️ sanity check failed")
    evidence = (f" I sent: {sanity['sent'][:400]} → received at the destination: {sanity['received'][:400]}."
                if sanity.get("sent") and sanity.get("received") else "")
    await flow.request_approval(pid, "code", title, live_summary(version, pushed) + " "
                                + (f"Sanity check passed: {sanity['summary']}" if ok else f"Sanity check failed: {sanity['summary']}") + evidence
                                + (f" Waiting for you in SQS: {sanity['left_for_user']}." if sanity.get("left_for_user") else "")
                                + " Approving hands over to Quinn's live tests.",
                                [*sorted(p for p in files_under(store, ("src/", "layers/")))[:9], "reports/code_deploy.md", "reports/coverage.html",
                                 "reports/pytest.md", *(["reports/lambda_test_event.json"] if sanity.get("test_event") else [])])


def load_code(project_id: str) -> dict | None:
    store = ProjectStore(project_id)
    meta, res = read(store, "reports/code.json"), read(store, "reports/pytest.json")
    if not meta:
        return None
    have = {f["path"] for f in store.tree()}
    try:
        return {**json.loads(meta), "tests": json.loads(res) if res else None, "deployed": aws_access.load(project_id, CODE_JSON),
                "reports": [p for p in ("reports/coverage.html", "reports/pytest.md", "reports/code_deploy.md", "reports/lambda_test_event.json")
                            if p in have]}
    except ValueError:
        return None
