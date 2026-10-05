"""Terra's Terraform: checks without AWS, and real runs with an agent's own short-lived credentials.

lint(files)      static sandbox rules, always: region eu-west-1, default tags created_by/project_id, every literal resource
                 name starts with `orkestra-`, only allowed providers, no provisioners / external programs, IAM roles
                 carry the crew boundary, nothing account-wide.
validate(files)  `terraform fmt` + `init -backend=false` + `validate` when the terraform binary is installed on the
                 host (src/infra/scripts/remote/setup_terraform.sh). It runs with NO AWS credentials and the instance
                 metadata endpoint disabled, so it can't reach the account even by accident; providers come from the
                 public registry into a shared plugin cache.
prepare / build_layers / init / plan / apply / output / destroy
                 the real deploy, in <project>/deploy/work (infra/ next to Dev's src/ and layers/, as in the repo). Only
                 the credentials passed in (Terra's role, from services/aws_access) reach terraform; the instance
                 metadata endpoint stays disabled so it can never fall back to the platform role.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from app.core.config import get_settings

ALLOWED_PROVIDERS = {"hashicorp/aws", "hashicorp/archive", "hashicorp/random", "hashicorp/null"}
AWS_LOG_PATHS = ("/aws/lambda/", "/aws/apigateway/", "/aws/vendedlogs/", "/aws/states/")  # names AWS itself prefixes
NAME_ATTRS = ("name", "function_name", "queue_name", "bucket", "role_name", "layer_name", "topic_name", "table_name",
              "rest_api_name", "secret_name", "alarm_name", "name_prefix", "cluster_id", "replication_group_id", "identifier",
              "broker_name", "family", "cluster_identifier", "db_subnet_group_name", "subnet_group_name", "parameter_group_name",
              "load_balancer_name", "target_group_name", "repository_name", "schedule_name", "state_machine_name", "key_name")
FORBIDDEN = {  # resource type → why (the crew's AWS fence refuses these too; better to hear it before a deploy)
    "aws_api_gateway_account": "it changes an account-wide API Gateway setting shared with colleagues; skip API Gateway "
                               "execution logging or use access logs to an orkestra- log group",
    "aws_iam_policy": "the crew can't create managed policies: put the statements in an inline aws_iam_role_policy",
    "aws_iam_user": "no IAM users: the flow runs on roles", "aws_iam_group": "no IAM groups", "aws_iam_access_key": "no access keys",
    "aws_kms_key": "a customer KMS key costs $1/month and needs key admin rights: use AWS-managed encryption (SSE-SQS, SSE-S3)",
    "aws_ebs_encryption_by_default": "an account-wide EBS setting", "aws_ebs_default_kms_key": "an account-wide EBS setting",
    "aws_ec2_serial_console_access": "an account-wide EC2 setting", "aws_ec2_image_block_public_access": "an account-wide EC2 setting",
    "aws_ec2_instance_metadata_defaults": "an account-wide EC2 setting", "aws_s3_account_public_access_block": "an account-wide S3 setting",
    "aws_vpc_peering_connection_accepter": "it changes another VPC's peering", "aws_route53_zone": "Route 53 is global (outside eu-west-1)",
    "aws_cloudfront_distribution": "CloudFront is global (outside eu-west-1)", "aws_organizations_account": "account-level",
    "aws_guardduty_detector": "account-wide security service", "aws_securityhub_account": "account-wide security service",
    "aws_config_configuration_recorder": "account-wide AWS Config", "aws_cloudtrail": "account-wide audit trail",
}
# (aws_default_* resources take over the account's existing default VPC, subnets, security group…: refused too.)
# settings that keep "Tear down" working for everything Terra creates (a destroy must never stop half-way)
TEARDOWN = {"aws_ecr_repository": ("force_delete", "true", "images left in the repository would block terraform destroy"),
            "aws_s3_bucket": ("force_destroy", "true", "objects left in the bucket would block terraform destroy"),
            "aws_db_instance": ("skip_final_snapshot", "true", "a final snapshot would be left behind (and billed) after tear down"),
            "aws_rds_cluster": ("skip_final_snapshot", "true", "a final snapshot would be left behind (and billed) after tear down"),
            # AWS can't scope DeregisterTaskDefinition to one task definition, so the crew isn't allowed it (live check
            # 10-05: tear down stopped there); revisions are free and stay registered
            "aws_ecs_task_definition": ("skip_destroy", "true", "the crew can't deregister task definitions (AWS has no "
                                        "per-resource permission for it), so without it tear down fails; old revisions are free")}
LAYER_ZIP_DIR = "build/layers"  # Dev's layer zips, built from layers/<key>/ by his code deploy
# Terra deploys everything to AWS (user, 10-03: "the dev code and layers all need to be given to Terra by Dev, then Terra
# deploys them in real AWS ... then the entire codebase and layers come under one Terraform state"). Dev builds the
# packages (each function's code from its src/ folder, each layer from layers/<key>/) and hands them over; this file is
# the platform's, next to Terra's Terraform. Orkestra fills both maps at plan time, so every new package is a plan the
# user approves, and the functions run Terra's placeholder only until Dev's first package arrives.
PACKAGES_TF_PATH = "infra/packages.tf"
PACKAGES_TF = '''# Written by Orkestra: don't edit. Dev builds the packages and hands them over; Terra deploys them in a plan you approve.
# Functions take their code with lookup(var.code_packages, "<key>", <placeholder zip>) and attach layers through
# local.layer_arns. Layer names come from local.layer_names (Terra's).
variable "code_packages" {
  description = "Dev's code packages: function key => zip file (Orkestra sets it when it plans)"
  type        = map(string)
  default     = {}
}

variable "layer_packages" {
  description = "Dev's layer packages: layer key => zip file (Orkestra sets it when it plans)"
  type        = map(string)
  default     = {}
}

variable "image_packages" {
  description = "Dev's container images: image key => repository URL @ digest (Orkestra sets it when it plans)"
  type        = map(string)
  default     = {}
}

resource "aws_lambda_layer_version" "package" {
  for_each         = var.layer_packages
  layer_name       = local.layer_names[each.key]
  filename         = each.value
  source_code_hash = filebase64sha256(each.value)
  description      = "Built by Dev from layers/${each.key}/, published by Terra (Orkestra)"
}

locals {
  layer_arns  = { for k, v in aws_lambda_layer_version.package : k => v.arn }
  code_hashes = { for k, v in var.code_packages : k => filebase64sha256(v) }
}
'''
# 10-02 to 10-03 only: Terra published the layers, Dev still uploaded the code. Read for projects planned in that window.
LAYERS_TF_PATH = "infra/layers.tf"
LAYERS_TF = '''# Written by Orkestra: don't edit. Dev builds each layer package from layers/<key>/ and hands it over; this publishes it
# (a plan you approve) and the functions attach it through local.layer_arns. Names come from local.layer_names (Terra's).
variable "layer_packages" {
  description = "Dev's layer packages: key => zip file (Orkestra sets it when it plans)"
  type        = map(string)
  default     = {}
}

resource "aws_lambda_layer_version" "package" {
  for_each         = var.layer_packages
  layer_name       = local.layer_names[each.key]
  filename         = each.value
  source_code_hash = filebase64sha256(each.value)
  description      = "Built by Dev from layers/${each.key}/, published by Terra (Orkestra)"
}

locals {
  layer_arns = { for k, v in aws_lambda_layer_version.package : k => v.arn }
}
'''
PACKAGES_DIR = "packages"  # in the Terraform workspace: the packages Dev handed over (kept between runs)
CODE_PACKAGES, LAYER_PACKAGES = f"{PACKAGES_DIR}/code", f"{PACKAGES_DIR}/layers"
PACKAGES_TFVARS = "infra/packages.auto.tfvars.json"
LAYERS_TFVARS = "infra/layer_packages.auto.tfvars.json"  # with the 10-02 layers.tf
PLATFORM_FILES = (PACKAGES_TF_PATH, LAYERS_TF_PATH, PACKAGES_TFVARS, LAYERS_TFVARS)


def manages_layers(infra: dict[str, str]) -> bool:
    """Does this project's Terraform publish the layers (the platform's file), or does Dev (older projects)?"""
    return PACKAGES_TF_PATH in infra or LAYERS_TF_PATH in infra


def manages_code(infra: dict[str, str]) -> bool:
    """Does Terra deploy Dev's code too (the platform's packages.tf), or does Dev upload it himself (older projects)?"""
    return PACKAGES_TF_PATH in infra


def lint(files: dict[str, str], project_id: str) -> list[str]:
    problems: list[str] = []
    tf = {p: c for p, c in files.items() if p.endswith((".tf", ".tfvars"))}
    if not tf:
        return ["no .tf files under infra/"]
    allc = "\n".join(tf.values())
    if not re.search(r'region\s*=\s*"eu-west-1"', allc) and not re.search(r'default\s*=\s*"eu-west-1"', allc):
        problems.append('the AWS provider must use region "eu-west-1" (the account denies every other region)')
    if "default_tags" not in allc or "created_by" not in allc or "project_id" not in allc:
        problems.append("the AWS provider needs default_tags with created_by = \"orkestra\" and project_id (sandbox rule)")
    if project_id not in allc:
        problems.append(f"tag every resource with project_id = \"{project_id}\" (e.g. in default_tags)")
    for src in re.findall(r'source\s*=\s*"([^"]+)"', allc):
        if "/" in src and not src.startswith((".", "/")) and src.count("/") == 1 and src not in ALLOWED_PROVIDERS:
            problems.append(f"provider {src!r} isn't allowed here; use only {sorted(ALLOWED_PROVIDERS)}")
    if re.search(r'\bprovisioner\s+"', allc) or re.search(r'data\s+"external"', allc):
        problems.append("no provisioners or external programs: Terraform must only declare AWS resources")
    for path, content in tf.items():
        for attr, value in re.findall(rf'^\s*({"|".join(NAME_ATTRS)})\s*=\s*"([^"]*)"', content, re.M):
            if attr.endswith("group_name") and value.startswith("default"):  # AWS's own default parameter/subnet groups: used, not changed
                continue
            name = value
            for service_path in AWS_LOG_PATHS:  # e.g. /aws/lambda/<function name>: AWS's convention, judged by the rest
                if name.startswith(service_path):
                    name = name[len(service_path):]
                    break
            if "${" in name:
                head = name.split("${", 1)[0]
                if head and not head.startswith("orkestra-"):
                    problems.append(f"{path}: {attr} = \"{value}\" must start with orkestra-")
            elif name and not name.startswith("orkestra-") and attr != "name_prefix" and not _not_a_resource_name(content, value):
                problems.append(f"{path}: {attr} = \"{value}\" must start with orkestra- (sandbox naming rule)")
    for m in re.finditer(r'variable\s+"(name_prefix|prefix)"\s*{[^}]*default\s*=\s*"([^"]*)"', allc, re.S):
        if not m.group(2).startswith("orkestra-"):
            problems.append(f'variable "{m.group(1)}" must default to a value starting with orkestra-')
    if not re.search(r'variable\s+"names"\s*{', allc):
        problems.append('put every resource name in variable "names" (a map: short key → the name after the prefix) in '
                        'infra/names.tf, and build each name as "${var.name_prefix}-${var.names["key"]}": the user renames '
                        "resources there")
    for rtype, why in FORBIDDEN.items():
        if re.search(rf'resource\s+"{rtype}"\s', allc):
            problems.append(f"{rtype} isn't allowed: {why}")
    for rtype in sorted(set(re.findall(r'resource\s+"(aws_default_[a-z0-9_]+)"\s', allc))):
        problems.append(f"{rtype} isn't allowed: it takes over the account's existing default resources, shared with colleagues; "
                        "create your own (orkestra-*) or reference existing ones read-only with a data source")
    for rtype, (attr, value, why) in TEARDOWN.items():
        for name, body in _blocks(allc, rtype):
            if not re.search(rf"^\s*{attr}\s*=\s*{value}\b", body, re.M):
                problems.append(f"{rtype}.{name} needs {attr} = {value}: {why}")
    for name, body in _blocks(allc, "aws_db_instance") + _blocks(allc, "aws_rds_cluster"):
        if re.search(r"^\s*deletion_protection\s*=\s*true", body, re.M):
            problems.append(f"{name}: deletion_protection = true would block tear down; leave it false in this sandbox")
    problems += _log_write_scope(allc)
    for name, body in _blocks(allc, "aws_instance"):  # "the newest AMI" changes weekly: every later plan would rebuild the server
        ignored = re.search(r"ignore_changes\s*=\s*\[([^\]]*)\]", body)
        if re.search(r"^\s*ami\s*=\s*data\.aws_ami\.", body, re.M) and not (ignored and re.search(r"\bami\b", ignored.group(1))):
            problems.append(f"aws_instance.{name} takes the newest AMI from data.aws_ami: add lifecycle {{ ignore_changes = [ami] }}, "
                            "or every later plan (even one that only deploys code) replaces the instance when AWS publishes a new image")
    for name, body in _blocks(allc, "aws_iam_role"):
        if "permissions_boundary" not in body:
            problems.append(f'aws_iam_role.{name} needs permissions_boundary = var.permissions_boundary_arn '
                            '(arn:aws:iam::144831534428:policy/orkestra-agent-boundary); AWS refuses crew roles without it')
    allowed_managed = ("arn:aws:iam::aws:policy/service-role/", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
                       "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy", "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly")
    for arn in re.findall(r'policy_arn\s*=\s*"([^"]+)"', allc):
        if not arn.startswith(allowed_managed):
            problems.append(f'policy_arn "{arn}": only AWS-managed service-role policies (arn:aws:iam::aws:policy/service-role/…), '
                            "AmazonSSMManagedInstanceCore, CloudWatchAgentServerPolicy or AmazonEC2ContainerRegistryReadOnly may be "
                            "attached; put anything else in an inline aws_iam_role_policy")
    if not re.search(r'backend\s+"s3"', allc):
        problems.append('state must use backend "s3" {} (partial config; the deploy step fills in the bucket)')
    if re.search(r'^\s*dynamodb_table\s*=', allc, re.M):
        problems.append("no dynamodb_table for state locking: use use_lockfile = true (S3-native locking)")
    problems += code_later("\n".join(c for p, c in tf.items() if p not in PLATFORM_FILES), manages_layers(tf), manages_code(tf))
    return problems


def code_later(mine: str, layers_tf: bool = True, code_tf: bool = False) -> list[str]:
    """Infrastructure comes first: functions start with placeholder code, so the Terraform never packages src/ or
    layers/ itself. Dev builds the packages; the platform's packages.tf deploys them (`code_tf`): each function takes
    lookup(var.code_packages, "<key>", <placeholder>) and attaches layers with local.layer_arns. Older projects: Dev
    uploads the code, so the functions ignore filename/source_code_hash. `mine` is Terra's own files (no platform file)."""
    problems: list[str] = []
    if re.search(r'resource\s+"aws_lambda_layer_version"\s', mine):
        problems.append("don't declare aws_lambda_layer_version yourself: the platform's infra/packages.tf publishes the packages Dev "
                        "hands over. Give their names in locals { layer_names = { <key> = \"${var.name_prefix}-${var.names[\"<key>_layer\"]}\" } } "
                        "and attach them with local.layer_arns")
    if re.search(r'\.\./(src|layers)/|build/layers/', mine):
        problems.append("the Terraform must not package Dev's src/ or layers/ itself: give each function placeholder code from a "
                        "data \"archive_file\" with an inline source block" + (" and take Dev's package with lookup(var.code_packages, "
                                                                                "\"<key>\", <placeholder zip>)" if code_tf else ""))
    functions = _blocks(mine, "aws_lambda_function")
    deploy_keys = _code_deploy_keys(mine)
    for name, body in functions:
        m = re.search(r"ignore_changes\s*=\s*\[([^\]]*)\]", body)
        if code_tf and re.search(r'^\s*package_type\s*=\s*"Image"', body, re.M):  # a container-image function: Dev's image
            if not re.search(r"^\s*image_uri\s*=.*var\.image_packages", body, re.M):
                problems.append(f"aws_lambda_function.{name} is a container image: image_uri = var.image_packages[\"<key>\"] (Dev's image, "
                                "pushed to this project's ECR repository), with count = contains(keys(var.image_packages), \"<key>\") ? 1 : 0 "
                                "so it's created once his first image arrives")
            if m and re.search(r"\bimage_uri\b", m.group(1)):
                problems.append(f"aws_lambda_function.{name}: don't ignore image_uri: Terra deploys each new image of Dev's through the plan")
            continue
        if code_tf:
            ignored = [a for a in ("filename", "source_code_hash") if m and re.search(rf"\b{a}\b", m.group(1))]
            if ignored:
                problems.append(f"aws_lambda_function.{name}: don't ignore {', '.join(ignored)}: Terra deploys Dev's code through "
                                "Terraform now (var.code_packages), so the plan must see each new package")
            fk = re.search(r'^\s*filename\s*=\s*lookup\(\s*var\.code_packages\s*,\s*"([^"]+)"', body, re.M)
            hk = re.search(r'^\s*source_code_hash\s*=\s*lookup\(\s*local\.code_hashes\s*,\s*"([^"]+)"', body, re.M)
            if not fk or not hk:
                problems.append(f"aws_lambda_function.{name}: take Dev's package when he has handed it over, else the placeholder: "
                                f"filename = lookup(var.code_packages, \"<key>\", data.archive_file.<placeholder>.output_path) and "
                                f"source_code_hash = lookup(local.code_hashes, \"<key>\", data.archive_file.<placeholder>.output_base64sha256), "
                                "with <key> = the function's key in output \"code_deploy\"")
            elif fk.group(1) != hk.group(1):
                problems.append(f"aws_lambda_function.{name}: filename and source_code_hash look up different keys "
                                f"({fk.group(1)!r} vs {hk.group(1)!r}): use the same key")
            elif deploy_keys is not None and fk.group(1) not in deploy_keys:
                problems.append(f"aws_lambda_function.{name} looks up code_packages[{fk.group(1)!r}], but output \"code_deploy\" has "
                                f"no function {fk.group(1)!r} ({', '.join(sorted(deploy_keys)) or 'none'}): use the same key in both")
        else:
            missing = [a for a in ("filename", "source_code_hash") if not m or not re.search(rf"\b{a}\b", m.group(1))]
            if missing:
                problems.append(f"aws_lambda_function.{name} needs lifecycle {{ ignore_changes = [filename, source_code_hash] }} "
                                f"(missing {', '.join(missing)}): Dev deploys the real code into it later")
        if m and re.search(r"\blayers\b", m.group(1)):
            problems.append(f"aws_lambda_function.{name}: don't ignore `layers` any more: the function attaches Dev's packages "
                            "itself, layers = [for k in [\"<key>\", …] : local.layer_arns[k] if contains(keys(local.layer_arns), k)]")
        elif re.search(r"^\s*layers\s*=", body, re.M) and "local.layer_arns" not in body:
            problems.append(f"aws_lambda_function.{name}: attach layers only through local.layer_arns (the published packages)")
    if functions and not re.search(r'output\s+"code_deploy"\s*{', mine):
        problems.append('add output "code_deploy" = { functions = { <key> = { function_name, source_dir, layers } }, layers = '
                        '{ <key> = <layer name> } }: Dev builds and hands over his packages by it')
    if layers_tf and not re.search(r"\blayer_names\s*=", mine):
        problems.append("define locals { layer_names = { <key> = <layer name> } } for every layer key (an empty map {} when the flow "
                        "has no layers): the platform's layers.tf publishes each of Dev's packages under that name")
    return problems


def _statement_around(text: str, i: int) -> str:
    """The innermost `{ … }` (an IAM statement in jsonencode) that contains position i."""
    depth, j = 0, i
    while j > 0:
        j -= 1
        if text[j] == "}":
            depth += 1
        elif text[j] == "{":
            if depth == 0:
                return "{" + _body(text, j + 1) + "}"
            depth -= 1
    return text[max(0, i - 400):i + 400]


def _log_write_scope(text: str) -> list[str]:
    """logs:CreateLogStream / PutLogEvents are checked against the log STREAMS: a resource that names only the log group
    (".../log-group:<name>", or aws_cloudwatch_log_group.<x>.arn, which has no ":*" since AWS provider v4) lets the function
    run while every log line is silently dropped (10-05: a Lambda wrote its S3 files with nothing in CloudWatch)."""
    problems, seen = [], set()
    if "AWSLambdaBasicExecutionRole" in text:  # the AWS-managed policy grants it correctly
        return []
    for m in re.finditer(r"logs:(PutLogEvents|CreateLogStream)", text):
        st = _statement_around(text, m.start())
        if st in seen:
            continue
        seen.add(st)
        r = re.search(r"Resource\s*[=:]\s*(\[[^\]]*\]|[^\n,]+)", st)
        if not r:
            continue
        res = r.group(1)
        names_group = re.search(r"aws_cloudwatch_log_group\.[\w-]+\.arn", res) or "log-group:" in res
        if names_group and ":*" not in res and "log-stream" not in res:
            problems.append("an IAM policy gives logs:CreateLogStream/PutLogEvents on the log group only: AWS checks them against the "
                            "log streams, so the function would run with every log line dropped. Use \"${aws_cloudwatch_log_group.<name>.arn}:*\" "
                            "(or attach service-role/AWSLambdaBasicExecutionRole)")
    return problems[:1]


def _blocks(text: str, rtype: str) -> list[tuple[str, str]]:
    """[(name, body)] of every `resource "<rtype>" "<name>" { … }` (brace-matched, so nested blocks are included)."""
    return [(m.group(1), _body(text, m.end())) for m in re.finditer(rf'resource\s+"{rtype}"\s+"([^"]+)"\s*{{', text)]


def _body(text: str, start: int) -> str:
    """The text between the `{` that ends just before `start` and its matching `}`."""
    depth, i = 1, start
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start:i - 1]


def _code_deploy_keys(text: str) -> set[str] | None:
    """The function keys in output "code_deploy" (functions = { <key> = { … } }), or None when it can't be read."""
    m = re.search(r'output\s+"code_deploy"\s*{', text)
    f = re.search(r"\bfunctions\s*=\s*{", _body(text, m.end())) if m else None
    if not f:
        return None
    inner = _body(_body(text, m.end()), f.end())
    keys, depth = set(), 0
    for tok in re.finditer(r'[{}]|^\s*"?([A-Za-z0-9_-]+)"?\s*=', inner, re.M):
        if tok.group(0) == "{":
            depth += 1
        elif tok.group(0) == "}":
            depth -= 1
        elif depth == 0 and tok.group(1):
            keys.add(tok.group(1))
    return keys or None  # e.g. a `for` expression: Dev's hand-over check catches a mismatch instead


def _not_a_resource_name(content: str, value: str) -> bool:
    """`name = "..."` also appears in tags, env vars and nested blocks; only flag it inside a resource's top level."""
    idx = content.find(f'"{value}"')
    before = content[:idx]
    depth = before.count("{") - before.count("}")
    return depth != 1  # top-level attribute of a resource/data block has brace depth 1


def available() -> bool:
    return shutil.which("terraform") is not None


async def _run(args: list[str], cwd: Path, timeout: int, creds: dict | None = None, full: bool = False) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env.update({"AWS_EC2_METADATA_DISABLED": "true", "TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "CHECKPOINT_DISABLE": "1",
                "TF_PLUGIN_CACHE_DIR": str(get_settings().data_dir / "tf-plugin-cache")})
    env.update(creds or {})
    Path(env["TF_PLUGIN_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec("terraform", *args, cwd=cwd, env=env,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"terraform {args[0]} timed out after {timeout}s"
    text = out.decode(errors="replace")
    return proc.returncode, text if full else text[-8000:]


async def validate(files: dict[str, str], code: dict[str, str] | None = None) -> dict:
    """{available, ok, diagnostics:[...], formatted:{path: content}} — in a throwaway copy of the project (infra/ next
    to src/ and layers/, as in a real deploy). The files are run through `terraform fmt` first (the formatted text is
    returned and kept), so only real errors go back to the agent, not whitespace."""
    if not available():
        return {"available": False, "ok": True, "diagnostics": [], "formatted": files,
                "note": "terraform isn't installed on this host; static checks only"}
    root = Path(tempfile.mkdtemp(prefix="tf-", dir=get_settings().data_dir))
    work = root / "infra"
    try:
        for p, c in {**(code or {}), **files}.items():
            f = root / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(c, encoding="utf-8")
        work.mkdir(exist_ok=True)
        code_fmt, fmt_out = await _run(["fmt", "-recursive", "-no-color"], work, 60)
        if code_fmt != 0:  # fmt only fails on syntax errors
            return {"available": True, "ok": False, "formatted": files, "diagnostics": [f"syntax error: {fmt_out[-1500:]}"]}
        formatted = {p: (root / p).read_text(encoding="utf-8") for p in files}
        code_init, init_out = await _run(["init", "-backend=false", "-input=false", "-no-color"], work, 240)
        if code_init != 0:
            return {"available": True, "ok": False, "formatted": formatted, "diagnostics": [f"terraform init failed: {init_out[-1500:]}"]}
        code_val, val_out = await _run(["validate", "-json", "-no-color"], work, 120, full=True)
        try:
            v = json.loads(val_out)
            diags = [f"{d.get('severity')}: {d.get('summary')} {d.get('detail', '')}".strip()
                     + (f" ({d['range']['filename']}:{d['range']['start']['line']})" if d.get("range") else "")
                     for d in v.get("diagnostics", [])]
            ok = v.get("valid", False)
        except ValueError:
            diags, ok = [val_out[-1500:]], code_val == 0
        errors = [d for d in diags if d.startswith("error")]
        return {"available": True, "ok": ok and not errors, "formatted": formatted, "diagnostics": diags,
                "reformatted": [p for p in files if formatted[p] != files[p]]}
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── real runs ──────────────────────────────────────────────────────────────────────────────────────────────────────
class TerraformError(Exception):
    pass


KEEP = (".terraform", "build", PACKAGES_DIR)  # provider downloads, built layer zips and handed-over packages survive


def prepare(work: Path, files: dict[str, str]) -> Path:
    """Sync the project's infra/, src/ and layers/ into the persistent workspace. Unchanged files keep their mtime,
    so Terraform's zips (and so the plan) only change when the code really changed."""
    work.mkdir(parents=True, exist_ok=True)
    for f in [p for p in work.rglob("*") if p.is_file()]:
        rel = f.relative_to(work)
        if rel.parts[0] in KEEP or (len(rel.parts) > 1 and rel.parts[1] in KEEP) or f.name in (".terraform.lock.hcl", "tfplan"):
            continue
        if rel.as_posix() not in files:
            f.unlink()
    for rel, content in files.items():
        f = work / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists() or f.read_text(encoding="utf-8", errors="replace") != content:
            f.write_text(content, encoding="utf-8")
    return work


IMAGES_FILE = "images.json"  # in the packages folder: image key → repository URL @ digest (images live in ECR, not on disk)


def handed_over(packages: Path) -> dict[str, dict[str, str]]:
    """{"code": {key: path from infra/}, "layers": {…}, "images": {key: uri@digest}}: what Dev handed over to Terra."""
    def zips(folder: Path) -> dict[str, str]:
        return {p.stem: f"../{p.relative_to(packages.parent).as_posix()}" for p in sorted(folder.glob("*.zip"))} if folder.is_dir() else {}
    try:
        images = json.loads((packages / IMAGES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        images = {}
    return {"code": zips(packages / "code"), "layers": {**zips(packages), **zips(packages / "layers")}, "images": images}  # flat: 10-02


def package_vars(infra: dict[str, str], packages: Path) -> dict[str, str]:
    """The infra files for a real run, plus the tfvars that point the platform's file at the packages Dev handed over
    (every plan gets them all: a plan without a package would put the placeholder back or remove that layer)."""
    infra = {p: c for p, c in infra.items() if p not in (PACKAGES_TFVARS, LAYERS_TFVARS)}
    have = handed_over(packages)
    if manages_code(infra):
        declared = "image_packages" in infra.get(PACKAGES_TF_PATH, "")  # a project planned before images existed
        return {**infra, PACKAGES_TFVARS: json.dumps({"code_packages": have["code"], "layer_packages": have["layers"],
                                                      **({"image_packages": have["images"]} if declared else {})}, indent=2)}
    if manages_layers(infra):
        return {**infra, LAYERS_TFVARS: json.dumps({"layer_packages": have["layers"]}, indent=2)}
    return infra


def runtime_of(work: Path) -> tuple[str, str]:
    """(python version, wheel architecture) of the functions in the Terraform."""
    tf = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (work / "infra").glob("*.tf"))
    arch = "aarch64" if re.search(r'architectures\s*=\s*\[\s*"arm64"', tf) else "x86_64"
    m = re.search(r'runtime\s*=\s*"python(3\.\d+)"', tf)
    return (m.group(1) if m else "3.14"), arch


async def build_layers(work: Path) -> list[dict]:
    """Layers: every layers/<name>/ with a requirements.txt (package layer: Linux wheels for the Lambda runtime and
    architecture in the Terraform) and/or a python/ folder (code layer) → build/layers/<name>.zip (python/…). Rebuilt
    only when the inputs change (`key` is that fingerprint); the zip is byte-for-byte reproducible (fixed timestamps,
    no .pyc)."""
    py, arch = runtime_of(work)
    built = []
    folders = sorted({p.parent for p in (work / "layers").glob("*/requirements.txt")} | {p.parent for p in (work / "layers").glob("*/python")})
    for folder in folders:
        name = folder.name
        req = folder / "requirements.txt"
        out_dir = work / LAYER_ZIP_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        zpath, stamp = out_dir / f"{name}.zip", out_dir / f"{name}.stamp"
        code_dir = folder / "python"
        key_src = (req.read_text(encoding="utf-8") if req.exists() else "") + py + arch
        if code_dir.exists():
            key_src += "".join(f"{p.relative_to(code_dir)}:{p.read_text(encoding='utf-8', errors='replace')}"
                               for p in sorted(code_dir.rglob("*")) if p.is_file())
        key = hashlib.sha256(key_src.encode()).hexdigest()
        if zpath.exists() and stamp.exists() and stamp.read_text() == key:
            built.append({"layer": name, "zip": f"{LAYER_ZIP_DIR}/{name}.zip", "kb": zpath.stat().st_size // 1024, "cached": True,
                          "key": key, "python": py, "arch": arch})
            continue
        staging = Path(tempfile.mkdtemp(prefix=f"layer-{name}-", dir=get_settings().data_dir))
        try:
            target = staging / "python"
            target.mkdir(parents=True)
            if req.exists() and req.read_text(encoding="utf-8").strip():
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "pip", "install", "--quiet", "--no-cache-dir", "--no-compile", "--target", str(target),
                    "--platform", f"manylinux_2_28_{arch}", "--platform", f"manylinux2014_{arch}", "--implementation", "cp",
                    "--python-version", py, "--only-binary=:all:", "-r", str(req),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                try:
                    out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise TerraformError(f"layer {name}: pip timed out")
                if proc.returncode:
                    raise TerraformError(f"layer {name}: couldn't fetch Linux wheels for Python {py} ({arch}): {out.decode(errors='replace')[-1500:]}")
            if code_dir.exists():
                shutil.copytree(code_dir, target, dirs_exist_ok=True)
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(p for p in staging.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
                    info = zipfile.ZipInfo(f.relative_to(staging).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                    info.external_attr = 0o644 << 16
                    z.writestr(info, f.read_bytes(), zipfile.ZIP_DEFLATED)
            stamp.write_text(key)
            built.append({"layer": name, "zip": f"{LAYER_ZIP_DIR}/{name}.zip", "kb": zpath.stat().st_size // 1024, "cached": False,
                          "key": key, "python": py, "arch": arch})
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return built


SHIP_EXCLUDE = re.compile(r"(^|/)(tests?/|conftest\.py$|test_[^/]*\.py$|[^/]*_test\.py$|\.pytest_cache/|__pycache__/)|\.(pyc|md)$")


def zip_folder(folder: Path) -> bytes:
    """A function's code package: only its runtime code, at the zip root (tests, docs and caches never ship to AWS:
    they live in the project's Code view). Reproducible: same code → same bytes → same CodeSha256, so an unchanged
    function is never re-uploaded."""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p for p in folder.rglob("*") if p.is_file() and not SHIP_EXCLUDE.search(p.relative_to(folder).as_posix())):
            info = zipfile.ZipInfo(f.relative_to(folder).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            z.writestr(info, f.read_bytes(), zipfile.ZIP_DEFLATED)
    return buf.getvalue()


async def init(work: Path, bucket: str, key: str, creds: dict) -> None:
    code, out = await _run(["init", "-input=false", "-no-color", "-reconfigure", f"-backend-config=bucket={bucket}",
                            f"-backend-config=key={key}", "-backend-config=region=eu-west-1", "-backend-config=encrypt=true",
                            "-backend-config=use_lockfile=true"], work / "infra", 300, creds)
    if code:
        raise TerraformError("terraform init failed: " + out[-2500:])


def _name(change: dict) -> str:
    for side in (change.get("after") or {}, change.get("before") or {}):
        for k in ("function_name", "name", "queue_name", "bucket", "layer_name", "role", "stage_name", "path_part", "http_method",
                  "statement_id", "log_group_name"):
            if isinstance(side.get(k), str) and side[k]:
                return side[k]
    return ""


async def plan(work: Path, creds: dict, destroy: bool = False, out_file: str = "tfplan") -> dict:
    """terraform plan -out=<out_file>, then a readable summary: [{address, type, name, action}] and counts."""
    code, out = await _run(["plan", "-input=false", "-no-color", f"-out={out_file}", *(["-destroy"] if destroy else [])],
                           work / "infra", 900, creds)
    if code:
        raise TerraformError("terraform plan failed: " + out[-3000:])
    code, shown = await _run(["show", "-json", "-no-color", out_file], work / "infra", 120, creds, full=True)
    if code:
        raise TerraformError("terraform show failed: " + shown[-1500:])
    data = json.loads(shown)
    changes = []
    for rc in data.get("resource_changes", []):
        actions = rc["change"]["actions"]
        if actions in (["no-op"], ["read"]):
            continue
        action = "replace" if "create" in actions and "delete" in actions else actions[0]
        before, after = rc["change"].get("before") or {}, rc["change"].get("after") or {}
        diff = sorted(k for k in set(before) | set(after) if action == "update" and before.get(k) != after.get(k)
                      and k not in ("tags_all", "last_modified"))[:12]
        if action == "replace":  # what forces the new resource (e.g. "ami" for an instance): the user sees why
            diff = sorted({str(p[0]) for p in rc["change"].get("replace_paths") or [] if p})[:12]
        changes.append({"address": rc["address"], "type": rc["type"], "name": _name(rc["change"]), "action": action,
                        **({"fields": diff} if diff else {})})
    counts = {a: sum(c["action"] == a for c in changes) for a in ("create", "update", "replace", "delete")}
    return {"changes": changes, "counts": counts, "log": out[-4000:], "config_keys": config_keys(data)}


def config_keys(plan_json: dict) -> dict[str, list[str]]:
    """resource address (without index) → the settings written in the Terraform (attributes and nested blocks); the
    rest of a resource's settings are AWS or provider defaults."""
    out: dict[str, list[str]] = {}
    for r in (plan_json.get("configuration") or {}).get("root_module", {}).get("resources", []):
        if r.get("mode") == "managed":
            out[r["address"]] = sorted(r.get("expressions", {}).keys())
    return out


async def state(work: Path, creds: dict) -> list[dict]:
    """The resources as they are in AWS after an apply: [{address, type, name, values, sensitive}] (terraform show)."""
    code, out = await _run(["show", "-json", "-no-color"], work / "infra", 180, creds, full=True)
    if code:
        raise TerraformError("terraform show failed: " + out[-1500:])
    data = json.loads(out or "{}")
    res = []

    def walk(mod: dict) -> None:
        for r in mod.get("resources", []):
            if r.get("mode") == "managed":
                res.append({"address": r["address"], "type": r["type"], "name": r.get("name"), "index": r.get("index"),
                            "values": r.get("values") or {}, "sensitive": r.get("sensitive_values") or {}})
        for child in mod.get("child_modules", []):
            walk(child)
    walk((data.get("values") or {}).get("root_module") or {})
    return res


DRIFT_IGNORE = {"layers", "last_modified", "source_code_hash", "code_sha256", "source_code_size", "version", "qualified_arn",
                "qualified_invoke_arn", "tags_all", "filename"}  # Dev's code deploys change these by design (older projects)
# Terra deploys the code and layers (packages.tf): a changed `layers` list is drift; the code itself is compared with Dev's
# package separately (CodeSha256), since the provider doesn't always notice a console edit
CODE_FIELDS = {"last_modified", "source_code_hash", "code_sha256", "source_code_size", "version", "qualified_arn",
               "qualified_invoke_arn", "tags_all", "filename"}


def _short(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False)
    return s if len(s) <= 400 else s[:397] + "…"


def _unjson(v):
    """Parse JSON hidden in strings (an IAM policy is a JSON string inside a list of objects), recursively."""
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return _unjson(json.loads(v))
        except ValueError:
            return v
    if isinstance(v, dict):
        return {k: _unjson(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_unjson(x) for x in v]
    return v


def changed_lines(before, after, limit: int = 16) -> list[str]:
    """Only the lines that differ between two values, readable (10-05: two 400-character policy blobs hid the one change,
    a `:*` at the end): '- ' Terraform, '+ ' AWS. Empty for plain values (the two values say it)."""
    import difflib

    a, b = _unjson(before), _unjson(after)
    if not isinstance(a, (dict, list)) and not isinstance(b, (dict, list)):
        return []
    pa = json.dumps(a, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    pb = json.dumps(b, indent=1, sort_keys=True, ensure_ascii=False).splitlines()
    out = [ln[0] + " " + ln[1:].strip() for ln in difflib.unified_diff(pa, pb, lineterm="", n=0)
           if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    return out[:limit]


async def live(work: Path, creds: dict, ignore: set[str] | None = None, details: dict | None = None) -> tuple[list[dict], dict[str, list[str]]]:
    """Read what's in AWS now, without writing anything: `plan -refresh-only` (state untouched), then its refreshed
    `prior_state` → rows like state(), and the drift: address → settings changed outside Terraform (e.g. in the console).
    `details` (a dict to fill): address → {type, name, deleted, fields: [{key, terraform, aws}]}: what changed, both values."""
    ignore = DRIFT_IGNORE if ignore is None else ignore
    plan_file = "refresh.tfplan"
    try:
        code, out = await _run(["plan", "-refresh-only", "-input=false", "-no-color", f"-out={plan_file}"], work / "infra", 600, creds)
        if code:
            raise TerraformError("terraform plan -refresh-only failed: " + out[-2000:])
        code, shown = await _run(["show", "-json", "-no-color", plan_file], work / "infra", 120, creds, full=True)
        if code:
            raise TerraformError("terraform show failed: " + shown[-1500:])
    finally:
        (work / "infra" / plan_file).unlink(missing_ok=True)
    data = json.loads(shown)
    rows: list[dict] = []

    def walk(mod: dict) -> None:
        for r in mod.get("resources", []):
            if r.get("mode") == "managed":
                rows.append({"address": r["address"], "type": r["type"], "name": r.get("name"), "index": r.get("index"),
                             "values": r.get("values") or {}, "sensitive": r.get("sensitive_values") or {}})
        for child in mod.get("child_modules", []):
            walk(child)
    walk(((data.get("prior_state") or {}).get("values") or {}).get("root_module") or {})
    drift: dict[str, list[str]] = {}

    def same(a, b) -> bool:  # null vs [] / {} / "" is the provider's bookkeeping, not a change in AWS (10-05: a layer's
        return a == b or (a in (None, [], {}, "") and b in (None, [], {}, ""))  # compatible_runtimes showed as drift)
    for d in data.get("resource_drift", []):
        before, after = d["change"].get("before") or {}, d["change"].get("after") or {}
        keys = sorted(k for k in set(before) | set(after) if not same(before.get(k), after.get(k)) and k not in ignore)
        if after and not keys:
            continue
        if keys or not after:
            drift[d["address"]] = keys or ["deleted outside Terraform"]
            if details is not None:
                details[d["address"]] = {"type": d.get("type"), "name": _name(d["change"]), "deleted": not after,
                                         "fields": [{"key": k, "terraform": _short(before.get(k)), "aws": _short(after.get(k)),
                                                     "lines": changed_lines(before.get(k), after.get(k))} for k in keys[:20]]}
    return rows, drift


SCHEMA_CACHE = "tf-schema.json"


async def schema(work: Path, types: set[str], creds: dict | None = None) -> dict[str, dict]:
    """type → {attr: {kind, desc, optional, computed, required, sensitive}, plus blocks}: the provider's own schema, so
    the AWS page can tell settings you may change from read-only facts. Cached on disk (the full AWS schema is big)."""
    cache_file = get_settings().data_dir / SCHEMA_CACHE
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if types <= set(cache):
        return {t: cache[t] for t in types}
    if not (work / "infra" / ".terraform").exists():
        return {t: cache[t] for t in types if t in cache}
    out_file = get_settings().data_dir / "tf-schema-full.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env.update({"AWS_EC2_METADATA_DISABLED": "true", "TF_IN_AUTOMATION": "1", "CHECKPOINT_DISABLE": "1",
                "TF_PLUGIN_CACHE_DIR": str(get_settings().data_dir / "tf-plugin-cache")})
    env.update(creds or {})
    try:
        with out_file.open("wb") as fh:
            proc = await asyncio.create_subprocess_exec("terraform", "providers", "schema", "-json", cwd=work / "infra", env=env,
                                                        stdout=fh, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=180)
        full = json.loads(out_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, asyncio.TimeoutError):
        return {t: cache[t] for t in types if t in cache}
    finally:
        out_file.unlink(missing_ok=True)
    for prov in (full.get("provider_schemas") or {}).values():
        for t, s in (prov.get("resource_schemas") or {}).items():
            if t in types:
                cache[t] = _trim(s.get("block") or {})
    del full
    try:
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return {t: cache[t] for t in types if t in cache}


def _trim(block: dict) -> dict:
    attrs = {}
    for k, a in (block.get("attributes") or {}).items():
        t = a.get("type")
        kind = t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "json")
        attrs[k] = {"kind": {"string": "string", "number": "number", "bool": "bool"}.get(kind, "json"),
                    "desc": (a.get("description") or "")[:300], "optional": bool(a.get("optional")),
                    "required": bool(a.get("required")), "computed": bool(a.get("computed")), "sensitive": bool(a.get("sensitive"))}
    for k, b in (block.get("block_types") or {}).items():
        attrs[k] = {"kind": "block", "desc": (b.get("block", {}).get("description") or "")[:300], "optional": True,
                    "required": bool(b.get("min_items")), "computed": False, "sensitive": False}
    return attrs


async def apply(work: Path, creds: dict, plan_file: str = "tfplan") -> str:
    code, out = await _run(["apply", "-input=false", "-no-color", "-auto-approve", plan_file], work / "infra", 1800, creds)
    if code:
        raise TerraformError("terraform apply failed: " + out[-3500:])
    return out[-4000:]


async def sync_state(work: Path, creds: dict) -> None:
    """Bring Terraform's state up to date with AWS without changing anything in AWS (apply -refresh-only): for a stale
    copy in the state that no plan would change, e.g. a role's inline_policy after its aws_iam_role_policy changed."""
    code, out = await _run(["apply", "-refresh-only", "-input=false", "-no-color", "-auto-approve"], work / "infra", 600, creds)
    if code:
        raise TerraformError("terraform apply -refresh-only failed: " + out[-2000:])


async def output(work: Path, creds: dict) -> dict:
    code, out = await _run(["output", "-json", "-no-color"], work / "infra", 120, creds, full=True)
    if code:
        return {}
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    return {k: ("(sensitive, hidden)" if v.get("sensitive") else v.get("value")) for k, v in data.items()}


# what the crew may create but AWS won't let it delete by name (no per-resource permission): tear down leaves these (free)
UNDELETABLE = {"aws_ecs_task_definition"}


async def forget(work: Path, creds: dict, addresses: list[str]) -> None:
    """Take resources out of Terraform's state without touching AWS (terraform state rm)."""
    if addresses:
        code, out = await _run(["state", "rm", "-no-color", *addresses], work / "infra", 180, creds)
        if code:
            raise TerraformError("terraform state rm failed: " + out[-1500:])


async def destroy(work: Path, creds: dict) -> str:
    code, out = await _run(["destroy", "-input=false", "-no-color", "-auto-approve"], work / "infra", 1800, creds)
    if code:
        raise TerraformError("terraform destroy failed: " + out[-3500:])
    return out[-4000:]
