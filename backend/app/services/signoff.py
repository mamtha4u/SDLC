"""Sign-off documents: when the user approves a stage, the platform writes that agent's sign-off (signoff/NN-<stage>.md).
Each one has what was delivered, the files, the facts that matter for that stage, the review history (changes asked,
approvals), and who approved it and when. No AI: everything comes from the approval records and the agents' own
outputs, so nothing is invented. They show in the agent's popup (Sign-off) and in the Code tab (Sign-offs).

Quinn's test sign-off (signoff/10-test-signoff.md) is the full test report: the approved test plan, every scenario's
result and evidence, every run, every ticket's life cycle (raised by, fixed by, retested), what wasn't tested and why,
the exit criteria, Quinn's sign-off statement, and the user's approval once given.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import Approval, Intake, Project, User
from app.services.storage import ProjectStore

# stage → (order, agent, what is signed off)
STAGES: dict[str, tuple[str, str, str]] = {
    "requirement": ("00", "intake", "Requirement"),
    "plan": ("01", "cto", "Delivery plan"),
    "mapping": ("02", "ba", "Data mapping"),
    "design": ("03", "ta", "Design: HLD, LLD and architecture diagram"),
    "infra": ("04", "tp", "Infrastructure code (Terraform) and the crew's AWS access"),
    "deploy": ("05", "tp", "Terraform plan, applied in AWS"),
    "infra_check": ("06", "tp", "Infrastructure checked in AWS"),
    "code_review": ("07", "ta", "Code review (Archie) and your check, before the deploy"),
    "code": ("08", "de", "Deploy and full-flow test (Dev)"),
    "test_plan": ("09", "qa", "Test plan"),
    "live": ("10", "qa", "Test sign-off"),
}
ROLE = {"intake": "Echo (Business Analyst)", "cto": "Orion (CTO)", "ba": "Atlas (Data Analyst)",
        "ta": "Archie (Technical Architect)", "tp": "Terra (Platform Engineer)", "de": "Dev (Data Engineer)", "qa": "Quinn (QA Engineer)"}
TEST_SIGNOFF = "signoff/10-test-signoff.md"


def path(stage: str) -> str:
    return TEST_SIGNOFF if stage == "live" else f"signoff/{STAGES[stage][0]}-{stage.replace('_', '-')}.md"


def _when(d: datetime | None) -> str:
    return d.strftime("%Y-%m-%d %H:%M UTC") if d else "-"


def _json(store: ProjectStore, p: str) -> dict:
    try:
        return json.loads(store.read(p).decode("utf-8"))
    except Exception:  # noqa: BLE001 (missing or not JSON)
        return {}


async def _people(project_id: str) -> tuple[str, str]:
    async with SessionLocal() as db:
        p = await db.get(Project, project_id)
        u = await db.get(User, p.owner_id) if p else None
        return (p.name if p else project_id), (u.display_name or u.username if u else "the project owner")


async def _approvals(project_id: str, stage: str) -> list[Approval]:
    async with SessionLocal() as db:
        return list((await db.execute(select(Approval).where(Approval.project_id == project_id, Approval.stage == stage,
                                                             Approval.status.in_(["approved", "changes_requested"]))
                                      .order_by(Approval.decided_at))).scalars())


def _facts(project_id: str, stage: str, store: ProjectStore) -> list[str]:
    """The few facts that matter for each stage, from the agent's own output. Missing data → no line."""
    out: list[str] = []
    try:
        if stage == "mapping":
            m = _json(store, "mapping/01_data_mapping.json")
            if m:
                out += [f"Target fields mapped: {len(m.get('rows', []))}", f"Worked examples (checked by the platform): {len(m.get('samples', []))}"]
        elif stage == "design":
            d = _json(store, "diagrams/design.json")
            if d:
                out += [f"Resources designed: {len(d.get('resources', []))}",
                        f"Quality gate: line coverage ≥ {(d.get('quality_gates') or {}).get('min_coverage_percent', '-')}%"]
        elif stage == "infra":
            pv = _json(store, "infra/plan_preview.json")
            if pv:
                out += [f"Resources in the Terraform: {len(pv.get('resources', []))}",
                        f"terraform validate: {'passed' if (pv.get('validate') or {}).get('ok') else 'not run / failed'}",
                        f"Terra's monthly estimate: ${sum(r.get('monthly_usd') or 0 for r in pv.get('resources', [])):.2f}"]
        elif stage in ("deploy", "infra_check"):
            from app.services import aws_access, inventory

            dep = aws_access.load(project_id, "deploy") or {}
            inv = inventory.load(project_id) or {}
            if inv.get("resources"):
                prim = [r for r in inv["resources"] if r.get("primary")]
                out.append(f"Resources live in AWS: {inv.get('count', len(inv['resources']))}")
                out += [f"  - {r['kind']}: {r['name']}" for r in prim[:20]]
            if dep.get("applied_at"):
                out.append(f"Applied: {dep['applied_at']}")
        elif stage == "code":
            c = _json(store, "reports/code.json")
            t = _json(store, "reports/pytest.json")
            from app.services import aws_access

            live = aws_access.load(project_id, "code") or {}
            if c:
                out += [f"Unit tests: {t.get('passed', '?')}/{t.get('total', '?')} passing",
                        f"Line coverage: {c.get('coverage')}% (gate {c.get('coverage_gate')}%)"]
            if live.get("functions"):
                out.append("Deployed to: " + ", ".join(live["functions"]))
            s = live.get("sanity") or {}
            if s:
                out.append(f"Dev's sanity check: {'passed' if s.get('passed') else 'failed'}: {s.get('summary', '')}")
                if s.get("sent") and s.get("received"):
                    out += [f"  - sent: {s['sent'][:300]}", f"  - received: {s['received'][:300]}"]
        elif stage == "code_review":
            from app.agents.codereview import STATE, load_review
            from app.services import aws_access

            r = load_review(project_id) or {}
            if r:
                sev = {k: sum(f["severity"] == k for f in r["findings"]) for k in ("must", "should", "nice")}
                t = r.get("tests") or {}
                out += [f"Archie's verdict: {r['verdict']} (review round {r['round']}): {r['summary']}",
                        f"His checklist: {sum(c['ok'] for c in r['checks'])}/{len(r['checks'])} points OK",
                        f"Findings: {sev['must']} must, {sev['should']} should, {sev['nice']} nice",
                        f"Unit tests: {t.get('passed')}/{t.get('total')} · coverage {t.get('coverage')}% (gate {t.get('gate')}%)"]
                out += [f"  - {n['topic']}: {n['recommendation']}" for n in r.get("design_notes", [])]
            chat = (aws_access.load(project_id, STATE) or {}).get("chat") or []
            asked = [m for m in chat if m["role"] == "user"]
            if asked:
                out.append(f"You asked Archie {len(asked)} question(s) before approving:")
                out += [f"  - {m['text'][:160]}" for m in asked[:10]]
        elif stage == "test_plan":
            from app.agents.qa import load_plan

            p = load_plan(project_id) or {}
            cases = p.get("cases", [])
            if cases:
                cats: dict[str, int] = {}
                for x in cases:
                    cats[x["category"]] = cats.get(x["category"], 0) + 1
                out += [f"Scenarios: {len(cases)} ({', '.join(f'{n} {k}' for k, n in cats.items())})",
                        f"High priority: {sum(x['priority'] == 'high' for x in cases)}"]
    except Exception:  # noqa: BLE001 (a fact we can't read is left out, never guessed)
        pass
    return out


async def write(project_id: str, stage: str) -> str | None:
    """(Re)write the sign-off for a stage from its approval records. Returns the path, or None if nothing is approved."""
    if stage == "live":
        return await write_test_signoff(project_id)
    if stage == "requirement":
        return await write_requirement(project_id)
    rows = await _approvals(project_id, stage)
    approved = [a for a in rows if a.status == "approved"]
    if not approved or stage not in STAGES:
        return None
    store = ProjectStore(project_id)
    name, person = await _people(project_id)
    last = approved[-1]
    order, agent, what = STAGES[stage]
    lines = [f"# Sign-off · {what}", "", f"**{ROLE[agent]}** · project **{name}** · {store.manifest()['current_version']}", "",
             "| | |", "|---|---|", f"| Delivered by | {ROLE[agent]} |", f"| Approved by | {person} |",
             f"| Approved on | {_when(last.decided_at)} |", f"| Approval | {last.title} |", "",
             "## What was signed off", "", last.summary or last.title, ""]
    facts = _facts(project_id, stage, store)
    if facts:
        lines += ["## Key facts", "", *[f"- {f}" if not f.startswith("  ") else f for f in facts], ""]
    if last.artifacts:
        lines += ["## Deliverables", "", *[f"- `{p}`" for p in last.artifacts], ""]
    lines += ["## Review history", ""]
    for a in rows:
        verb = "✅ approved" if a.status == "approved" else "✏️ changes requested"
        lines.append(f"- {_when(a.decided_at)}: {verb}: {a.title}" + (f". “{a.comment}”" if a.comment else ""))
    lines += ["", f"_Written by Orkestra from the approval records when {person} approved it. Nothing in it is generated by AI._", ""]
    p = path(stage)
    store.write(p, "\n".join(lines))
    return p


async def write_requirement(project_id: str) -> str | None:
    async with SessionLocal() as db:
        intake = await db.get(Intake, project_id)
        if not intake or not intake.signed_off_at:
            return None
        from app.db.models import ChangeRequest

        crs = list((await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == project_id)
                                     .order_by(ChangeRequest.number))).scalars())
    store = ProjectStore(project_id)
    name, person = await _people(project_id)
    md = intake.requirement_md or ""
    sections = [ln.lstrip("# ").strip() for ln in md.splitlines() if ln.startswith("## ")]
    lines = [f"# Sign-off · Requirement", "", f"**{ROLE['intake']}** · project **{name}** · {store.manifest()['current_version']}", "",
             "| | |", "|---|---|", f"| Written by | {ROLE['intake']}, from the interview with {person} |", f"| Signed off by | {person} |",
             f"| Signed off on | {_when(intake.signed_off_at)} |", f"| Document | `00_requirement.md` ({len(md.splitlines())} lines) |", ""]
    if sections:
        lines += ["## Sections", "", *[f"- {s}" for s in sections[:30]], ""]
    if crs:
        lines += ["## Change requests since the first sign-off", ""]
        lines += [f"- CR-{c.number:03d} ({c.status}, route {c.route or '-'}{', ' + c.version_from + ' → ' + c.version_to if c.version_to else ''}): "
                  f"{c.text[:200]}" for c in crs]
        lines.append("")
    lines += [f"_Written by Orkestra from Echo's sign-off record. Nothing in it is generated by AI._", ""]
    p = "signoff/00-requirement.md"
    store.write(p, "\n".join(lines))
    return p


async def backfill(project_id: str) -> list[str]:
    """Write every missing sign-off for the stages already approved (older projects)."""
    store = ProjectStore(project_id)
    have = {f["path"] for f in store.tree()}
    done = []
    for stage in STAGES:
        if path(stage) in have:
            continue
        try:
            p = await write(project_id, stage)
        except Exception:  # noqa: BLE001 (one stage's records must not stop the others)
            p = None
        if p:
            done.append(p)
    return done


async def listing(project_id: str) -> list[dict]:
    store = ProjectStore(project_id)
    have = {f["path"] for f in store.tree()}
    out = []
    for stage, (order, agent, what) in STAGES.items():
        p = path(stage)
        if p in have:
            out.append({"stage": stage, "order": order, "agent": agent, "title": what, "path": p})
    if "reports/test_plan.md" in have:
        out.append({"stage": "test_plan_doc", "order": "09", "agent": "qa", "title": "Test plan (the document)", "path": "reports/test_plan.md"})
    return sorted(out, key=lambda x: x["order"])


async def on_approved(project_id: str, a: Approval) -> None:
    """Called by flow.decide after an approval: stage side effects, then the sign-off document."""
    if a.stage == "test_plan":
        from app.agents.qa import mark_plan_approved

        mark_plan_approved(project_id, a)
    if a.stage in STAGES:
        await write(project_id, a.stage)


# ── Quinn's test sign-off ───────────────────────────────────────────────────────────────────────────────────────────
def _esc(s: object) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


async def write_test_signoff(project_id: str) -> str | None:
    """The full test report, rebuilt from the data every time (after each test run and when the user signs it off)."""
    from app.agents.qa import load_live, load_plan, load_runs
    from app.services import aws_access
    from app.services import tickets as tk

    store = ProjectStore(project_id)
    live = load_live(project_id)
    if not live:
        return None
    plan = load_plan(project_id) or {}
    runs = load_runs(project_id)
    tickets = await tk.listing(project_id, with_comments=True)
    code = aws_access.load(project_id, "code") or {}
    dep = aws_access.load(project_id, "deploy") or {}
    name, person = await _people(project_id)
    final = [a for a in await _approvals(project_id, "live") if a.status == "approved"]
    cases = plan.get("cases") or [{"id": c["id"], "title": c["title"], "category": "-", "priority": "-"} for c in live["checks"]]
    results = {c["id"]: c for c in live["checks"]}
    not_run = {x["id"]: x["why"] for x in live.get("not_run") or []}
    passed = [c for c in cases if results.get(c["id"], {}).get("passed")]
    failed = [c for c in cases if c["id"] in results and not results[c["id"]]["passed"]]
    skipped = [c for c in cases if c["id"] not in results]
    extra = [c for c in live["checks"] if c["id"] not in {x["id"] for x in cases}]
    open_t = [t for t in tickets if t["status"] in tk.ACTIVE or t["status"] == "resolved"]
    signed = bool(live.get("signoff")) and not failed and not open_t

    lines = [f"# Test sign-off · {name} · {live['version']}", "",
             "| | |", "|---|---|", f"| Tester | {ROLE['qa']} |", f"| Test lead (approver) | {person} |",
             f"| Code under test | {live.get('deployed_version') or code.get('version') or '-'} "
             f"({'deployed by Terra from Dev' + chr(39) + 's packages' if code.get('code_by') == 'terra' else 'deployed by Dev'}, Dev's sanity check "
             f"{'passed' if (code.get('sanity') or {}).get('passed') else 'not passed'}) |",
             f"| Test plan | {'approved by ' + person + ' on ' + plan['approved_at'] if plan.get('approved_at') else 'none: this run predates test plans; the scenarios are the checks Quinn ran'} |",
             f"| Test runs | {len(runs) or 1} |", f"| Region | eu-west-1 |", ""]
    lines += ["## Verdict", ""]
    if final:
        a = final[-1]
        who = "Signed off by Quinn and approved" if live.get("signoff") else "Approved"
        lines.append(f"✅ **{who} by {person} on {_when(a.decided_at)}**" + (f": “{a.comment}”" if a.comment else "") + "."
                     + ("" if live.get("signoff") else " (Quinn's run predates sign-off statements: his summary is below.)"))
    elif signed:
        lines.append(f"✅ **Signed off by Quinn**, waiting for {person}'s approval.")
    else:
        lines.append("⏳ **Not signed off yet**: " + (f"{len(failed)} scenario(s) failing, " if failed else "")
                     + (f"{len(open_t)} ticket(s) still open." if open_t else "testing continues."))
    if live.get("signoff"):
        lines += ["", f"> **Quinn:** {live['signoff']}"]
    lines += ["", "## Summary", "", f"- Scenarios planned: **{len(cases)}**" + (f" ({_by_cat(cases)})" if plan else ""),
              f"- Passed: **{len(passed)}** · Failed: **{len(failed)}** · Not run: **{len(skipped)}**" + (f" · Extra checks: {len(extra)}" if extra else ""),
              f"- Tickets raised: **{len(tickets)}** · closed: {sum(t['status'] == 'closed' for t in tickets)} · still open: {len(open_t)}",
              f"- Real calls in AWS (last run): {live.get('calls', 0)}, as `{live.get('role', '-')}`", "", live.get("summary", ""), ""]
    if plan.get("scope") or plan.get("out_of_scope"):
        lines += ["## Scope", "", plan.get("scope", "")]
        if plan.get("out_of_scope"):
            lines += ["", "**Not in scope:**", *[f"- {x['what']}: {x['why']}" for x in plan["out_of_scope"]]]
        lines.append("")
    outs = dep.get("outputs") or {}
    endpoints = [v for v in _strings(outs) if v.startswith("https://")]
    if endpoints:
        lines += ["## Environment", "", *[f"- `{e}`" for e in endpoints[:6]], ""]
    lines += ["## Results by scenario", "", "| ID | Scenario | Category | Priority | Result | Evidence |", "|---|---|---|---|---|---|"]
    for c in cases:
        r = results.get(c["id"])
        res = "✅ pass" if r and r["passed"] else "❌ fail" if r else "⏭ not run"
        ev = r["evidence"] if r else not_run.get(c["id"], "not run in the last run")
        lines.append(f"| {c['id']} | {_esc(c['title'])} | {c.get('category', '-')} | {c.get('priority', '-')} | {res} | {_esc(ev)[:350]} |")
    for c in extra:
        lines.append(f"| {c['id']} | {_esc(c['title'])} | extra | - | {'✅ pass' if c['passed'] else '❌ fail'} | {_esc(c['evidence'])[:350]} |")
    lines.append("")
    if any(r.get("did") for r in results.values()):  # 10-05: every scenario's flow in plain words, for anyone who reads the sign-off
        lines += ["## What was tested, step by step", ""]
        for c in [*cases, *extra]:
            r = results.get(c["id"])
            if not r:
                continue
            lines += [f"### {'✅' if r['passed'] else '❌'} {c['id']}: {c['title']}", ""]
            if c.get("flow"):
                lines.append(f"- **The flow:** {c['flow']}")
            if c.get("example"):
                lines.append(f"- **Example:** {c['example']}")
            if r.get("did"):
                lines.append(f"- **What Quinn did:** {r['did']}")
            if c.get("expected") or r.get("expected"):
                lines.append(f"- **Expected:** {c.get('expected') or r.get('expected')}")
            lines += [f"- **What came back:** {r['evidence']}", ""]
    lines += ["## Defects (tickets)", ""]
    if tickets:
        lines += ["| Ticket | Title | Severity | Area | Raised by | Fixed by | Found in | Fixed in | Status |", "|---|---|---|---|---|---|---|---|---|"]
        for t in tickets:
            fixer = next((c["author"] for c in reversed(t.get("comments", [])) if c["kind"] == "status" and "→ resolved" in c["text"]), None)
            lines.append(f"| {t['label']} | {_esc(t['title'])} | {t['severity']} | {t['area']} | {tk.name(t['reporter'])} | "
                         f"{tk.name(fixer) if fixer else '-'} | {t.get('version_found') or '-'} | {t.get('version_fixed') or '-'} | {t['status']} |")
        lines.append("")
        for t in tickets:
            lines += [f"### {t['label']}: {t['title']}", ""]
            lines += [f"- {_when(c['created_at'])} · {tk.name(c['author'])}: {c['text'][:300]}" for c in t.get("comments", [])]
            lines.append("")
    else:
        lines += ["No defects were raised: every scenario passed in the runs below.", ""]
    if runs:
        lines += ["## Test runs", "", "| Run | When | Code | Kind | Passed | Failed | Not run | Tickets opened / closed / reopened |", "|---|---|---|---|---|---|---|---|"]
        for r in runs:
            lines.append(f"| {r['run']} | {r['at']} | {r.get('deployed_version') or r.get('version')} | {r['kind']} | {r['passed']} | {r['failed']} | "
                         f"{r.get('not_run', 0)} | {len(r.get('created', []))} / {len(r.get('closed', []))} / {len(r.get('reopened', []))} |")
        lines.append("")
    if not_run or live.get("risks"):
        lines += ["## Not tested, and residual risks", ""]
        lines += [f"- {k}: {v}" for k, v in not_run.items()]
        lines += [f"- {r}" for r in live.get("risks") or []]
        lines.append("")
    crit = plan.get("exit_criteria") or []
    lines += ["## Exit criteria", "", f"- {'✅' if not skipped else '❌'} Every planned scenario executed",
              f"- {'✅' if not failed else '❌'} Every executed scenario passed", f"- {'✅' if not open_t else '❌'} No open tickets"]
    lines += [f"- {c}" for c in crit]
    lines += ["", f"_Written by Orkestra from Quinn's test runs, the tickets and the approvals. The verdict lines quote Quinn and {person}; "
              "everything else is the recorded evidence._", ""]
    store.write(TEST_SIGNOFF, "\n".join(lines))
    return TEST_SIGNOFF


def _by_cat(cases: list[dict]) -> str:
    cats: dict[str, int] = {}
    for c in cases:
        cats[c.get("category", "-")] = cats.get(c.get("category", "-"), 0) + 1
    return ", ".join(f"{n} {k}" for k, n in cats.items())


def _strings(v) -> list[str]:
    if isinstance(v, dict):
        return [s for x in v.values() for s in _strings(x)]
    if isinstance(v, list):
        return [s for x in v for s in _strings(x)]
    return [v] if isinstance(v, str) else []
