"""Live check of the real infrastructure-first machinery, with no LLM: a scratch project with a tiny hand-written flow
(API Gateway → Lambda (+ a code layer) → SQS) goes through Terra's hand-over (Orion's access draft) → roles → plan →
apply with placeholder code (the API answers 503) → the AWS inventory → Dev hands his code and layer packages to Terra →
Terra's plan (a gate) and apply swap them in (the API answers 200; Dev's role can't upload code itself) → Dev checks the
function runs exactly his package → a console-style code edit is caught as drift and restored → a queue read with
Quinn's role → tear down (destroy, layer versions, roles and state bucket).

AWS cost ≈ $0 (a few requests). Everything is orkestra-* and tagged; tear down runs even when a step fails.
Run ON THE HOST (needs terraform, the platform role and the network):
  cd /opt/orkestra/app/backend && sudo -u orkestra env ORKESTRA_DATA_DIR=/opt/orkestra/data HOME=/opt/orkestra \
     /opt/orkestra/venv/bin/python -m tests.live_deploy
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from app.tools.terraform import PACKAGES_TF

INFRA = {
    "infra/versions.tf": """terraform {
  required_version = ">= 1.10"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
  backend "s3" {}
}
""",
    "infra/providers.tf": """provider "aws" {
  region = "eu-west-1"
  default_tags {
    tags = { created_by = "orkestra", project_id = var.project_id }
  }
}
""",
    "infra/variables.tf": """variable "name_prefix" {
  type    = string
  default = "orkestra-deploycheck-__ID__"
}
variable "names" {
  type    = map(string)
  default = { out = "out", echo = "echo", echo_role = "echo-role", api = "api", util_layer = "util" }
}
variable "project_id" {
  type    = string
  default = "__PID__"
}
variable "permissions_boundary_arn" {
  type    = string
  default = "arn:aws:iam::144831534428:policy/orkestra-agent-boundary"
}
""",
    "infra/main.tf": """data "archive_file" "placeholder_echo" {
  type        = "zip"
  output_path = "${path.module}/../build/placeholder-echo.zip"
  source {
    filename = "handler.py"
    content  = "def handler(event, context):\\n    return {\\"statusCode\\": 503, \\"body\\": \\"Code not deployed yet\\"}\\n"
  }
}

resource "aws_sqs_queue" "out" {
  name                    = "${var.name_prefix}-${var.names["out"]}"
  sqs_managed_sse_enabled = true
}

resource "aws_iam_role" "fn" {
  name                 = "${var.name_prefix}-${var.names["echo_role"]}"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole",
  Principal = { Service = "lambda.amazonaws.com" } }] })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.fn.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "send" {
  name = "send"
  role = aws_iam_role.fn.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sqs:SendMessage",
  Resource = aws_sqs_queue.out.arn }] })
}

resource "aws_cloudwatch_log_group" "fn" {
  name              = "/aws/lambda/${var.name_prefix}-${var.names["echo"]}"
  retention_in_days = 1
}

resource "aws_lambda_function" "echo" {
  function_name    = "${var.name_prefix}-${var.names["echo"]}"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.14"
  handler          = "handler.handler"
  filename         = lookup(var.code_packages, "echo", data.archive_file.placeholder_echo.output_path)
  source_code_hash = lookup(local.code_hashes, "echo", data.archive_file.placeholder_echo.output_base64sha256)
  timeout          = 10
  environment {
    variables = { QUEUE_URL = aws_sqs_queue.out.url }
  }
  layers     = [for k in ["util"] : local.layer_arns[k] if contains(keys(local.layer_arns), k)]
  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.send]
}

locals {
  layer_names = { util = "${var.name_prefix}-${var.names["util_layer"]}" }
}

resource "aws_api_gateway_rest_api" "api" {
  name = "${var.name_prefix}-${var.names["api"]}"
}

resource "aws_api_gateway_resource" "echo" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "echo"
}

resource "aws_api_gateway_method" "post" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.echo.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "post" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.echo.id
  http_method             = aws_api_gateway_method.post.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.echo.invoke_arn
}

resource "aws_api_gateway_deployment" "d" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  triggers    = { redeploy = sha1(jsonencode([aws_api_gateway_integration.post.id, aws_api_gateway_method.post.id])) }
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "dev" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.d.id
  stage_name    = "dev"
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.echo.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/*"
}

output "api_url" {
  value = "${aws_api_gateway_stage.dev.invoke_url}/echo"
}
output "queue_url" {
  value = aws_sqs_queue.out.url
}
output "code_deploy" {
  value = {
    functions = {
      echo = { function_name = aws_lambda_function.echo.function_name, source_dir = "src/echo", layers = ["util"] }
    }
    layers = local.layer_names
  }
}
""",
    "infra/packages.tf": PACKAGES_TF,
    "src/echo/handler.py": """import json
import os

import boto3
from echo_util import tag

sqs = boto3.client("sqs")


def handler(event, context):
    body = tag(event.get("body") or "{}")
    sqs.send_message(QueueUrl=os.environ["QUEUE_URL"], MessageBody=body)
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"queued": True})}
""",
    "layers/util/python/echo_util.py": """import json


def tag(body: str) -> str:
    data = json.loads(body)
    data["via_layer"] = True
    return json.dumps(data, sort_keys=True)
""",
}


async def main() -> None:
    import httpx

    from sqlalchemy import select

    from app.agents import access as access_mod, de, drift, tp
    from app.db.base import SessionLocal, init_db
    from app.db.models import AgentState, Approval, Project, User
    from app.orchestrator import runner as runner_mod
    from app.orchestrator.runner import JobContext
    from app.services import aws_access, inventory
    from app.services.storage import ProjectStore
    from app.tools import terraform
    from tests.live_change_flow import CapturedJobs

    runner_mod.runner = CapturedJobs()  # hand-offs are recorded, not run: this script drives each step itself
    await init_db()
    async with SessionLocal() as db:
        owner_id = (await db.execute(select(User.id).limit(1))).scalar_one()
        p = Project(name="[scratch] deploy check", owner_id=owner_id, budget_usd=1.0, archived=True)
        db.add(p)
        await db.flush()
        for k in ("cto", "intake", "ba", "ta", "tp", "de", "qa"):
            db.add(AgentState(project_id=p.id, agent=k, status="waiting"))
        await db.commit()
        pid = p.id
    store = ProjectStore(pid)
    store.create("scratch")
    short = uuid.uuid4().hex[:6]
    for path, content in INFRA.items():
        store.write(path, content.replace("__ID__", short).replace("__PID__", pid))
    t0 = time.time()
    ctx = lambda label, **payload: JobContext(f"job_{label}", pid, payload, {}, owner_id)  # noqa: E731
    say = lambda s: print(f"[{time.time() - t0:6.0f}s] {s}", flush=True)  # noqa: E731
    ok = False
    try:
        problems = terraform.lint({k: v.replace("__PID__", pid) for k, v in INFRA.items() if k.startswith("infra/")}, pid)
        say(f"lint: {problems or 'clean'}")
        await tp.handover(ctx("IAC"), {"summary": "deploy check", "changes": ""}, "check", [])
        draft = aws_access.load(pid, "access_draft")
        say(f"Orion's draft: prefix {draft['prefix']} · services {draft['services']} · problem {draft['problem']}")
        assert not draft["problem"], draft["problem"]
        await access_mod.grant(ctx("GRANT"))
        acc = aws_access.load(pid)
        say(f"roles: {[r['role'] for r in acc['roles']]} · state s3://{acc['state_bucket']}")
        await tp.deploy(ctx("PLAN", approved_infra=True))
        d = aws_access.load(pid, "deploy")
        async with SessionLocal() as db:
            gate = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.stage == "deploy",
                                                            Approval.status == "pending"))).scalars().first()
        say(f"plan: {d['plan']['counts']} · gate: {gate.title if gate else None} · next jobs {[k for k, _ in runner_mod.runner.jobs][-2:]}")
        assert gate and runner_mod.runner.jobs[-1][0] != "tp.apply", "every plan that changes AWS waits for the user's go"
        await tp.apply(ctx("APPLY"))  # = the user approved the plan
        d = aws_access.load(pid, "deploy")
        inv = inventory.load(pid) or {}
        say(f"applied: status {d['status']} · outputs {list(d['outputs'])} · inventory {inv.get('count')} resources, "
            f"{inv.get('settings')} settings")
        fn = next((r for r in inv.get("resources", []) if r["type"] == "aws_lambda_function"), None)
        if fn:
            say(f"  {fn['name']}: set {[i['key'] for i in fn['set']][:8]} · defaults {len(fn['defaults'])} · facts {len(fn['facts'])} · {fn['console']}")
        url, queue = d["outputs"]["api_url"], d["outputs"]["queue_url"]
        payload = json.dumps({"check": short})

        async def post() -> httpx.Response:
            for _ in range(6):  # a brand-new API can take a few seconds to answer
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(url, content=payload, headers={"Content-Type": "application/json"})
                if r.status_code not in (403, 500, 502):
                    return r
                await asyncio.sleep(5)
            return r
        r = await post()
        say(f"placeholder: HTTPS POST → {r.status_code} {r.text[:80]}")
        placeholder_ok = r.status_code == 503
        creds = await asyncio.to_thread(aws_access.assume, pid, "de")
        work = terraform.prepare(aws_access.deploy_dir(pid) / "code_work", {k: v for k, v in INFRA.items()} | {
            k: store.read(k).decode() for k in INFRA if k.startswith("infra/")})
        built = await terraform.build_layers(work)
        cd = d["outputs"]["code_deploy"]
        # Terra deploys everything (10-03): Dev hands his code and layer packages over; Terra's plan swaps them in for the
        # placeholder (the user approves); then Dev checks the function runs exactly his package
        await asyncio.to_thread(de.check_fit, creds, cd, work)
        handed = de.hand_over(pid, work, built, cd, "v1")
        say(f"Dev handed over: code {[(h['key'], h['kb'], h['from']) for h in handed['code']]} · layers {[h['key'] for h in handed['layers']]}")
        assert [h["key"] for h in handed["code"]] == ["echo"] and [h["key"] for h in handed["layers"]] == ["util"]
        try:  # Dev's role reads and tests; it can't upload code any more
            await asyncio.to_thread(lambda: aws_access.session_for(creds).client("lambda").update_function_code(
                FunctionName=cd["functions"]["echo"]["function_name"], ZipFile=b"x"))
            say("FAIL: Dev could upload code himself")
            dev_locked = False
        except Exception as exc:  # noqa: BLE001
            say(f"Dev can't upload code himself: {type(exc).__name__} ✅")
            dev_locked = "AccessDenied" in type(exc).__name__ or "AccessDenied" in str(exc)
        dep = aws_access.load(pid, "deploy")
        aws_access.save(pid, {**dep, "intent": {"reason": "packages", "code": ["echo"], "layers": ["util"], "handover": handed, "tickets": [],
                                                "changes": "", "attempt": 0, "version": "v1"}}, "deploy")
        await tp.deploy(ctx("PLAN_PACKAGES"))
        d = aws_access.load(pid, "deploy")
        async with SessionLocal() as db:
            gate = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.stage == "deploy",
                                                            Approval.status == "pending"))).scalars().first()
        say(f"Terra's package plan: {d['plan']['counts']} · {[(c['address'], c.get('fields')) for c in d['plan']['changes']]} · "
            f"wired {d['plan']['checks'].get('wired')} · gate: {gate.title if gate else None}")
        assert gate and any(c["address"].startswith("aws_lambda_layer_version.package") for c in d["plan"]["changes"])
        assert any(c["address"] == "aws_lambda_function.echo" for c in d["plan"]["changes"]) and d["plan"]["checks"].get("wired")
        await tp.apply(ctx("APPLY_PACKAGES"))  # = the user approved it
        say(f"after the apply, next job: {runner_mod.runner.jobs[-1][0]} · packages {de.load_packages(pid)}")
        assert runner_mod.runner.jobs[-1][0] == "de.deploy" and de.hand_over(pid, work, built, cd, "v1") == {"code": [], "layers": [], "images": []}
        pushed = await asyncio.to_thread(de.record_live, pid, creds, cd, "v1")
        aws_access.save(pid, {"status": "deployed", "functions": pushed["functions"], "layers": pushed["layers"], "deploys": 1,
                              "code_by": "terra", "layers_by": "terra"}, "code")
        say(f"Dev checked what Terra deployed: {pushed['changed']}")
        again = await asyncio.to_thread(de.record_live, pid, creds, cd, "v1")
        say(f"checked again with no change: changed {again['changed']} · unchanged {again['unchanged']}")
        r = await post()
        say(f"with Dev's code: HTTPS POST → {r.status_code} {r.text[:80]}")
        qcreds = await asyncio.to_thread(aws_access.assume, pid, "qa")
        sqs = aws_access.session_for(qcreds).client("sqs")
        msgs = await asyncio.to_thread(lambda: sqs.receive_message(QueueUrl=queue, WaitTimeSeconds=10).get("Messages", []))
        say(f"queue read as Quinn: {[m['Body'] for m in msgs]}")
        ok = (placeholder_ok and dev_locked and r.status_code == 200 and not again["changed"]
              and any(json.loads(m["Body"]) == {"check": short, "via_layer": True} for m in msgs))
        # drift (10-03): a "console" edit of the code and of the queue (Terra's role, this scratch project only); Terra's
        # drift check finds both, and his restore puts the source of truth back with no Dev or Quinn step
        async with SessionLocal() as db:  # the gates this script answered by calling the next step itself
            for a in (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.status == "pending"))).scalars():
                a.status = "approved"
            await db.commit()
        tses = aws_access.session_for(await asyncio.to_thread(aws_access.assume, pid, "tp"))
        fname = cd["functions"]["echo"]["function_name"]
        pkg = (aws_access.deploy_dir(pid) / "work" / terraform.CODE_PACKAGES / "echo.zip").read_bytes()
        import io
        import zipfile
        src = zipfile.ZipFile(io.BytesIO(pkg))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for i in src.infolist():
                data = src.read(i)
                z.writestr(i.filename, data.replace(b'"queued": True', b'"queued": "edited in the console"') if i.filename == "handler.py" else data)
        lam = tses.client("lambda")
        await asyncio.to_thread(lambda: lam.update_function_code(FunctionName=fname, ZipFile=buf.getvalue()))
        await asyncio.to_thread(lambda: lam.get_waiter("function_updated").wait(FunctionName=fname))
        tsqs = tses.client("sqs")
        await asyncio.to_thread(lambda: tsqs.set_queue_attributes(QueueUrl=queue, Attributes={"VisibilityTimeout": "45"}))
        for _ in range(12):  # SQS attribute changes take a few seconds to show
            if (await asyncio.to_thread(lambda: tsqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["VisibilityTimeout"])))["Attributes"]["VisibilityTimeout"] == "45":
                break
            await asyncio.sleep(5)
        await drift.check(ctx("DRIFT"))
        rep = drift.load(pid)
        say(f"drift check: {drift.summary_line(rep)} · restore plan {(rep.get('restore') or {}).get('counts')} · "
            f"code diff {[(f['file'], f['change']) for c in rep['code'] for f in c['files']]}")
        say(f"  settings drift: {[(s['address'], [(f['key'], f['terraform'][:40], f['aws'][:40]) for f in s['fields']]) for s in rep['settings']]}")
        found = bool(rep["code"]) and any(s["address"] == "aws_sqs_queue.out" for s in rep["settings"])
        await drift.restore(ctx("RESTORE"))
        rep = drift.load(pid)
        vis = (await asyncio.to_thread(lambda: tsqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["VisibilityTimeout"])))["Attributes"]["VisibilityTimeout"]
        sha = (await asyncio.to_thread(lambda: lam.get_function_configuration(FunctionName=fname)))["CodeSha256"]
        restored = rep.get("status") == "restored" and vis == "30" and sha == de.load_packages(pid)["code"]["echo"]["fingerprint"]
        say(f"restored: {(rep.get('restored') or {}).get('line')} · queue visibility {vis} · code is Dev's package: "
            f"{sha == de.load_packages(pid)['code']['echo']['fingerprint']} {'✅' if restored else '❌'}")
        r = await post()
        say(f"after the restore: HTTPS POST → {r.status_code} {r.text[:80]}")
        ok = ok and found and restored and r.status_code == 200 and "edited in the console" not in r.text
        try:  # Quinn's role must not reach beyond the project
            await asyncio.to_thread(lambda: sqs.create_queue(QueueName=f"orkestra-deploycheck-{short}-sneaky"))
            say("FAIL: Quinn could create a queue")
            ok = False
        except Exception as exc:
            say(f"Quinn can't create queues: {type(exc).__name__} ✅")
    finally:
        say("tear down …")
        layer = f"orkestra-deploycheck-{short}-util"
        try:
            await tp.destroy(ctx("DESTROY"))
        except Exception as exc:  # noqa: BLE001
            say(f"destroy failed: {str(exc)[-700:]}")
            ok = False
        say(f"after tear down: deploy {aws_access.load(pid, 'deploy')['status']} · access {(aws_access.load(pid) or {}).get('status')}")
        try:
            left = await asyncio.to_thread(lambda: aws_access._session().client("lambda").list_layer_versions(LayerName=layer)["LayerVersions"])
            say(f"layer versions left for {layer}: {len(left)}" + (" ❌" if left else " ✅"))
            ok = ok and not left
        except Exception as exc:  # noqa: BLE001
            say(f"layer check: {type(exc).__name__}: {str(exc)[:120]}")
        say(f"audit trail: {len(aws_access.calls(pid))} records, e.g. {[(c['agent'], c['action']) for c in aws_access.calls(pid)[-8:]]}")
        async with SessionLocal() as db:
            await db.delete(await db.get(Project, pid))
            await db.commit()
        store.delete()
        say(f"scratch project deleted · RESULT: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
