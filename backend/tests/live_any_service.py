"""Live check that the crew can build the team's usual services beyond serverless (user, 10-03: "Orkestra must build ANY
AWS service"), with no LLM: a scratch project's hand-written Terraform with an own VPC (2 public subnets, internet gateway,
route table), security groups, an ALB → Lambda, an ECR repository, an ECS cluster + Fargate task definition + service
(0 tasks), an EC2 instance (t4g.nano with an SSM instance profile) and a container-image Lambda. It goes through Orion's
access draft → roles → plan → apply (placeholder code: the ALB answers 503) → Dev builds his Docker image and pushes it
to ECR, hands over his zip code → Terra's package plan creates the image function and swaps the zip in → Dev checks both
run exactly his packages → the ALB answers 200 with Dev's code, the image function answers when invoked → tear down.

AWS cost ≈ $0.02 (an ALB, a t4g.nano and 3 public IPv4 addresses for ~15 minutes). Everything is orkestra-* and tagged;
tear down runs even when a step fails. The ALB only accepts the host's own IP. Run ON THE HOST (terraform, Docker):
  bash remote/live_any_service.sh        (detached; follow /tmp/live_any_service.log)
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
import uuid

from app.tools.terraform import PACKAGES_TF

ROLE_TRUST = 'jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "%s" } }] })'

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
  default = "orkestra-anysvc-__ID__"
}
variable "names" {
  type = map(string)
  default = {
    vpc = "vpc", public_a = "public-a", public_b = "public-b", igw = "igw", public_rt = "public-rt", alb_sg = "alb-sg",
    app_sg = "app-sg", alb = "alb", tg = "tg", web = "web", web_role = "web-role", img = "img", img_role = "img-role",
    repo = "repo", cluster = "cluster", task = "task", task_exec = "task-exec", service = "svc", ec2 = "ec2", ec2_role = "ec2-role"
  }
}
variable "project_id" {
  type    = string
  default = "__PID__"
}
variable "permissions_boundary_arn" {
  type    = string
  default = "arn:aws:iam::144831534428:policy/orkestra-agent-boundary"
}
variable "caller_cidr" {
  type    = string
  default = "__MYIP__/32"
}
""",
    "infra/network.tf": """data "aws_availability_zones" "here" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.77.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name_prefix}-${var.names["vpc"]}" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.here.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name_prefix}-${var.names[count.index == 0 ? "public_a" : "public_b"]}" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.name_prefix}-${var.names["igw"]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = { Name = "${var.name_prefix}-${var.names["public_rt"]}" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-${var.names["alb_sg"]}"
  description = "ALB: HTTP from the Orkestra host only"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.caller_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app" {
  name        = "${var.name_prefix}-${var.names["app_sg"]}"
  description = "ECS tasks and the EC2 instance: no inbound"
  vpc_id      = aws_vpc.main.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
""",
    "infra/web.tf": """data "archive_file" "placeholder_web" {
  type        = "zip"
  output_path = "${path.module}/../build/placeholder-web.zip"
  source {
    filename = "handler.py"
    content  = "def handler(event, context):\\n    return {\\"statusCode\\": 503, \\"statusDescription\\": \\"503 Service Unavailable\\", \\"isBase64Encoded\\": False, \\"headers\\": {\\"Content-Type\\": \\"text/plain\\"}, \\"body\\": \\"placeholder\\"}\\n"
  }
}

resource "aws_iam_role" "web" {
  name                 = "${var.name_prefix}-${var.names["web_role"]}"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy   = __LAMBDA_TRUST__
}

resource "aws_iam_role_policy_attachment" "web_logs" {
  role       = aws_iam_role.web.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "web" {
  name              = "/aws/lambda/${var.name_prefix}-${var.names["web"]}"
  retention_in_days = 1
}

resource "aws_lambda_function" "web" {
  function_name    = "${var.name_prefix}-${var.names["web"]}"
  role             = aws_iam_role.web.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = lookup(var.code_packages, "web", data.archive_file.placeholder_web.output_path)
  source_code_hash = lookup(local.code_hashes, "web", data.archive_file.placeholder_web.output_base64sha256)
  timeout          = 10
  depends_on       = [aws_cloudwatch_log_group.web, aws_iam_role_policy_attachment.web_logs]
}

resource "aws_lb" "web" {
  name               = "${var.name_prefix}-${var.names["alb"]}"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "web" {
  name        = "${var.name_prefix}-${var.names["tg"]}"
  target_type = "lambda"
}

resource "aws_lambda_permission" "alb" {
  statement_id  = "AllowAlb"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.web.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.web.arn
}

resource "aws_lb_target_group_attachment" "web" {
  target_group_arn = aws_lb_target_group.web.arn
  target_id        = aws_lambda_function.web.arn
  depends_on       = [aws_lambda_permission.alb]
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.web.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}
""",
    "infra/containers.tf": """resource "aws_ecr_repository" "repo" {
  name                 = "${var.name_prefix}-${var.names["repo"]}"
  force_delete         = true
  image_tag_mutability = "MUTABLE"
}

resource "aws_iam_role" "img" {
  name                 = "${var.name_prefix}-${var.names["img_role"]}"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy   = __LAMBDA_TRUST__
}

resource "aws_iam_role_policy_attachment" "img_logs" {
  role       = aws_iam_role.img.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "img" {
  name              = "/aws/lambda/${var.name_prefix}-${var.names["img"]}"
  retention_in_days = 1
}

resource "aws_lambda_function" "img" {
  count         = contains(keys(var.image_packages), "img") ? 1 : 0
  function_name = "${var.name_prefix}-${var.names["img"]}"
  role          = aws_iam_role.img.arn
  package_type  = "Image"
  image_uri     = var.image_packages["img"]
  architectures = ["x86_64"]
  memory_size   = 256
  timeout       = 30
  depends_on    = [aws_cloudwatch_log_group.img, aws_iam_role_policy_attachment.img_logs]
}

resource "aws_ecs_cluster" "main" {
  name = "${var.name_prefix}-${var.names["cluster"]}"
}

resource "aws_iam_role" "task_exec" {
  name                 = "${var.name_prefix}-${var.names["task_exec"]}"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy   = __ECS_TRUST__
}

resource "aws_iam_role_policy_attachment" "task_exec" {
  role       = aws_iam_role.task_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${var.name_prefix}-${var.names["task"]}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.task_exec.arn
  skip_destroy             = true
  container_definitions    = jsonencode([{ name = "app", image = "${aws_ecr_repository.repo.repository_url}:placeholder", essential = true, portMappings = [{ containerPort = 8080, protocol = "tcp" }] }])
}

resource "aws_ecs_service" "app" {
  name            = "${var.name_prefix}-${var.names["service"]}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 0
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }
}
""",
    "infra/server.tf": """data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-*-arm64"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

resource "aws_iam_role" "ec2" {
  name                 = "${var.name_prefix}-${var.names["ec2_role"]}"
  permissions_boundary = var.permissions_boundary_arn
  assume_role_policy   = __EC2_TRUST__
}

resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.name_prefix}-${var.names["ec2_role"]}"
  role = aws_iam_role.ec2.name
}

resource "aws_instance" "worker" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = "t4g.nano"
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name
  volume_tags            = { created_by = "orkestra", project_id = var.project_id }
  tags                   = { Name = "${var.name_prefix}-${var.names["ec2"]}" }
  metadata_options {
    http_tokens = "required"
  }
  lifecycle {
    ignore_changes = [ami]
  }
}
""",
    "infra/outputs.tf": """locals {
  layer_names = {}
}

output "alb_url" {
  value = "http://${aws_lb.web.dns_name}/"
}
output "instance_id" {
  value = aws_instance.worker.id
}
output "ecs" {
  value = { cluster = aws_ecs_cluster.main.name, service = aws_ecs_service.app.name }
}
output "code_deploy" {
  value = {
    functions = {
      web = { function_name = aws_lambda_function.web.function_name, source_dir = "src/web", layers = [] }
    }
    layers = local.layer_names
    images = {
      img = { repository_url = aws_ecr_repository.repo.repository_url, source_dir = "src/img", platform = "linux/amd64",
      function_name = "${var.name_prefix}-${var.names["img"]}" }
    }
  }
}
""",
    "infra/packages.tf": PACKAGES_TF,
    "src/web/handler.py": """def handler(event, context):
    return {"statusCode": 200, "statusDescription": "200 OK", "isBase64Encoded": False,
            "headers": {"Content-Type": "text/plain"}, "body": "hello from Dev's code behind the ALB"}
""",
    "src/img/handler.py": """import platform


def lambda_handler(event, context):
    return {"from": "container image", "python": platform.python_version()}
""",
    "src/img/Dockerfile": """FROM public.ecr.aws/lambda/python:3.13
COPY handler.py ${LAMBDA_TASK_ROOT}/
CMD ["handler.lambda_handler"]
""",
}
for _k, _v in list(INFRA.items()):
    INFRA[_k] = (_v.replace("__LAMBDA_TRUST__", ROLE_TRUST % "lambda.amazonaws.com").replace("__ECS_TRUST__", ROLE_TRUST % "ecs-tasks.amazonaws.com")
                 .replace("__EC2_TRUST__", ROLE_TRUST % "ec2.amazonaws.com"))


def host_ip() -> str:
    """The host's public IPv4 (IMDSv2): the only address the scratch ALB accepts."""
    tok = urllib.request.urlopen(urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
                                                        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=3).read().decode()
    return urllib.request.urlopen(urllib.request.Request("http://169.254.169.254/latest/meta-data/public-ipv4",
                                                         headers={"X-aws-ec2-metadata-token": tok}), timeout=3).read().decode()


async def main() -> None:
    import httpx

    from sqlalchemy import select

    from app.agents import access as access_mod, de, tp
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
        p = Project(name="[scratch] any-service check", owner_id=owner_id, budget_usd=1.0, archived=True)
        db.add(p)
        await db.flush()
        for k in ("cto", "intake", "ba", "ta", "tp", "de", "qa"):
            db.add(AgentState(project_id=p.id, agent=k, status="waiting"))
        await db.commit()
        pid = p.id
    store = ProjectStore(pid)
    store.create("scratch")
    short, ip = uuid.uuid4().hex[:6], host_ip()
    files = {k: v.replace("__ID__", short).replace("__PID__", pid).replace("__MYIP__", ip) for k, v in INFRA.items()}
    for path, content in files.items():
        store.write(path, content)
    t0 = time.time()
    ctx = lambda label, **payload: JobContext(f"job_{label}", pid, payload, {}, owner_id)  # noqa: E731
    say = lambda s: print(f"[{time.time() - t0:6.0f}s] {s}", flush=True)  # noqa: E731
    results: dict[str, bool] = {}

    async def pending_gate() -> str | None:
        async with SessionLocal() as db:
            gate = (await db.execute(select(Approval).where(Approval.project_id == pid, Approval.stage == "deploy",
                                                            Approval.status == "pending"))).scalars().first()
            if gate:
                gate.status = "approved"  # this script answers the gate by calling the next step itself
                await db.commit()
            return gate.title if gate else None

    async def get(url: str, want: int) -> httpx.Response | None:
        r = None
        for _ in range(24):  # a new ALB's DNS and a new target take a minute or two
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(url)
                if r.status_code == want:
                    return r
            except httpx.HTTPError:
                pass
            await asyncio.sleep(10)
        return r

    try:
        problems = terraform.lint({k: v for k, v in files.items() if k.startswith("infra/")}, pid)
        say(f"lint: {problems or 'clean'}")
        results["lint clean"] = not problems
        await tp.handover(ctx("IAC"), {"summary": "any-service check", "changes": ""}, "check", [])
        draft = aws_access.load(pid, "access_draft")
        say(f"Orion's draft: prefix {draft['prefix']} · services {draft['services']} · problem {draft['problem']}")
        say(f"  running costs he points out: {draft.get('always_on')}")
        assert not draft["problem"], draft["problem"]
        await access_mod.grant(ctx("GRANT"))
        acc = aws_access.load(pid)
        say(f"roles: {[r['role'] for r in acc['roles']]} · state s3://{acc['state_bucket']}")
        await tp.deploy(ctx("PLAN", approved_infra=True))
        d = aws_access.load(pid, "deploy")
        say(f"plan: {d['plan']['counts']} · gate: {await pending_gate()}")
        await tp.apply(ctx("APPLY"))
        d = aws_access.load(pid, "deploy")
        results["infrastructure applied"] = d["status"] == "deployed"
        say(f"applied: status {d['status']} · error {(d.get('error') or '')[:600]}")
        assert d["status"] == "deployed"
        inv = inventory.load(pid) or {}
        say(f"AWS page: {inv.get('count')} resources in {[(s['label'], s['count']) for s in inv.get('services', [])]}")
        prim = [r for r in inv.get("resources", []) if r["primary"]]
        no_link = [r["address"] for r in prim if not r.get("console")]
        say(f"  console links: {len(prim) - len(no_link)}/{len(prim)} primary resources" + (f" (none for {no_link})" if no_link else ""))
        results["console links"] = not no_link
        out = d["outputs"]
        r = await get(out["alb_url"], 503)
        say(f"ALB → placeholder Lambda: {r.status_code if r else 'no answer'} {r.text[:60] if r else ''}")
        results["ALB answers (placeholder)"] = bool(r and r.status_code == 503)
        tcreds = await asyncio.to_thread(aws_access.assume, pid, "tp")
        tses = aws_access.session_for(tcreds)
        ec2 = tses.client("ec2")
        inst = (await asyncio.to_thread(lambda: ec2.describe_instances(InstanceIds=[out["instance_id"]])))["Reservations"][0]["Instances"][0]
        vols = [b["Ebs"]["VolumeId"] for b in inst.get("BlockDeviceMappings", [])]
        vtags = (await asyncio.to_thread(lambda: ec2.describe_volumes(VolumeIds=vols)))["Volumes"][0].get("Tags", []) if vols else []
        say(f"EC2 {inst['InstanceId']}: {inst['State']['Name']} · {inst['InstanceType']} · profile {inst.get('IamInstanceProfile', {}).get('Arn', '-').rsplit('/', 1)[-1]} "
            f"· volume tags {sorted(t['Key'] for t in vtags)}")
        results["EC2 running"] = inst["State"]["Name"] in ("pending", "running")
        ecs = tses.client("ecs")
        svc = (await asyncio.to_thread(lambda: ecs.describe_services(cluster=out["ecs"]["cluster"], services=[out["ecs"]["service"]])))["services"]
        say(f"ECS: cluster {out['ecs']['cluster']} · service {svc[0]['serviceName']} {svc[0]['status']} desired {svc[0]['desiredCount']} · "
            f"task definition {svc[0]['taskDefinition'].rsplit('/', 1)[-1]}")
        results["ECS service active"] = bool(svc) and svc[0]["status"] == "ACTIVE"
        # Dev: his Docker image to the project's ECR (his own role), his zip code; both handed to Terra
        dcreds = await asyncio.to_thread(aws_access.assume, pid, "de")
        work = terraform.prepare(aws_access.deploy_dir(pid) / "code_work", files)
        cd = out["code_deploy"]
        pushed = await asyncio.to_thread(de.push_images, pid, dcreds, work, cd, "v1")
        say(f"Dev built and pushed: {pushed}")
        results["image pushed to ECR"] = bool(pushed) and pushed[0]["built"] and "@sha256:" in pushed[0]["uri"]
        await asyncio.to_thread(de.check_fit, dcreds, cd, work)
        handed = de.hand_over(pid, work, [], cd, "v1")
        say(f"Dev handed over: code {[h['key'] for h in handed['code']]} · images {[h['key'] for h in handed['images']]}")
        dep = aws_access.load(pid, "deploy")
        aws_access.save(pid, {**dep, "intent": {"reason": "packages", "code": ["web"], "layers": [], "images": ["img"], "handover": handed,
                                                "tickets": [], "changes": "", "attempt": 0, "version": "v1"}}, "deploy")
        await tp.deploy(ctx("PLAN_PACKAGES"))
        d = aws_access.load(pid, "deploy")
        say(f"Terra's package plan: {d['plan']['counts']} · {[(c['address'], c['action']) for c in d['plan']['changes']]} · "
            f"wired {d['plan']['checks'].get('wired')} · gate: {await pending_gate()}")
        results["package plan creates the image function"] = any(c["address"].startswith("aws_lambda_function.img") and c["action"] == "create"
                                                                 for c in d["plan"]["changes"])
        results["package plan touches only the packages"] = {c["address"] for c in d["plan"]["changes"]} == {"aws_lambda_function.img[0]",
                                                                                                         "aws_lambda_function.web"}
        await tp.apply(ctx("APPLY_PACKAGES"))
        d = aws_access.load(pid, "deploy")
        say(f"after the apply: status {d['status']} · next job {runner_mod.runner.jobs[-1][0]} · error {(d.get('error') or '')[:600]}")
        live = await asyncio.to_thread(de.record_live, pid, dcreds, cd, "v1")
        say(f"Dev checked what Terra deployed: {live['changed']}")
        results["both functions run Dev's packages"] = "web" in json.dumps(live["functions"]) and "img" in live["images"]
        lam = aws_access.session_for(dcreds).client("lambda")
        res = await asyncio.to_thread(lambda: lam.invoke(FunctionName=cd["images"]["img"]["function_name"], Payload=b"{}"))
        body = json.loads(res["Payload"].read() or b"{}")
        say(f"image function invoked as Dev: {body}")
        results["image function answers"] = body.get("from") == "container image"
        r = await get(out["alb_url"], 200)
        say(f"ALB → Dev's code: {r.status_code if r else 'no answer'} {r.text[:60] if r else ''}")
        results["ALB answers with Dev's code"] = bool(r and r.status_code == 200 and "Dev's code" in r.text)
    except Exception as exc:  # noqa: BLE001
        say(f"STOPPED: {type(exc).__name__}: {str(exc)[:1500]}")
        results["finished"] = False
    finally:
        say("tear down …")
        for attempt in ("DESTROY", "DESTROY2"):  # once more on failure: a subnet can wait for the ALB's network interfaces
            try:
                await tp.destroy(ctx(attempt))
            except Exception as exc:  # noqa: BLE001
                say(f"destroy failed: {str(exc)[-700:]}")
            dep = aws_access.load(pid, "deploy") or {}
            if dep.get("status") == "destroyed":
                break
        say(f"after tear down: deploy {dep.get('status')} · access {(aws_access.load(pid) or {}).get('status')}")
        results["torn down"] = dep.get("status") == "destroyed"
        try:  # what's still tagged with this project (the tagging API lags a few minutes behind deletes)
            tag = aws_access._session().client("resourcegroupstaggingapi")
            left = await asyncio.to_thread(lambda: [x["ResourceARN"] for x in tag.get_resources(
                TagFilters=[{"Key": "project_id", "Values": [pid]}])["ResourceTagMappingList"]])
            say(f"still tagged with {pid} (may lag): {left or 'nothing'}")
        except Exception as exc:  # noqa: BLE001
            say(f"tag check: {type(exc).__name__}: {str(exc)[:120]}")
        if results["torn down"]:  # keep the scratch project (state, roles) when something is left, to finish by hand
            async with SessionLocal() as db:
                await db.delete(await db.get(Project, pid))
                await db.commit()
            store.delete()
        else:
            say(f"⚠️ NOT everything is gone: scratch project {pid} kept; finish with remote/finish_teardown.py {pid}")
        for k, v in results.items():
            say(f"  {'✅' if v else '❌'} {k}")
        say(f"RESULT: {'PASS' if results and all(results.values()) else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
