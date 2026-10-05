"""Terra — Platform Engineer.

Infrastructure first, like the team works: the infrastructure exists in AWS (with placeholder code) before Dev writes a
line, the user checks it in the console. Terra deploys everything (user, 10-03): Dev hands over his packages (each
function's code, each layer), Terra's next plan swaps them in for the placeholder, so code, layers and infrastructure
share one Terraform state (and a console edit shows up as drift).

tp.iac      from Archie's approved LLD (or a change request, or tickets) writes the Terraform under infra/. The platform
            enforces the sandbox rules (tools/terraform.lint), runs terraform fmt/init/validate with no AWS
            credentials, and Orion checks he can grant the crew's AWS access for it; any failure goes back to Terra.
            First build (or new access needed) → "infra" gate: the Terraform and Orion's access plan; approving it
            creates the roles and runs the plan. Infrastructure already live → straight to the plan (tp.deploy).
Deploying, always with Terra's own short-lived role:
tp.deploy   terraform init (S3 state) + plan → "deploy" gate: everything is ready, only apply is left (user, 10-02:
            always ask before apply, also for the first creation). Nothing to change → the state is just recorded.
tp.apply    applies the plan, records the outputs and the AWS inventory (the AWS page), then → "infra_check" gate: the
            user checks the real resources. A ticket fix instead resolves its tickets back to Quinn; Dev's packages
            (intent "packages") go back to Dev, who checks them live and tests the whole flow.
tp.destroy  tear down: terraform destroy, Dev's layer versions, then Orion removes the roles and the state bucket
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import replace

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.agents.buildkit import CHANGED_FILES, DELETE, WorkingSet, context, files_under
from app.agents.intake import load_intake
from app.orchestrator import crewchat, flow
from app.orchestrator import runner as runner_mod
from app.orchestrator.runner import JobContext, handler
from app.services import aws_access
from app.services.storage import ProjectStore, StorageError
from app.tools import terraform

PREVIEW = "infra/plan_preview.json"
# Terra writes the Terraform a few files at a time, then submits. One call carrying every file (13 files, ~30k tokens)
# proved fragile: on 2026-10-02 the model dropped `files` from a huge submission three times and ran out of turns.
WRITE_FILES = Tool(
    name="write_files",
    description="Write or replace Terraform files under infra/ (full content). Send 2-5 files per call; files you wrote "
                "earlier are kept. Use `delete` to remove a file. Then call submit_infra.",
    schema=obj({"files": CHANGED_FILES, "delete": DELETE}, required=["files"]),  # delete is optional
)
SUBMIT = Tool(
    name="submit_infra",
    terminal=True,
    description="Submit the infrastructure: the Terraform is the files you wrote with write_files. The platform lints and runs "
                "terraform validate on them; fix the files with write_files and submit again if rejected.",
    schema=obj({
        "summary": {"type": "string", "description": "2-3 sentences: what this builds"},
        "resources": {"type": "array", "description": "Every resource, for the preview screen", "items": obj({
            "address": {"type": "string", "description": "Terraform address, e.g. aws_lambda_function.transform"},
            "type": {"type": "string"}, "name": {"type": "string", "description": "final AWS name (prefix resolved)"},
            "settings": {"type": "string", "description": "key settings in one line"},
            "tags": {"type": "string"}, "monthly_usd": {"type": "number", "description": "rough monthly cost at the stated volume"}},
            required=["address", "type", "name", "settings"])},
        "notes": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "string", "description": "What changed versus the previous version (if revising), else empty"},
    }),
)


def load_preview(project_id: str) -> dict | None:
    try:
        return json.loads(ProjectStore(project_id).read(PREVIEW))
    except (StorageError, FileNotFoundError, ValueError):
        return None


def deployed(project_id: str) -> bool:
    return (aws_access.load(project_id, "deploy") or {}).get("status") in ("deployed", "partial")


@handler("tp.iac", resumable=False)
async def iac(ctx: JobContext) -> None:
    from app.agents.buildkit import new_minor_version
    from app.agents.cto import research_tools
    from app.services import tickets as tk

    pid = ctx.project_id
    feedback = (ctx.payload.get("feedback") or "").strip()
    ticket_ids = list(ctx.payload.get("tickets") or [])
    mine = await tk.rows_for(pid, ticket_ids)
    if any(t["status"] != "in_progress" for t in mine):  # a fix goes into a new minor version (not again on a retry)
        await new_minor_version(pid, "Fixing " + ", ".join(t["label"] for t in mine))
    for t in mine:
        if t["status"] != "in_progress":
            await tk.change(t["id"], "tp", "On it: fixing it in the Terraform.", status="in_progress")
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    intake = await load_intake(pid)
    previous = load_preview(pid)
    trail: list[dict] = []
    box: dict = {}
    current = {p: c for p, c in files_under(store, ("infra/",)).items() if p != PREVIEW}
    work = WorkingSet(current, ("infra/",), "Terra")  # a revision starts from the current Terraform

    async def write_files(a: dict):
        from app.services.naming import USER_NAMES

        if any(f.get("path", "").endswith(USER_NAMES.split("/")[-1]) for f in a.get("files") or []) or \
                any(str(p).endswith(USER_NAMES.split("/")[-1]) for p in a.get("delete") or []):
            raise AgentError(f"{USER_NAMES} holds the user's chosen names: never write or delete it. Change names.tf defaults instead.")
        platform = terraform.PLATFORM_FILES
        if any(f.get("path", "").strip("./") in platform for f in a.get("files") or []) or any(str(p).strip("./") in platform for p in a.get("delete") or []):
            raise AgentError(f"{terraform.PACKAGES_TF_PATH} is the platform's (it deploys Dev's code and layer packages): don't write or "
                             "delete it. In your own files: each function takes lookup(var.code_packages, \"<key>\", <placeholder zip>), "
                             "define local.layer_names and attach layers with local.layer_arns.")
        before = set(work.files)
        files = work.apply(a)
        written = [f["path"] for f in a.get("files") or []]
        await ctx.set_agent("tp", "working", f"✍️ Writing {', '.join(p.removeprefix('infra/') for p in written[:4])}")
        return (f"Saved {len(written)} file(s); removed {len(set(before) - set(files))}. Your files now:\n" + work.listing()
                + "\nWrite the rest, then call submit_infra.")

    async def verify(a: dict):
        files = dict(work.files)
        if not files:
            raise AgentError("You haven't written any files yet: write them with write_files (2-5 per call), then submit.")
        files.pop(terraform.LAYERS_TF_PATH, None)  # 10-02's layers-only file: packages.tf replaces it
        files[terraform.PACKAGES_TF_PATH] = terraform.PACKAGES_TF  # the platform's: deploys Dev's packages (user, 10-03)
        problems = terraform.lint(files, pid)
        if problems:
            raise AgentError("Sandbox rules: " + "; ".join(problems[:20]))
        await ctx.set_agent("tp", "working", "🔍 terraform fmt / init / validate (no AWS credentials)")
        v = await terraform.validate(files)
        if not v["ok"]:
            raise AgentError("terraform validate failed: " + "; ".join(v["diagnostics"][:15]))
        try:  # Orion must be able to grant the crew's access for exactly this Terraform
            acc = aws_access.draft(pid, infra=v["formatted"], resources=a.get("resources"))
        except aws_access.AccessError as exc:
            raise AgentError(f"Orion can't draft the AWS access: {exc}") from exc
        if acc["problem"]:
            raise AgentError(f"Orion can't grant the crew's AWS access for this Terraform: {acc['problem']}")
        box["files"], box["validate"] = v["formatted"], {k: val for k, val in v.items() if k != "formatted"}

    plan_step = next((s for s in (intake.plan or {}).get("steps", []) if s["agent"] == "tp"), None)
    ask = (f"# Project id: {pid} (use it in the project_id tag and the state bucket name)\n"
           + context(store, intake.requirement_md, mapping=False, own="tp")
           + (f"\n\n# Orion's plan for you\n{plan_step['task']}" if plan_step else ""))
    if current:
        ask += ("\n\n---\n# Your current Terraform (already in your files)\nChange only what the LLD, the feedback or the tickets "
                "need, with write_files; keep everything else as it is, and describe the change in `changes`.\n"
                + "\n\n".join(f"## {p}\n```hcl\n{c}\n```" for p, c in current.items()))
    if deployed(pid):
        ask += ("\n\n# Already live in AWS\nThis infrastructure exists in AWS and Dev's code runs in it. Your change is applied as a "
                "plan the user approves: avoid renames or settings that force a replacement unless asked. Keep every function's "
                "filename/source_code_hash lookups on var.code_packages / local.code_hashes, so Dev's code stays deployed.")
    if mine:
        ask += ("\n\n# Tickets assigned to you (fix each one; `changes` becomes your comment on them)\n" + tk.prompt_block(mine))
    if feedback and ctx.payload.get("unblock"):  # agents/unblock.py: a step failed, Archie diagnosed it, Orion decided
        ask += f"\n\n# Archie and Orion's fix for the step that failed (apply it exactly, describe it in `changes`)\n{feedback}"
    elif feedback:
        ask += f"\n\n# The user's feedback or change request (address every point, describe it in `changes`)\n{feedback}"
    ask += "\n\nWrite the Terraform with write_files (2-5 files per call), then call submit_infra."

    await ctx.set_agent("tp", "working", f"🎫 Fixing {', '.join(t['label'] for t in mine)}" if mine else
                        "Revising the infrastructure" if feedback else f"🏗 Reading Archie's LLD ({version})")
    await ctx.set_project(status="running", last_activity="Terra is writing the infrastructure code")
    await crewchat.say(pid, "tp", "qa" if mine else "cto",
                       f"On {', '.join(t['label'] for t in mine)}: fixing the infrastructure in {version}." if mine else
                       f"Changing the Terraform for {version} as asked; you'll see the exact plan before anything changes in AWS." if feedback else
                       f"Writing Terraform for {version} from Archie's LLD. Every name gets the orkestra- prefix and the created_by/"
                       "project_id tags; functions start with placeholder code, and I swap in Dev's packages once he hands them over.", "ack")
    try:
        res = await run_loop(project_id=pid, agent="tp",
                             system=[{"type": "text", "text": llm.prompt("tp.md") + "\n\n" + llm.prompt("org_context.md"),
                                      "cache_control": {"type": "ephemeral"}}],
                             messages=[{"role": "user", "content": ask}],
                             tools=[*research_tools(ctx, trail, agent="tp"), replace(WRITE_FILES, handler=write_files),
                                    replace(SUBMIT, handler=verify)],
                             max_turns=26, purpose="infra-revise" if feedback or current else "infra")
        data = res.terminal.get("submit_infra")
        if not data or "files" not in box:
            raise AgentError("Terra finished without submitting validated Terraform.")
        v = box["validate"]
        stale = [p for p in store.replace_files(("infra/",), {**box["files"], PREVIEW: "{}"}) if p != PREVIEW]
        preview = {"summary": data["summary"], "resources": data["resources"], "notes": data["notes"], "changes": data["changes"],
                   "files": sorted(box["files"]), "validate": v, "version": version, "research": trail}
        store.write(PREVIEW, json.dumps(preview, indent=2, ensure_ascii=False))
        store.write("reports/infra_validate.md", "# Terraform validation\n\n"
                    + ("terraform fmt, init (-backend=false) and validate passed, with no AWS credentials.\n" if v.get("available")
                       else "terraform isn't installed on this host: sandbox static checks only.\n")
                    + "".join(f"\n- {d}" for d in v.get("diagnostics", [])))
        store.append_changelog(version, [f"Infrastructure {'revised' if previous else 'written'} by Terra: {len(box['files'])} files, "
                                         f"{len(data['resources'])} resources" + (f"; removed {', '.join(stale)}" if stale else "")])
        cost = sum(r.get("monthly_usd") or 0 for r in data["resources"])
        line = f"{len(data['resources'])} resources · {len(box['files'])} files · validated · ~${cost:.2f}/month"
        await ctx.emit("build.updated", f"Terra {'revised' if previous else 'wrote'} the infrastructure: {line}", agent="tp")
        await crewchat.say(pid, "tp", "cto", f"Terraform ready for {version}: {line}. {data['summary']}"
                           + (f"\nChanges: {data['changes']}" if data.get("changes") else ""), "handoff", files=["infra/README.md", PREVIEW])
        await handover(ctx, data, line, mine, unblock=bool(ctx.payload.get("unblock")))
    except Exception as exc:
        from app.agents import unblock

        blocked = isinstance(exc, llm.BudgetExceeded)
        await ctx.set_agent("tp", "blocked" if blocked else "failed", str(exc)[:240])
        await ctx.set_project(status="waiting" if blocked else "failed", last_activity=f"Terra: {str(exc)[:200]}")
        await ctx.emit("build.updated", f"Terra hit a problem: {str(exc)[:200]}", agent="tp")
        await crewchat.say(pid, "tp", "cto", f"I'm stuck: {str(exc)[:300]}", "issue")
        if not blocked:
            await unblock.escalate(ctx, "tp", "tp.iac", "Writing validated Terraform failed", exc)
        raise


async def handover(ctx: JobContext, data: dict, line: str, tickets: list[dict], unblock: bool = False) -> None:
    """After validated Terraform: Orion drafts the crew's access. Infrastructure not in AWS yet (or new access needed)
    → the "infra" gate; approving it creates the roles and the infrastructure. Already live with the same access → the
    plan right away (it has its own gate whenever live resources change). `unblock`: Archie and Orion's fix for a failed
    plan/apply, after the user already approved the infrastructure: straight to the plan (which the user approves)."""
    pid = ctx.project_id
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    plan = aws_access.draft(pid)
    current = aws_access.load(pid)
    same = bool(current and current.get("status") == "active" and aws_access.same_access(current, plan))
    live = deployed(pid)
    reason = "tickets" if tickets else "change" if live else "build"
    state = aws_access.load(pid, "deploy") or {}
    from app.agents import unblock as unblock_mod

    resume = unblock_mod.load(pid).get("resume") if unblock else None  # the step that was stuck: it continues after the apply
    aws_access.save(pid, {**state, "intent": {"reason": reason, "tickets": [t["id"] for t in tickets],
                                              "tickets_labels": [t["label"] for t in tickets], "version": version,
                                              "changes": data.get("changes") or "", "summary": data["summary"],
                                              **({"resume": resume} if resume else {})}}, "deploy")
    if same:
        aws_access.drop(pid, "access_draft")
    else:
        aws_access.save(pid, plan, "access_draft")
        store.write("reports/aws_access.md", aws_access.markdown(plan))
    if (live or unblock) and same:
        await crewchat.say(pid, "tp", "cto", ("Fix applied, and the crew's access doesn't change: planning again now. " if unblock and not live else
                                              "This is live in AWS and the crew's access doesn't change: planning the change now. ")
                           + "Nothing changes in AWS until the user approves the plan.", "update")
        await ctx.set_agent("tp", "working", "📝 Planning the change in AWS")
        await runner_mod.runner.enqueue("tp.deploy", project_id=pid)
        return
    if not same:
        await crewchat.say(pid, "cto", "crew", f"Drafted the crew's AWS access for {version}: one role per agent, only "
                           f"{', '.join(aws_access.label(s) for s in plan['services'])} named **{plan['prefix']}\\***, only eu-west-1, "
                           "each capped by the crew boundary. Nothing is created until the user approves.", "update",
                           files=["reports/aws_access.md"])
        for r in plan["roles"]:
            await crewchat.say(pid, "cto", r["agent"], f"{r['persona']}, you'll act as `{r['role']}`. You can: " + "; ".join(r["can"]) + ".", "assign")
    await ctx.set_agent("tp", "needs_approval", f"Infrastructure ready: {line}")
    await ctx.set_project(progress=round(3.5 / 7, 3))
    files = sorted(p for p in files_under(store, ("infra/",)) if p != PREVIEW)
    access_line = "" if same else f" Orion's access plan: 3 roles scoped to {plan['prefix']}*."
    await flow.request_approval(
        pid, "infra", "Terra's infrastructure" + ("" if same else " and the crew's AWS access") + f" ({version})",
        f"{data['summary']}{access_line} Approving: " + ("Orion creates the crew's roles and " if not same else "")
        + "Terra runs terraform plan. You approve that plan before anything is created in AWS (functions start with placeholder "
        "code; Terra swaps in Dev's packages later), then you check it in the AWS console.",
        [*files, *([] if same else ["reports/aws_access.md"])])


# ── deploying (with Terra's own role, made by Orion: services/aws_access) ──────────────────────────────────────────
SYMBOL = {"create": "+", "update": "~", "replace": "±", "delete": "−"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def plan_line(counts: dict) -> str:
    return f"{counts['create']} to add, {counts['update'] + counts['replace']} to change, {counts['delete']} to destroy"


def plan_markdown(p: dict) -> str:
    """The plan you approve before `terraform apply` (user, 10-02: "the plan works without issues, next is apply, the final
    step before you see it in the AWS console: check and approve"). What was checked, what approving does, the changes."""
    chk = p.get("checks") or {}
    c = p["counts"]
    risky = [x for x in p["changes"] if x["action"] in ("delete", "replace")]
    lines = [f"# Terraform plan · {p['version']}: ready for `terraform apply`", "",
             f"**{plan_line(c)}** · planned {p.get('planned_at', '')} as `{p['role']}`. **Nothing has changed in AWS yet.**", "",
             "## What was checked before this plan", "",
             f"- {'✅' if chk.get('validate') else '⚠️'} `terraform validate`: {'the Terraform is valid' if chk.get('validate') else 'not run on this host'}",
             "- ✅ Orkestra's lint: every name starts with the project prefix, every resource is tagged, no account-wide or managed policies",
             "- ✅ Orion's access check: Terra's role reaches only this project's resources, capped by the crew boundary",
             f"- ✅ `terraform plan` ran without errors, with Terra's own role" + (f" (state in `s3://{chk['state_bucket']}`)" if chk.get("state_bucket") else ""),
             *(["- ✅ Lambda functions start with placeholder code: Dev's real code comes later, as packages Terra deploys"] if chk.get("placeholder") else []),
             *([f"- ✅ Archie reviewed Dev's code and you approved it ({chk['reviewed']})"] if chk.get("reviewed") else []),
             *(["- ✅ Every function of Dev's is wired to its package (the plan updates its code)"] if chk.get("wired") else []),
             ""]
    ho = p.get("handover") or {}
    if ho.get("code") or ho.get("layers") or ho.get("images"):
        lines += ["## Dev's hand-over: what Terra deploys", "",
                  "Dev built these packages and handed them over. Terraform deploys them, so code, layers and infrastructure live in "
                  "one Terraform state: a change made outside it (e.g. in the console) shows up as drift you can undo.", "",
                  "| | Package | Into | Size | Live now → after apply |", "|---|---|---|---|---|"]
        lines += [f"| ⚡ code | `{h['source_dir']}/` | {h['function_name']} | {h['kb']} KB | "
                  f"{'placeholder' if h['from'] == 'placeholder' else h['from']} → Dev's {h['version']} |" for h in ho.get("code") or []]
        lines += [f"| 📦 layer | `layers/{h['key']}/` | {h.get('name') or h['key']} | {h['kb']} KB | → new version ({h['version']}) |"
                  for h in ho.get("layers") or []]
        lines += [f"| 🐳 image | `{h.get('source_dir')}/Dockerfile` | {h.get('function_name') or h['key']} | `…{str(h.get('uri'))[-12:]}` | "
                  f"{'placeholder' if h['from'] == 'placeholder' else h['from']} → Dev's {h['version']} |" for h in ho.get("images") or []]
        lines += [""]
    if p.get("review"):
        rv = p["review"]
        lines += [f"## Orion's review ({rv.get('cr')}): {str(rv.get('verdict', '')).replace('_', ' ')}", "", rv.get("summary") or "", ""]
    if p.get("changes_note"):
        lines += ["## What Terra changed in the Terraform", "", p["changes_note"], ""]
    if risky:
        lines += ["## ⚠️ Removed or replaced", "", "These live resources are deleted (and, for a replace, created again). Check them before approving:", "",
                  *[f"- **{x['action']}** `{x['address']}` {x['name']}" for x in risky], ""]
    lines += ["## Changes in AWS", "", "| | Resource | Name | Settings |", "|---|---|---|---|"]
    lines += [f"| {SYMBOL[x['action']]} {x['action']} | `{x['address']}` | {x['name']} | {', '.join(x.get('fields') or [])} |"
              for x in p["changes"]] or ["| | No changes | | |"]
    if chk.get("monthly_usd") is not None:
        lines += ["", f"**Cost:** Terra's estimate is about **${chk['monthly_usd']:.2f}/month** at the requirement's volume "
                  "(the AWS tab's cost panel works it out for any volume)."]
    lines += ["", "## What happens when you approve", ""]
    if ho.get("code") or ho.get("layers") or ho.get("images"):
        lines += ["1. Terra runs `terraform apply`: Dev's packages replace what's live (the placeholder, or the previous version), nothing else.",
                  "2. Dev checks every function runs exactly his package, then tests the whole flow live (one real message, hop by hop).",
                  "3. You check Dev's test and the code in AWS, then Quinn runs the test plan."]
    else:
        lines += ["1. Terra runs `terraform apply`: exactly the changes above, nothing else.",
                  "2. Orkestra lists what's in AWS on the AWS tab, with a console link for every resource.",
                  "3. You check it in the AWS console and give the go-ahead, or raise a change request (it goes to Orion and Terra)."]
    lines += ["", "Not right? Use Change request: Terra revises the Terraform and plans again; nothing is applied until you approve a plan.", ""]
    return "\n".join(lines) + "\n"


def deploy_markdown(d: dict) -> str:
    out = d.get("outputs") or {}
    return "\n".join([f"# Deployment · {d.get('version')}", "", f"Status: **{d.get('status')}** · applied {d.get('applied_at', '-')} "
                      f"as `{d.get('role')}`", "", "## Outputs", "", "| Output | Value |", "|---|---|",
                      *[f"| {k} | `{json.dumps(v) if isinstance(v, (dict, list)) else v}` |" for k, v in out.items()]]) + "\n"


def inventory_markdown(inv: dict) -> str:
    lines = [f"# What's in AWS · {inv.get('version')}", "", f"{inv['count']} resources, all named with the project prefix and tagged "
             "created_by = orkestra. Open each one in the AWS console from the AWS tab.", "", "| Service | Resource | Name | Console |", "|---|---|---|---|"]
    for r in inv["resources"]:
        if r["primary"]:
            link = f"[open]({r['console']})" if r.get("console") else ""
            lines.append(f"| {r['service_label']} | {r['kind']} | `{r['name']}` | {link} |")
    return "\n".join(lines) + "\n"


def _adopt_dev_layers(pid: str) -> list[str]:
    """A live project whose Terraform just took over the layers, while the functions still run the layers Dev published
    himself: hand Terra Dev's last packages, so the first plan publishes the same packages under Terraform and keeps
    them attached (instead of detaching them until Dev's next deploy)."""
    import shutil

    from app.agents import de

    packages = aws_access.deploy_dir(pid) / "work" / terraform.LAYER_PACKAGES
    built = aws_access.deploy_dir(pid) / "code_work" / terraform.LAYER_ZIP_DIR
    code = aws_access.load(pid, "code") or {}
    if terraform.handed_over(packages.parent)["layers"] or code.get("layers_by") == "terra" or not built.is_dir():
        return []
    packages.mkdir(parents=True, exist_ok=True)
    rec, keys = de.load_packages(pid), []
    for z in sorted(built.glob("*.zip")):
        stamp = z.with_suffix(".stamp")
        shutil.copyfile(z, packages / z.name)
        rec["layers"][z.stem] = {"fingerprint": stamp.read_text() if stamp.exists() else "", "kb": z.stat().st_size // 1024,
                                 "handed_at": _now(), "version": code.get("version"), "applied": None, "adopted": True}
        keys.append(z.stem)
    aws_access.save(pid, rec, de.PACKAGES)
    return keys


def _adopt_dev_code(pid: str, creds: dict) -> list[str]:
    """A live project whose Terraform just took over the code (the platform's packages.tf), while the functions run code
    Dev uploaded himself: download exactly that code from AWS (Terra's role) as Dev's packages, so the first plan keeps
    it live under Terraform instead of putting the placeholder back. Blocking."""
    import urllib.request

    from app.agents import de

    code = aws_access.load(pid, "code") or {}
    folder = aws_access.deploy_dir(pid) / "work" / terraform.CODE_PACKAGES
    if terraform.handed_over(folder.parent)["code"] or code.get("code_by") == "terra" or not code.get("functions"):
        return []
    folder.mkdir(parents=True, exist_ok=True)
    lam = aws_access.session_for(creds).client("lambda")
    rec, keys = de.load_packages(pid), []
    for fname, f in code["functions"].items():
        if not f.get("key"):
            continue
        got = lam.get_function(FunctionName=fname)
        with urllib.request.urlopen(got["Code"]["Location"], timeout=120) as r:  # noqa: S310 (AWS's presigned S3 URL)
            data = r.read()
        sha = de._sha(data)
        if sha != got["Configuration"]["CodeSha256"]:
            raise AgentError(f"{fname}: the code downloaded from AWS doesn't match its CodeSha256; not adopting it")
        (folder / f"{f['key']}.zip").write_bytes(data)
        aws_access.audit(pid, "tp", "lambda:GetFunction", fname, True, f"adopted Dev's live code ({len(data) // 1024} KB)")
        rec["code"][f["key"]] = {"fingerprint": sha, "kb": max(1, len(data) // 1024), "handed_at": _now(), "version": f.get("code_version") or code.get("version"),
                                 "applied": None, "adopted": True, "function_name": fname, "source_dir": f.get("source_dir")}
        keys.append(f["key"])
    aws_access.save(pid, rec, de.PACKAGES)
    return keys


async def _workspace(ctx: JobContext, pid: str, creds: dict, bucket: str):
    store = ProjectStore(pid)
    infra = files_under(store, ("infra/",))
    if terraform.manages_layers(infra):
        adopted = _adopt_dev_layers(pid)
        if adopted:
            await crewchat.say(pid, "tp", "de", f"Dev, my Terraform publishes the layers now: I took over your current packages "
                               f"({', '.join(adopted)}) so the functions keep them. Hand me new ones whenever they change.", "update")
    if terraform.manages_code(infra) and (aws_access.load(pid, "deploy") or {}).get("status") in ("deployed", "partial"):
        adopted = await asyncio.to_thread(_adopt_dev_code, pid, creds)
        if adopted:
            await crewchat.say(pid, "tp", "de", f"Dev, my Terraform deploys your code now: I took over exactly the code that's live "
                               f"({', '.join(adopted)}), so nothing changes in the functions. Hand me new packages whenever it changes.", "update")
    work = terraform.prepare(aws_access.deploy_dir(pid) / "work", terraform.package_vars(infra, aws_access.deploy_dir(pid) / "work" / terraform.PACKAGES_DIR))
    await ctx.set_agent("tp", "working", f"terraform init (state in s3://{bucket})")
    await terraform.init(work, bucket, f"{pid}/terraform.tfstate", creds)
    aws_access.audit(pid, "tp", "terraform init", f"s3://{bucket}/{pid}/terraform.tfstate")
    return work


async def _failed(ctx: JobContext, pid: str, what: str, exc: Exception, kind: str | None = None) -> None:
    """`kind`: the step that failed; Terra then asks Archie and Orion for a fix (agents/unblock.py) instead of waiting
    for the user's Try again."""
    from app.agents import unblock

    await ctx.set_agent("tp", "failed", f"{what}: {str(exc)[:200]}")
    await ctx.set_project(status="failed", last_activity=f"Terra: {what}: {str(exc)[:160]}")
    await ctx.emit("build.updated", f"Terra: {what}", agent="tp")
    await crewchat.say(pid, "tp", "cto", f"{what}: {unblock._tail(str(exc), 600)}", "issue")
    if kind:
        await unblock.escalate(ctx, "tp", kind, what, exc)


@handler("tp.deploy", resumable=False)
async def deploy(ctx: JobContext) -> None:
    """terraform plan with Terra's own role. Right after the infra gate (`approved_infra`), a plan that only creates
    resources is what the user just approved: applied at once. Anything that changes or removes live resources gets
    its own "deploy" gate first."""
    pid = ctx.project_id
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    acc = aws_access.load(pid)
    if not acc or acc.get("status") != "active":
        await crewchat.say(pid, "tp", "cto", "I have no AWS role for this project yet. Orion, please create the crew's access first.", "issue")
        await ctx.set_agent("tp", "blocked", "Waiting for Orion's AWS access (no role yet)")
        await ctx.set_project(status="waiting", last_activity="Paused: no AWS role for Terra yet")
        return
    role = aws_access.role_name(pid, "tp")
    state = aws_access.load(pid, "deploy") or {}
    intent = state.get("intent") or {"reason": "build", "tickets": []}
    await ctx.set_agent("tp", "working", f"🔑 Acting as {role}")
    await ctx.set_project(status="running", last_activity="Terra is planning the change in AWS (terraform plan)")
    await crewchat.say(pid, "tp", "cto", f"Planning {version} as `{role}`: terraform plan only, nothing changes yet.", "ack")
    try:
        if not terraform.available():
            raise AgentError("terraform isn't installed on this host")
        creds = await asyncio.to_thread(aws_access.assume, pid, "tp")
        work = await _workspace(ctx, pid, creds, acc["state_bucket"])
        await ctx.set_agent("tp", "working", "📝 terraform plan")
        p = await terraform.plan(work, creds)
        aws_access.audit(pid, "tp", "terraform plan", plan_line(p["counts"]))
    except Exception as exc:
        await _failed(ctx, pid, "The plan failed", exc, kind="tp.deploy")
        raise
    from app.agents import de

    preview = load_preview(pid) or {}
    note = intent.get("changes") or ""
    packages = intent.get("reason") in ("packages", "layers")
    ho = intent.get("handover") or {}
    if intent.get("reason") == "layers":  # 10-02: layers only
        pk = de.load_packages(pid)["layers"]
        ho = {"code": [], "layers": [{"key": k, "kb": (pk.get(k) or {}).get("kb", 0), "version": intent.get("version"), "from": "", "name": k}
                                     for k in intent.get("layers") or []]}
    if packages:
        note = ("Nothing in the Terraform changed: Dev handed over " + "; ".join(
            [f"the code for {h['function_name']} ({h['kb']} KB from {h['source_dir']}/)" for h in ho.get("code") or []]
            + [f"layer {h['key']} ({h['kb']} KB from layers/{h['key']}/)" for h in ho.get("layers") or []]
            + [f"container image {h['key']} (from {h.get('source_dir')}/Dockerfile, in ECR)" for h in ho.get("images") or []])
            + ". The platform's infra/packages.tf deploys them: functions and services take Dev's code and images instead of the "
            "placeholder, and attach the new layer versions.")
    unwired = [h for h in ho.get("code") or []
               if not any(c["name"] == h["function_name"] and c["action"] in ("update", "replace", "create") for c in p["changes"])]
    if unwired:  # no change planned for it: fine only if the state already holds that package (an earlier apply got through)
        held = {(r["values"].get("function_name"), r["values"].get("source_code_hash")) for r in await terraform.state(work, creds)
                if r["type"] == "aws_lambda_function"}
        fp = de.load_packages(pid)["code"]
        unwired = [h["function_name"] for h in unwired if (h["function_name"], (fp.get(h["key"]) or {}).get("fingerprint")) not in held]
    rv = aws_access.load(pid, "code_review") or {}
    rec = {"version": version, "role": role, "counts": p["counts"], "changes": p["changes"], "planned_at": _now(),
           "config_keys": p["config_keys"], "reason": intent.get("reason"), "changes_note": note, "handover": ho if packages else None,
           "review": state.get("review") if intent.get("reason") == "change" else None,
           "checks": {"validate": bool((preview.get("validate") or {}).get("ok")), "state_bucket": acc.get("state_bucket"),
                      "placeholder": intent.get("reason") == "build" and any(c["address"].startswith("aws_lambda_function.") for c in p["changes"]),
                      "reviewed": f"code review, round {rv.get('round') or 1}" if packages and rv.get("version") == version else None,
                      "wired": bool(ho.get("code") or ho.get("images")) and not unwired,
                      "monthly_usd": round(sum(r.get("monthly_usd") or 0 for r in preview.get("resources", [])), 2) if preview else None}}
    aws_access.save(pid, {**state, "status": state.get("status") or "planned", "plan": rec}, "deploy")
    store.write("reports/deploy_plan.md", plan_markdown(rec))
    line = plan_line(p["counts"])
    if unwired:  # the package would never reach AWS: Terra's Terraform doesn't take it (lint should have caught it)
        exc = AgentError(f"my plan doesn't deploy Dev's package into {', '.join(unwired)}: the function's filename/source_code_hash "
                         "must look up var.code_packages / local.code_hashes with its code_deploy key")
        await _failed(ctx, pid, "Dev's code isn't wired into the plan", exc, kind="tp.deploy")
        raise exc
    if packages:
        got = ([f"code for {h['function_name']}" for h in ho.get("code") or []] + [f"layer {h['key']}" for h in ho.get("layers") or []]
               + [f"image {h['key']}" for h in ho.get("images") or []])
        await crewchat.say(pid, "tp", "de", f"Got your packages, Dev: {', '.join(got)}. Planning the deploy; the user approves the plan "
                           "before anything changes in AWS.", "ack")
    if not p["changes"]:  # nothing would change in AWS: just record the state (no apply to approve)
        await crewchat.say(pid, "tp", "cto", f"Plan for {version}: nothing to change in AWS. Recording the state.", "handoff",
                           files=["reports/deploy_plan.md"])
        await runner_mod.runner.enqueue("tp.apply", project_id=pid)
        return
    # Every plan that changes AWS waits for the user (user, 10-02: "before apply, ask: everything is ready, only apply is pending")
    await crewchat.say(pid, "tp", "cto", f"Plan for {version}: **{line}**.\n" + "\n".join(
        f"{SYMBOL[c['action']]} `{c['address']}` {c['name']}" + (f" ({', '.join(c['fields'])})" if c.get("fields") else "")
        for c in p["changes"][:25]), "handoff", files=["reports/deploy_plan.md"])
    if p["counts"]["delete"] or p["counts"]["replace"]:
        await crewchat.say(pid, "tp", "user", f"⚠️ This plan removes or replaces {p['counts']['delete'] + p['counts']['replace']} "
                           "live resource(s); check them before approving.", "question")
    await ctx.set_agent("tp", "needs_approval", f"Plan ready, waiting for your go: {line}")
    await ctx.emit("build.updated", f"Terra's plan: {line}", agent="tp")
    first = intent.get("reason") == "build" and (state.get("status") not in ("deployed", "partial"))
    n_code, n_layers, n_images = len(ho.get("code") or []), len(ho.get("layers") or []), len(ho.get("images") or [])
    parts = [f"Dev's code into {n_code} function(s)" if n_code else "", f"{n_layers} layer(s)" if n_layers else "",
             f"{n_images} container image(s)" if n_images else ""]
    swap = " + ".join(p for p in parts if p)
    if swap and not swap.startswith("Dev's"):
        swap = "Dev's " + swap
    what = {"tickets": "the fix for " + ", ".join(intent.get("tickets_labels") or []) if intent.get("tickets_labels") else "the ticket fix",
            "change": "the infrastructure change", "build": "the infrastructure",
            "packages": f"deploying {swap}", "layers": f"Dev's layer packages ({', '.join(intent.get('layers') or [])}): publish and attach"
            }.get(intent.get("reason"), "the change")
    if packages:
        await crewchat.say(pid, "tp", "user", f"Dev's packages are planned ({line}): {swap}, nothing else. Approve and I run `terraform apply`; "
                           "then Dev checks his code is live and tests the whole flow.", "question", files=["reports/deploy_plan.md"])
    else:
        await crewchat.say(pid, "tp", "user", f"Everything is validated and planned ({line}). The only step left is `terraform apply`: approve it "
                           "and I create exactly this in AWS; then you check it in the console.", "question", files=["reports/deploy_plan.md"])
    await flow.request_approval(pid, "deploy", (f"Ready to create in AWS: Terra's plan ({line})" if first else f"Terra's plan for {what} ({line})"),
                                (f"Dev handed over {swap} for {version}; Terra planned the deploy as {role}. Nothing has changed in AWS yet. "
                                 "Approving runs terraform apply (the placeholder or previous code is replaced by Dev's package), then Dev "
                                 "checks it and tests the whole flow live." if packages else
                                 f"terraform plan for {version} as {role}: validated, the roles exist, nothing has changed in AWS yet. "
                                 "The only step left is apply: approving creates/changes exactly these resources, then you check them in the AWS console."),
                                ["reports/deploy_plan.md"])


@handler("tp.apply", resumable=False)
async def apply(ctx: JobContext) -> None:
    """Apply the plan, record the outputs and the AWS inventory. Then: the user checks the resources (infra_check), or
    for a ticket fix, Terra resolves the tickets back to Quinn and Orion routes the next step."""
    from app.services import inventory, tickets as tk

    pid = ctx.project_id
    store = ProjectStore(pid)
    work = aws_access.deploy_dir(pid) / "work"
    state = aws_access.load(pid, "deploy") or {}
    intent = state.get("intent") or {"reason": "build", "tickets": []}
    if not (work / "infra" / "tfplan").exists():
        await crewchat.say(pid, "tp", "cto", "The approved plan isn't on disk any more (or went stale), so I'm planning again; "
                           "you'll get a fresh plan to approve.", "issue")
        await runner_mod.runner.enqueue("tp.deploy", project_id=pid)
        return
    role = aws_access.role_name(pid, "tp")
    plan = state.get("plan") or {}
    counts = plan.get("counts") or {"create": 0, "update": 0, "replace": 0, "delete": 0}
    await ctx.set_agent("tp", "working", "🚀 terraform apply")
    await ctx.set_project(status="running", last_activity="Terra is creating the infrastructure in AWS")
    await crewchat.say(pid, "tp", "cto", f"Applying as `{role}`: {plan_line(counts)}.", "ack")
    try:
        creds = await asyncio.to_thread(aws_access.assume, pid, "tp")
        await terraform.init(work, aws_access.state_bucket(pid), f"{pid}/terraform.tfstate", creds)
        await terraform.apply(work, creds)
        outputs = await terraform.output(work, creds)
        aws_access.audit(pid, "tp", "terraform apply", plan_line(counts))
    except Exception as exc:
        (work / "infra" / "tfplan").unlink(missing_ok=True)  # after a partial apply the plan is stale: Retry re-plans
        aws_access.save(pid, {**state, "status": "partial" if state.get("status") != "deployed" else "deployed",
                              "error": str(exc)[:1500]}, "deploy")
        await _failed(ctx, pid, "terraform apply failed (anything already created is tracked in the state; Try again re-plans, "
                      "Tear down removes it all)", exc, kind="tp.apply")
        raise
    (work / "infra" / "tfplan").unlink(missing_ok=True)
    try:  # the AWS page: every resource and its settings (never blocks the hand-over)
        await ctx.set_agent("tp", "working", "🗂 Listing what's in AWS (terraform show)")
        rows = await terraform.state(work, creds)
        schema = await terraform.schema(work, {r["type"] for r in rows}, creds)
        inv = inventory.build(pid, rows, schema, plan.get("config_keys") or {}, aws_access.load(pid, "code"), plan.get("version"))
        inv["applied_at"] = _now()
        inventory.save(pid, inv)
        store.write("reports/aws_inventory.md", inventory_markdown(inv))
    except Exception as exc:  # noqa: BLE001
        inv = inventory.load(pid) or {"count": len(plan.get("changes") or []), "resources": []}
        await crewchat.say(pid, "tp", "cto", f"Applied, but I couldn't list the resources for the AWS page: {str(exc)[:200]}", "issue")
    n = int(state.get("applies") or 0) + 1
    state = {**state, "status": "deployed", "outputs": outputs, "applied_at": _now(), "version": plan.get("version"), "role": role,
             "error": None, "applied": counts, "applies": n, "intent": None, "last_intent": intent, "review": None}
    aws_access.save(pid, state, "deploy")
    from app.agents import unblock

    unblock.clear(pid, "tp")  # applied: a later problem gets fresh help from Archie and Orion
    store.write("reports/deploy.md", deploy_markdown(state))
    store.append_changelog(store.manifest()["current_version"], [f"Infrastructure applied in AWS by Terra: {plan_line(counts)}"])
    await ctx.emit("build.updated", f"Terra applied the infrastructure: {plan_line(counts)}", agent="tp")
    from app.agents import de

    packages = de.load_packages(pid)
    have = terraform.handed_over(work / terraform.PACKAGES_DIR)
    if (packages["code"] or packages["layers"] or packages["images"]) and terraform.manages_layers(files_under(store, ("infra/",))):
        for kind in ("code", "layers", "images"):  # every package in the workspace was in this apply's tfvars: it's live now
            for k, rec in packages[kind].items():
                if k in have[kind] and rec.get("applied") != rec.get("fingerprint"):
                    rec.update(applied=rec.get("fingerprint"), applied_version=rec.get("version"), applied_at=_now())
        aws_access.save(pid, packages, de.PACKAGES)
    if intent.get("reason") in ("packages", "layers"):  # Dev's packages are in AWS (you approved this plan): back to Dev to check and test
        ho = intent.get("handover") or {}
        done = [f"⚡ {h['function_name']} runs Dev's {h['version']} (placeholder gone)" if h["from"] == "placeholder"
                else f"⚡ {h['function_name']}: {h['from']} → {h['version']}" for h in ho.get("code") or []]
        done += [f"📦 layer {h.get('name') or h['key']}: new version published and attached" for h in ho.get("layers") or []]
        done += [f"🐳 image {h['key']} running in {h['function_name']} (" + ("placeholder gone" if h["from"] == "placeholder" else f"{h['from']} → {h['version']}") + ")"
                 for h in ho.get("images") or []]
        await crewchat.say(pid, "tp", "de", f"Done, Dev: your packages are live in AWS ({plan_line(counts)}):\n" + "\n".join(f"- {d}" for d in done)
                           + "\nEverything is in one Terraform state now. Over to you: check and test the whole flow.", "handoff",
                           files=["reports/deploy_plan.md", "reports/aws_inventory.md"])
        await ctx.set_agent("tp", "done", f"Deployed Dev's packages ({plan_line(counts)})")
        await ctx.emit("build.updated", "Terra deployed Dev's packages", agent="tp")
        await runner_mod.runner.enqueue("de.deploy", project_id=pid, tickets=intent.get("tickets") or [], changes=intent.get("changes") or "",
                                        attempt=int(intent.get("attempt") or 0))
        return
    if intent.get("reason") == "tickets" and intent.get("tickets"):
        version = store.manifest()["current_version"]
        for t in await tk.rows_for(pid, intent["tickets"]):
            await tk.change(t["id"], "tp", f"Fixed in the infrastructure ({version}), applied in AWS ({plan_line(counts)})."
                            + (f" {intent.get('changes')}" if intent.get("changes") else "") + " Quinn, please retest.",
                            status="resolved", assignee="qa", version_fixed=version)
        await ctx.set_agent("tp", "done", f"Ticket fix applied: {plan_line(counts)}")
        await runner_mod.runner.enqueue("cto.tickets", project_id=pid)
        return
    resume = intent.get("resume")
    if resume:  # a fix Archie and Orion asked for: the step that was stuck carries on now (no separate AWS check)
        from app.agents import unblock as unblock_mod

        rec = unblock_mod.load(pid)
        rec.pop("resume", None)
        aws_access.save(pid, rec, unblock_mod.STORE)
        who = crewchat.NAMES.get(resume.get("agent", ""), resume.get("agent", ""))
        await crewchat.say(pid, "tp", resume.get("agent") or "crew", f"Fix applied in AWS ({plan_line(counts)}). {who}, carry on from where "
                           "you stopped.", "handoff", files=["reports/deploy_plan.md"])
        await ctx.set_agent("tp", "done", f"Fix applied: {plan_line(counts)}")
        await ctx.set_project(status="running", last_activity=f"Fix applied; {who} carries on")
        await runner_mod.runner.enqueue(resume["kind"], project_id=pid, **(resume.get("payload") or {}))
        return
    primary = [r for r in inv.get("resources", []) if r.get("primary")]
    await crewchat.say(pid, "tp", "user", f"✅ The infrastructure for {plan.get('version')} is in AWS ({plan_line(counts)}). Please check "
                       f"it in the AWS console: {len(primary) or inv.get('count', 0)} resources, each with a link on the AWS tab. "
                       "Approve when it matches, or raise a change request: it goes straight to Orion and me.", "question",
                       files=["reports/aws_inventory.md", "reports/deploy.md"])
    await ctx.set_agent("tp", "needs_approval", f"In AWS: {plan_line(counts)}. Waiting for your check")
    await ctx.set_project(progress=round(4.2 / 7, 3), last_activity="The infrastructure is in AWS: check it")
    await flow.request_approval(pid, "infra_check", f"Check the infrastructure in AWS ({len(primary) or inv.get('count', 0)} resources)",
                                f"Terra created/changed it as `{role}` ({plan_line(counts)}). Open each resource from the AWS tab in the "
                                "AWS console and confirm names and settings. Approving tells the crew it's ready for the code.",
                                ["reports/aws_inventory.md", "reports/deploy.md"])


@handler("tp.inventory", resumable=False)
async def refresh_inventory(ctx: JobContext) -> None:
    """The AWS page's "Refresh from AWS": read every resource as it is in AWS now (refresh-only plan: nothing is written,
    not even the state) and flag settings changed outside Terraform. Terra's role; no AI call; his card is untouched."""
    from app.services import inventory

    pid = ctx.project_id
    acc = aws_access.load(pid) or {}
    dep = aws_access.load(pid, "deploy") or {}
    if acc.get("status") != "active" or dep.get("status") not in ("deployed", "partial"):
        return
    work = terraform.prepare(aws_access.deploy_dir(pid) / "work", terraform.package_vars(files_under(ProjectStore(pid), ("infra/",)), aws_access.deploy_dir(pid) / "work" / terraform.PACKAGES_DIR))
    try:
        creds = await asyncio.to_thread(aws_access.assume, pid, "tp")
        await terraform.init(work, acc["state_bucket"], f"{pid}/terraform.tfstate", creds)
        infra = files_under(ProjectStore(pid), ("infra/",))
        rows, drift = await terraform.live(work, creds, ignore=terraform.CODE_FIELDS if terraform.manages_layers(infra) else None)
        schema = await terraform.schema(work, {r["type"] for r in rows}, creds)
        aws_access.audit(pid, "tp", "terraform plan -refresh-only", f"{len(rows)} resources read, {len(drift)} changed outside Terraform")
    except Exception as exc:  # noqa: BLE001
        await ctx.emit("infra.refreshed", f"Couldn't read AWS: {str(exc)[:200]}", agent="tp", ok=False)
        raise
    inv = inventory.build(pid, rows, schema, (dep.get("plan") or {}).get("config_keys") or {}, aws_access.load(pid, "code"), dep.get("version"), drift)
    inv["applied_at"] = (inventory.load(pid) or {}).get("applied_at") or dep.get("applied_at")
    inv["refreshed_at"] = _now()
    inventory.save(pid, inv)
    if drift:
        await crewchat.say(pid, "tp", "user", "⚠️ Changed in AWS outside Terraform: " + "; ".join(
            f"`{a}` ({', '.join(k[:4])})" for a, k in list(drift.items())[:6]) + ". The next plan would put them back; raise a change "
            "request if the new values should stay.", "issue")
    await ctx.emit("infra.refreshed", f"Read {len(rows)} resources from AWS" + (f"; {len(drift)} changed outside Terraform" if drift else ""),
                   agent="tp", ok=True)


@handler("tp.destroy", resumable=False)
async def destroy(ctx: JobContext) -> None:
    """Tear down: terraform destroy with Terra's role, Dev's layer versions, then Orion removes the roles and the state bucket."""
    pid = ctx.project_id
    acc = aws_access.load(pid)
    state = aws_access.load(pid, "deploy") or {}
    await ctx.set_agent("tp", "working", "🧹 Tearing down the AWS resources (terraform destroy)")
    await ctx.set_project(status="running", last_activity="Terra is tearing down the AWS resources")
    await crewchat.say(pid, "tp", "cto", "Tearing down everything this project created in AWS (terraform destroy).", "ack")
    try:
        if acc and acc.get("status") == "active" and state.get("status") in ("deployed", "partial", "planned", "destroy_failed"):
            creds = await asyncio.to_thread(aws_access.assume, pid, "tp")
            work = await _workspace(ctx, pid, creds, acc["state_bucket"])
            left = [r["address"] for r in await terraform.state(work, creds) if r["type"] in terraform.UNDELETABLE]
            if left:  # e.g. task definitions planned before skip_destroy was required: AWS won't let the crew deregister them
                await terraform.forget(work, creds, left)
                await crewchat.say(pid, "tp", "cto", f"Leaving {', '.join(left)} registered (free; AWS has no per-resource permission "
                                   "to remove them), the rest goes.", "update")
            await terraform.destroy(work, creds)
            aws_access.audit(pid, "tp", "terraform destroy", "all resources")
            await asyncio.to_thread(_delete_layers, pid, creds)
        state = {**state, "status": "destroyed", "destroyed_at": _now(), "outputs": {}, "plan": None, "error": None, "intent": None}
        aws_access.save(pid, state, "deploy")
        code = aws_access.load(pid, "code")
        if code:
            aws_access.save(pid, {**code, "functions": {}, "layers": {}, "status": "destroyed"}, "code")
        inv = aws_access.load(pid, "inventory")
        if inv:
            aws_access.save(pid, {**inv, "resources": [], "services": [], "count": 0, "destroyed_at": _now()}, "inventory")
        if acc and acc.get("status") == "active":
            await crewchat.say(pid, "cto", "crew", "Resources are gone; removing the crew's roles and the Terraform state bucket.", "update")
            await asyncio.to_thread(aws_access.revoke, pid, acc, True)
            aws_access.save(pid, {**acc, "status": "removed", "removed_at": _now()})
        shutil.rmtree(aws_access.deploy_dir(pid) / "work", ignore_errors=True)  # incl. the packages Dev handed over
        shutil.rmtree(aws_access.deploy_dir(pid) / "code_work", ignore_errors=True)
        for name in ("packages", "layer_packages"):  # nothing is live any more: a new deploy hands everything over again
            aws_access.drop(pid, name)
    except Exception as exc:
        aws_access.save(pid, {**state, "status": "destroy_failed", "error": str(exc)[:1500]}, "deploy")
        await _failed(ctx, pid, "Tear down failed (nothing else was touched; Try again continues)", exc)
        raise
    await crewchat.say(pid, "tp", "user", "🧹 Torn down: the project's AWS resources, Dev's layers, the crew's roles and the state bucket "
                       "are deleted. The code, documents and history stay here.", "update")
    await ctx.set_agent("tp", "done", "Torn down: nothing left in AWS")
    await ctx.set_project(status="completed", last_activity="Torn down: nothing left in AWS")
    await ctx.emit("build.updated", "Terra tore down the AWS resources", agent="tp")


def _delete_layers(pid: str, creds: dict) -> None:
    """Every version of the layers Dev published for this project (only names with the project prefix). Blocking."""
    from botocore.exceptions import ClientError

    acc = aws_access.load(pid) or {}
    prefix = acc.get("prefix")
    if not prefix:
        return
    lam = aws_access.session_for(creds).client("lambda")
    names = set((aws_access.load(pid, "code") or {}).get("layers", {}).keys())
    names |= {layer["LayerName"] for page in lam.get_paginator("list_layers").paginate() for layer in page["Layers"]
              if layer["LayerName"].startswith(prefix)}
    for name in sorted(n for n in names if n.startswith(prefix)):
        try:
            for page in lam.get_paginator("list_layer_versions").paginate(LayerName=name):
                for v in page["LayerVersions"]:
                    lam.delete_layer_version(LayerName=name, VersionNumber=v["Version"])
                    aws_access.audit(pid, "tp", "lambda:DeleteLayerVersion", f"{name}:{v['Version']}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
