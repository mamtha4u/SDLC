"""Terra's drift watch (user, 10-03): "if someone modifies the Lambda code in the console, deletes the Lambda or a layer,
or changes the SQS configuration, Terra must track the state and inform the user". The Terraform state and Dev's
packages are the source of truth; nothing here goes through Dev or Quinn.

tp.drift       compare AWS with the source of truth, with Terra's role and no AI call: a refresh-only plan (settings
               changed or resources deleted outside Terraform) plus every function's CodeSha256 against Dev's package
               (with a file diff when it differs). Something differs → a restore plan (terraform plan, nothing applied)
               and the "drift" gate: Restore, Keep the changes, or Leave it for now. Runs on demand (AWS tab) and from
               the watcher for live, idle projects (settings.drift_check_minutes).
tp.restore     the user chose Restore: re-plan, apply exactly the approved restore (more drift since → check again
               instead), put Dev's package back where the code differs, and refresh the AWS page. Done: no other gate.
tp.drift_keep  the user chose Keep: the console changes become a change request for Orion and Terra (settings, deleted
               resources) and/or a ticket for Dev (code), so the source of truth catches up with AWS.
"""
from __future__ import annotations

import asyncio
import calendar
import difflib
import hashlib
import io
import json
import logging
import time
import urllib.request
import zipfile

from sqlalchemy import select

from app.agents import de
from app.agents.buildkit import files_under
from app.db.base import SessionLocal
from app.db.models import Approval, Job, Project
from app.orchestrator import crewchat, flow
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import aws_access, inventory
from app.services.storage import ProjectStore
from app.tools import terraform

log = logging.getLogger(__name__)
DRIFT = "drift"  # deploy/drift.json: the last check, what was raised, what was restored
RESTORE_PLAN = "restore.tfplan"
REPORT = "reports/drift.md"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load(pid: str) -> dict | None:
    return aws_access.load(pid, DRIFT)


def _expected_code(pid: str, infra: dict[str, str]) -> dict[str, dict]:
    """function name → what should run there: Dev's package Terra deployed (older projects: Dev's last deploy)."""
    if terraform.manages_code(infra):
        root = aws_access.deploy_dir(pid) / "work" / terraform.CODE_PACKAGES
        return {r["function_name"]: {"sha": r["applied"], "version": r.get("applied_version") or r.get("version"), "key": k,
                                     "zip": root / f"{k}.zip", "source_dir": r.get("source_dir")}
                for k, r in de.load_packages(pid)["code"].items() if r.get("applied") and r.get("function_name")}
    code = aws_access.load(pid, "code") or {}
    return {n: {"sha": f.get("sha"), "version": f.get("code_version"), "key": f.get("key"), "zip": None, "source_dir": f.get("source_dir")}
            for n, f in (code.get("functions") or {}).items() if f.get("sha")}


def _package(pid: str, exp: dict) -> bytes | None:
    """The exact zip that should run: Dev's handed-over package, or (older projects) his last deploy rebuilt, only if
    it's byte-identical to what he deployed."""
    if exp.get("zip") and exp["zip"].exists():
        return exp["zip"].read_bytes()
    src = aws_access.deploy_dir(pid) / "code_work" / str(exp.get("source_dir") or "")
    if exp.get("source_dir") and src.is_dir():
        data = terraform.zip_folder(src)
        return data if de._sha(data) == exp["sha"] else None
    return None


def _files(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}


def _diff(mine: bytes | None, live: bytes, version: str | None) -> list[dict]:
    """[{file, change, diff}]: Dev's package vs the code in AWS, text files as a unified diff."""
    if mine is None:
        return [{"file": "", "change": "unknown", "diff": "Dev's package for this function isn't on the host, so the files can't be compared."}]
    a, b = _files(mine), _files(live)
    out = []
    for name in sorted(set(a) | set(b)):
        if a.get(name) == b.get(name):
            continue
        change = "added in AWS" if name not in a else "deleted in AWS" if name not in b else "changed in AWS"
        text = ""
        try:
            lines = list(difflib.unified_diff((a.get(name) or b"").decode().splitlines(), (b.get(name) or b"").decode().splitlines(),
                                              f"Dev's {version or 'package'}/{name}", f"AWS now/{name}", lineterm="", n=2))
            text = "\n".join(lines[:160]) + ("\n…" if len(lines) > 160 else "")
        except UnicodeDecodeError:
            text = "(binary file)"
        out.append({"file": name, "change": change, "diff": text})
    return out[:20]


def check_code(pid: str, creds: dict, infra: dict[str, str], diff: bool = True) -> list[dict]:
    """Functions whose code in AWS isn't the source of truth (CodeSha256 ≠ Dev's package). Blocking; Terra's role."""
    from botocore.exceptions import ClientError

    lam = aws_access.session_for(creds).client("lambda")
    out = []
    for fname, exp in _expected_code(pid, infra).items():
        try:
            cfg = lam.get_function_configuration(FunctionName=fname)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                continue  # deleted: the refresh reports it, and the restore plan recreates it
            raise
        if cfg["CodeSha256"] == exp["sha"]:
            continue
        item = {"function_name": fname, "key": exp["key"], "expected_version": exp["version"], "expected_sha": exp["sha"],
                "live_sha": cfg["CodeSha256"], "last_modified": cfg.get("LastModified"), "files": []}
        if diff:
            try:
                with urllib.request.urlopen(lam.get_function(FunctionName=fname)["Code"]["Location"], timeout=60) as r:  # noqa: S310 (AWS's presigned URL)
                    item["files"] = _diff(_package(pid, exp), r.read(), exp["version"])
            except Exception as exc:  # noqa: BLE001 (the comparison is a bonus; the hash already proves the drift)
                item["files"] = [{"file": "", "change": "unknown", "diff": f"Couldn't download the code from AWS to compare: {str(exc)[:200]}"}]
        out.append(item)
    return out


def fingerprint(rep: dict) -> str:
    key = {"settings": sorted((s["address"], tuple((f["key"], f["aws"]) for f in s["fields"])) for s in rep["settings"]),
           "deleted": sorted(d["address"] for d in rep["deleted"]), "code": sorted((c["function_name"], c["live_sha"]) for c in rep["code"])}
    return hashlib.sha1(json.dumps(key, default=str).encode()).hexdigest()[:16]


def summary_line(rep: dict) -> str:
    parts = ([f"{len(rep['settings'])} resource(s) with changed settings"] if rep["settings"] else []) \
        + ([f"{len(rep['deleted'])} deleted"] if rep["deleted"] else []) \
        + ([f"code changed in {', '.join(c['function_name'] for c in rep['code'])}"] if rep["code"] else [])
    return ", ".join(parts) or "nothing"


def markdown(rep: dict) -> str:
    lines = [f"# Drift check · {rep['checked_at']}", "",
             "Terra compared what's in AWS with the source of truth: the Terraform state and the code packages Dev handed over "
             "(older projects: Dev's last deploy). " + ("**Everything matches.**" if rep["clean"] else f"**Changed outside Terraform: {summary_line(rep)}.**"), ""]
    if rep["settings"]:
        lines += ["## Settings changed in AWS", "", "| Resource | Setting | Terraform (source of truth) | Now in AWS |", "|---|---|---|---|"]
        for s in rep["settings"]:
            for f in s["fields"]:
                lines.append(f"| `{s['address']}` {s.get('name') or ''} | {f['key']} | `{f['terraform']}` | `{f['aws']}` |")
        lines.append("")
    if rep["deleted"]:
        lines += ["## Deleted in AWS", "", *[f"- `{d['address']}` {d.get('name') or ''}" for d in rep["deleted"]], ""]
    for c in rep["code"]:
        lines += [f"## Code changed in AWS: {c['function_name']}", "",
                  f"It should run Dev's {c.get('expected_version') or 'package'} (CodeSha256 `{c['expected_sha']}`); AWS has "
                  f"`{c['live_sha']}`, last modified {c.get('last_modified') or '?'}.", ""]
        for f in c.get("files") or []:
            lines += [f"**{f['file'] or 'files'}** ({f['change']})", "", "```diff", f["diff"], "```", ""]
    if rep.get("restore"):
        r = rep["restore"]
        lines += ["## What Restore does", "", f"`terraform apply` of this plan: **{r['counts']['create']} to add, "
                  f"{r['counts']['update'] + r['counts']['replace']} to change, {r['counts']['delete']} to destroy**"
                  + (", then Dev's package goes back into the function(s) whose code changed" if rep["code"] else "") + ".", "",
                  *[f"- {c['action']} `{c['address']}` {c['name']}" + (f" ({', '.join(c['fields'])})" if c.get("fields") else "") for c in r["changes"]], ""]
    lines += [f"Unchanged: {rep['unchanged']} of {rep['total']} resources match the state.", ""]
    if rep.get("restored"):
        x = rep["restored"]
        lines += ["## Restored", "", f"{x['at']}: {x['line']}", ""]
    return "\n".join(lines) + "\n"


async def _workspace(pid: str, creds: dict):
    store = ProjectStore(pid)
    infra = files_under(store, ("infra/",))
    work = terraform.prepare(aws_access.deploy_dir(pid) / "work",
                             terraform.package_vars(infra, aws_access.deploy_dir(pid) / "work" / terraform.PACKAGES_DIR))
    await terraform.init(work, aws_access.state_bucket(pid), f"{pid}/terraform.tfstate", creds)
    return work, infra


async def _busy(pid: str, exclude_job: str | None = None) -> str | None:
    """Why a drift check or restore must wait: another job, a change in progress, a plan waiting for the user."""
    dep = aws_access.load(pid, "deploy") or {}
    if dep.get("intent"):
        return "Terra is in the middle of a change (a plan is being prepared or waits for your approval)"
    async with SessionLocal() as db:
        job = (await db.execute(select(Job).where(Job.project_id == pid, Job.status.in_(["queued", "running"]),
                                                  *([Job.id != exclude_job] if exclude_job else [])))).scalars().first()
        if job:
            return f"{job.kind} is running"
        gate = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.status == "pending",
                                                        Approval.stage.in_(["infra", "deploy", "infra_check", "change_review"])))).scalars().first()
        if gate:
            return f"“{gate.title}” waits for your approval"
    return None


@handler("tp.drift", resumable=False)
async def check(ctx: JobContext) -> None:
    pid = ctx.project_id
    auto = bool(ctx.payload.get("auto"))
    acc, dep = aws_access.load(pid) or {}, aws_access.load(pid, "deploy") or {}
    if acc.get("status") != "active" or dep.get("status") not in ("deployed", "partial"):
        return
    why = await _busy(pid, ctx.job_id)
    if why:
        if not auto:
            await crewchat.say(pid, "tp", "user", f"I'll check for drift once this is done: {why}.", "update")
        return
    if not auto:
        await ctx.set_agent("tp", "working", "🔎 Checking AWS against the Terraform state and Dev's packages")
    try:
        creds = await asyncio.to_thread(aws_access.assume, pid, "tp")
        work, infra = await _workspace(pid, creds)
        details: dict = {}
        rows, drift = await terraform.live(work, creds, ignore=terraform.CODE_FIELDS if terraform.manages_layers(infra) else None, details=details)
        code = await asyncio.to_thread(check_code, pid, creds, infra)
        restore = None
        if details or code:
            p = await terraform.plan(work, creds, out_file=RESTORE_PLAN)
            restore = {"counts": p["counts"], "changes": p["changes"]}
            if details and not code and not p["changes"]:
                # nothing to put back: only Terraform's own record was behind (10-05: a role's copy of its inline policy
                # after Terra's fix to that policy was flagged as drift with a restore of +0 ~0 −0). Not a change in AWS:
                # bring the record up to date (AWS untouched) and report no drift.
                await terraform.sync_state(work, creds)
                aws_access.audit(pid, "tp", "terraform apply -refresh-only", f"state caught up: {', '.join(list(details)[:4])}")
                details.clear()
                drift, restore = {}, None
        schema = await terraform.schema(work, {r["type"] for r in rows}, creds)
        aws_access.audit(pid, "tp", "drift check", f"{len(rows)} resources read; {len(details)} changed, {len(code)} function(s) with other code")
    except Exception as exc:  # noqa: BLE001
        prev = load(pid) or {}
        aws_access.save(pid, {**prev, "error": str(exc)[:600], "error_at": _now()}, DRIFT)
        await ctx.emit("drift.checked", f"Couldn't check for drift: {str(exc)[:200]}", agent="tp", ok=False)
        if not auto:
            await ctx.set_agent("tp", "failed", f"Drift check: {str(exc)[:200]}")
            raise
        log.warning("drift check for %s failed: %s", pid, exc)
        return
    inv = inventory.build(pid, rows, schema, (dep.get("plan") or {}).get("config_keys") or {}, aws_access.load(pid, "code"), dep.get("version"), drift)
    inv["applied_at"] = (inventory.load(pid) or {}).get("applied_at") or dep.get("applied_at")
    inv["refreshed_at"] = _now()
    inventory.save(pid, inv)
    rep = {"checked_at": _now(), "auto": auto, "mode": "terra" if terraform.manages_code(infra) else "dev",
           "settings": [{"address": a, **d} for a, d in details.items() if not d["deleted"]],
           "deleted": [{"address": a, "type": d["type"], "name": d["name"]} for a, d in details.items() if d["deleted"]],
           "code": code, "restore": restore, "total": len(rows), "unchanged": len([r for r in rows if r["address"] not in details])}
    rep["clean"] = not (rep["settings"] or rep["deleted"] or rep["code"])
    rep["fingerprint"] = fingerprint(rep)
    prev = load(pid) or {}
    history = ([{k: prev.get(k) for k in ("checked_at", "clean", "fingerprint", "status")}] if prev.get("checked_at") else []) + (prev.get("history") or [])
    rep["history"] = history[:12]
    rep["raised"] = prev.get("raised")
    store = ProjectStore(pid)
    if rep["clean"]:
        rep["status"] = "clean"
        aws_access.save(pid, rep, DRIFT)
        await _close_gate(pid, "AWS matches the Terraform state again")
        await ctx.emit("drift.checked", "AWS matches the Terraform state and Dev's packages", agent="tp", ok=True, clean=True, manual=not auto)
        if not auto:
            await crewchat.say(pid, "tp", "user", f"🔎 Checked AWS against the source of truth: all {rep['total']} resources and every "
                               "function's code match the Terraform state and Dev's packages.", "update")
            await ctx.set_agent("tp", "done", "Drift check: AWS matches the Terraform state")
        return
    store.write(REPORT, markdown(rep))
    new = rep["fingerprint"] != prev.get("raised")
    pending = await _pending_gate(pid)
    rep["status"] = "found"
    if new or not pending:
        rep["raised"] = rep["fingerprint"]
        aws_access.save(pid, rep, DRIFT)
        line = summary_line(rep)
        await crewchat.say(pid, "tp", "user", f"⚠️ Changed in AWS outside Terraform: {line}. The source of truth is my Terraform state "
                           "and Dev's packages. Restore puts AWS back exactly as it was (only those changes, no other step); or keep "
                           "the changes as a change request.", "issue", files=[REPORT])
        await ctx.set_agent("tp", "needs_approval", f"Drift found: {line}")
        r = rep["restore"]["counts"] if rep.get("restore") else {"create": 0, "update": 0, "replace": 0, "delete": 0}
        await flow.request_approval(
            pid, "drift", f"Changed in AWS outside Terraform: {line}",
            f"Terra compared AWS with the Terraform state and Dev's packages{' (automatic check)' if auto else ''}. Restore applies "
            f"{r['create']} to add, {r['update'] + r['replace']} to change, {r['delete']} to destroy"
            + (" and puts Dev's code back" if rep["code"] else "") + ": nothing else, and no Dev or Quinn steps. Keep the changes: "
            "they become a change request (Orion and Terra) or a ticket for Dev (code).", [REPORT])
    else:
        aws_access.save(pid, rep, DRIFT)
    await ctx.emit("drift.checked", f"Changed outside Terraform: {summary_line(rep)}", agent="tp", ok=True, clean=False)


async def _pending_gate(pid: str) -> Approval | None:
    async with SessionLocal() as db:
        return (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.stage == "drift",
                                                        Approval.status == "pending"))).scalars().first()


async def _close_gate(pid: str, why: str) -> None:
    async with SessionLocal() as db:
        gates = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.stage == "drift",
                                                         Approval.status == "pending"))).scalars().all()
        for a in gates:
            a.status, a.comment = "superseded", why
        if gates:
            await _settle(db, pid)
        await db.commit()


async def _settle(db, pid: str) -> None:
    """The project's status after a drift episode: done if its testing was signed off, else waiting for the next step."""
    p = await db.get(Project, pid)
    final = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.stage == "live",
                                                     Approval.status == "approved"))).scalars().first()
    p.status = "completed" if final else "waiting"


@handler("tp.restore", resumable=False)
async def restore(ctx: JobContext) -> None:
    pid = ctx.project_id
    rep = load(pid) or {}
    approved = {c["address"] for c in (rep.get("restore") or {}).get("changes", [])}
    await ctx.set_agent("tp", "working", "♻️ Restoring AWS from the Terraform state and Dev's packages")
    await ctx.set_project(status="running", last_activity="Terra is restoring AWS from the Terraform state")
    await crewchat.say(pid, "tp", "cto", "Restoring what was changed outside Terraform, as the user approved: exactly the restore "
                       "plan, nothing else.", "ack")
    try:
        creds = await asyncio.to_thread(aws_access.assume, pid, "tp")
        work, infra = await _workspace(pid, creds)
        p = await terraform.plan(work, creds, out_file=RESTORE_PLAN)
        extra = [c for c in p["changes"] if c["address"] not in approved]
        if extra:  # AWS changed again since the user approved: never apply more than they saw
            (work / "infra" / RESTORE_PLAN).unlink(missing_ok=True)
            await crewchat.say(pid, "tp", "user", "AWS changed again since you approved the restore ("
                               + ", ".join(f"`{c['address']}`" for c in extra[:5]) + "). I didn't apply anything; checking again so "
                               "you see the full picture.", "issue")
            await ctx.set_agent("tp", "working", "🔎 AWS changed again: checking for drift")
            await runner_mod.runner.enqueue("tp.drift", project_id=pid)
            return
        if p["changes"]:
            await ctx.set_agent("tp", "working", f"🚀 terraform apply: {tp_line(p['counts'])}")
            await terraform.apply(work, creds, RESTORE_PLAN)
            aws_access.audit(pid, "tp", "terraform apply (restore)", tp_line(p["counts"]))
        (work / "infra" / RESTORE_PLAN).unlink(missing_ok=True)
        pushed = await asyncio.to_thread(_put_code_back, pid, creds, infra)
        rows = await terraform.state(work, creds)
        schema = await terraform.schema(work, {r["type"] for r in rows}, creds)
        left = await asyncio.to_thread(check_code, pid, creds, infra, False)
    except Exception as exc:
        await ctx.set_agent("tp", "failed", f"Restore failed: {str(exc)[:200]}")
        await ctx.set_project(status="failed", last_activity=f"Terra's restore failed: {str(exc)[:160]}")
        await crewchat.say(pid, "tp", "cto", f"The restore failed: {str(exc)[:600]}", "issue")
        raise
    dep = aws_access.load(pid, "deploy") or {}
    inv = inventory.build(pid, rows, schema, (dep.get("plan") or {}).get("config_keys") or {}, aws_access.load(pid, "code"), dep.get("version"))
    inv["applied_at"], inv["refreshed_at"] = _now(), _now()
    inventory.save(pid, inv)
    line = (tp_line(p["counts"]) if p["changes"] else "no setting to change") + (f"; Dev's code put back in {', '.join(pushed)}" if pushed else "")
    rep = {**rep, "status": "restored" if not left else "partly", "restored": {"at": _now(), "line": line, "plan": p["counts"], "code": pushed},
           "clean": not left, "code": left}
    aws_access.save(pid, rep, DRIFT)
    ProjectStore(pid).write(REPORT, markdown(rep))
    ProjectStore(pid).append_changelog(ProjectStore(pid).manifest()["current_version"], [f"Restored AWS from the Terraform state (drift): {line}"])
    async with SessionLocal() as db:
        await _settle(db, pid)
        p_row = await db.get(Project, pid)
        p_row.last_activity = f"Restored from the Terraform state: {line}"
        await db.commit()
    await crewchat.say(pid, "tp", "user", f"♻️ Restored: {line}. AWS matches the Terraform state and Dev's packages again"
                       + ("." if not left else f", except the code of {', '.join(c['function_name'] for c in left)} (see the report)."),
                       "update", files=[REPORT])
    await ctx.set_agent("tp", "done", f"Restored from the Terraform state: {line}")
    await ctx.emit("drift.checked", f"Restored: {line}", agent="tp", ok=True, clean=not left)
    await ctx.emit("build.updated", "Terra restored AWS from the Terraform state", agent="tp")


def tp_line(c: dict) -> str:
    return f"{c['create']} to add, {c['update'] + c['replace']} to change, {c['delete']} to destroy"


def _put_code_back(pid: str, creds: dict, infra: dict[str, str]) -> list[str]:
    """Upload the source of truth where a function's code still differs after the apply (Terra's role). Blocking."""
    lam = aws_access.session_for(creds).client("lambda")
    exp_all = _expected_code(pid, infra)
    done = []
    for item in check_code(pid, creds, infra, diff=False):
        data = _package(pid, exp_all[item["function_name"]])
        if data is None:
            continue
        lam.update_function_code(FunctionName=item["function_name"], ZipFile=data)
        lam.get_waiter("function_updated").wait(FunctionName=item["function_name"])
        aws_access.audit(pid, "tp", "lambda:UpdateFunctionCode", item["function_name"], True,
                         f"restore: Dev's {item.get('expected_version') or 'package'} ({len(data) // 1024} KB)")
        done.append(item["function_name"])
    return done


@handler("tp.drift_keep", resumable=False)
async def keep(ctx: JobContext) -> None:
    """The user keeps the console changes: the source of truth must catch up (a change request, a ticket for Dev)."""
    from app.orchestrator import changes
    from app.services import tickets as tk

    pid = ctx.project_id
    rep = load(pid) or {}
    note = (ctx.payload.get("feedback") or "").strip()
    rows = [f"- `{s['address']}` ({s.get('name') or s.get('type')}): keep `{f['key']}` = `{f['aws']}` (Terraform has `{f['terraform']}`)"
            for s in rep.get("settings") or [] for f in s["fields"]]
    rows += [f"- `{d['address']}` ({d.get('name') or d.get('type')}) was deleted in AWS on purpose: remove it from the Terraform"
             for d in rep.get("deleted") or []]
    made = []
    if rows:
        cr = await changes.create(pid, "Keep the changes made in AWS outside Terraform (found by Terra's drift check "
                                  f"{rep.get('checked_at')}):\n" + "\n".join(rows) + (f"\n\nThe user: {note}" if note else ""),
                                  [REPORT], source="drift", route="infra")
        made.append(changes.label(cr))
    for c in rep.get("code") or []:
        diffs = "\n\n".join(f"{f['file']} ({f['change']}):\n{f['diff']}" for f in c.get("files") or [])
        t = await tk.create(pid, title=f"Keep the console change to {c['function_name']}'s code", area="code", severity="minor",
                            assignee="de", reporter="tp",
                            description=f"Someone changed the code of {c['function_name']} in AWS (last modified {c.get('last_modified')}); the "
                                        f"user wants to keep it. Bring the change into src/ (with a test), so your package matches.\n\n"
                                        + (f"The user: {note}\n\n" if note else "") + f"What differs from your {c.get('expected_version')}:\n\n{diffs[:6000]}",
                            expected="Dev's package has the change; Terra's next deploy keeps it", actual="The change exists only in AWS")
        made.append(tk.label(t))
        if not rows:
            await runner_mod.runner.enqueue("cto.tickets", project_id=pid)
    aws_access.save(pid, {**rep, "status": "keeping", "kept": {"at": _now(), "as": made, "note": note}}, DRIFT)
    await crewchat.say(pid, "tp", "user", f"Understood: the changes stay and the source of truth catches up ({', '.join(made) or 'nothing to do'}).", "update")
    await ctx.set_agent("tp", "done", f"Keeping the console changes: {', '.join(made)}")


async def watch() -> None:
    """Every settings.drift_check_minutes: a drift check for every live, idle project (no AI call, Terra's role)."""
    from app.core.config import get_settings

    every = get_settings().drift_check_minutes
    if every <= 0:
        return
    await asyncio.sleep(120)  # let the app settle after a restart
    while True:
        try:
            async with SessionLocal() as db:
                projects = (await db.execute(select(Project).where(Project.archived.is_(False), Project.paused.is_(False)))).scalars().all()
                # the project's Settings tab can switch the automatic check off ("Check for drift" still works)
                ids = [p.id for p in projects if (p.settings or {}).get("drift_watch") is not False]
            for pid in ids:
                acc, dep = aws_access.load(pid) or {}, aws_access.load(pid, "deploy") or {}
                if acc.get("status") != "active" or dep.get("status") != "deployed" or not terraform.available():
                    continue
                last = (load(pid) or {}).get("checked_at")
                if last and time.time() - calendar.timegm(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ")) < every * 60 - 30:
                    continue
                if await _busy(pid) or await _pending_gate(pid):
                    continue
                await runner_mod.runner.enqueue("tp.drift", project_id=pid, auto=True)
                await asyncio.sleep(20)  # one at a time: each runs a terraform refresh
        except Exception as exc:  # noqa: BLE001 (the watch never stops)
            log.warning("drift watch: %s", exc)
        await asyncio.sleep(max(60, every * 60 // 4))
