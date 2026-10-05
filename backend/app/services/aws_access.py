"""The crew's AWS access: one IAM role per agent per project, drafted by Orion at runtime from Terra's Terraform.

The platform role (orkestra-host-role) carries the fence (config/iam/orkestra-crew-fence.json): it may only create
orkestra-* things, and every role it creates must carry the agent boundary (config/iam/orkestra-agent-boundary.json),
the ceiling no agent can ever exceed. Per project, Orion drafts (`draft`) and, after the user approves, creates
(`grant`):

  orkestra-<id>-terra   build, change and tear down this project's resources with Terraform; its state bucket
  orkestra-<id>-dev     deploy the code (and layers) into this project's functions, sanity-check it, read its logs
  orkestra-<id>-quinn   call this project's flow and read its queues and logs (live end-to-end tests)

Any AWS service (user, 10-03: "Orkestra must build anything inside AWS"), fenced by name and tag instead of a list:
Terra may do anything to resources named with the project's prefix or tagged with its project_id, create unnamed things
(EC2 instances, security groups, MQ brokers…) only with those tags, use existing VPCs/subnets/AMIs without changing them,
and make IAM roles/instance profiles only under its prefix with the crew boundary. Tagging is allowed only while creating
(ec2:/ecs:CreateAction), so a colleague's resource can't be tagged into reach. Every AWS action an agent takes is
recorded in logs/aws.jsonl. Deployment state lives in <project>/deploy/ (not versioned: it spans versions).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from app.core.config import BACKEND_DIR, get_settings
from app.services.storage import ProjectStore

ACCOUNT = "144831534428"
REGION = "eu-west-1"
PLATFORM_ROLE = "orkestra-host-role"
PLATFORM_ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/{PLATFORM_ROLE}"
BOUNDARY_ARN = f"arn:aws:iam::{ACCOUNT}:policy/orkestra-agent-boundary"
TASK_POLICY = "orkestra-task"
PERSONA = {"tp": "terra", "de": "dev", "qa": "quinn"}
PURPOSE = {"tp": "Build, change and tear down this project's AWS resources, and deploy Dev's packages (terraform plan/apply/destroy)",
           "de": "Check his code is live in this project's functions, sanity-check the flow, read its logs to debug tickets",
           "qa": "Call this project's deployed flow and read its queues and logs, for the live end-to-end tests"}
# names the platform itself uses: a project prefix may not overlap them
RESERVED = ("orkestra-host-role", "orkestra-deploy-", "orkestra-agent-boundary", "orkestra-crew-fence", "orkestra-tfstate-",
            "orkestra-fence-smoke")
SERVICE_OF = (  # Terraform resource type prefix → service, for labels and test permissions (first match wins; any other
    # aws_<service>_… type is allowed too: the fence is the project's names and tags, not this list)
    ("aws_lambda_", "lambda"), ("aws_sqs_", "sqs"), ("aws_sns_", "sns"), ("aws_api_gateway_", "apigateway"),
    ("aws_apigatewayv2_", "apigateway"), ("aws_cloudwatch_log_", "logs"), ("aws_cloudwatch_metric_alarm", "cloudwatch"),
    ("aws_cloudwatch_event_", "events"), ("aws_scheduler_", "events"), ("aws_iam_", "iam"), ("aws_s3_", "s3"), ("aws_dynamodb_", "dynamodb"),
    ("aws_sfn_", "states"), ("aws_secretsmanager_", "secretsmanager"), ("aws_ssm_parameter", "ssm"),
    ("aws_ecs_", "ecs"), ("aws_ecr_", "ecr"), ("aws_instance", "ec2"), ("aws_launch_template", "ec2"), ("aws_autoscaling_", "ec2"),
    ("aws_ebs_", "ec2"), ("aws_eip", "vpc"), ("aws_key_pair", "ec2"), ("aws_vpc", "vpc"), ("aws_subnet", "vpc"), ("aws_route", "vpc"),
    ("aws_internet_gateway", "vpc"), ("aws_nat_gateway", "vpc"), ("aws_security_group", "vpc"), ("aws_network_", "vpc"),
    ("aws_lb", "elb"), ("aws_alb", "elb"), ("aws_mq_", "mq"), ("aws_elasticache_", "elasticache"), ("aws_db_", "rds"), ("aws_rds_", "rds"),
    ("aws_msk_", "kafka"), ("aws_kinesis_", "kinesis"), ("aws_efs_", "efs"), ("aws_service_discovery_", "cloudmap"),
    ("aws_appautoscaling_", "autoscaling"),
)
LABEL = {"lambda": "Lambda functions and layers", "sqs": "SQS queues", "sns": "SNS topics", "apigateway": "API Gateway APIs",
         "logs": "CloudWatch log groups", "cloudwatch": "CloudWatch alarms", "events": "EventBridge rules and schedules",
         "iam": "IAM roles for the flow", "s3": "S3 buckets", "dynamodb": "DynamoDB tables", "states": "Step Functions state machines",
         "secretsmanager": "Secrets Manager secrets", "ssm": "SSM parameters", "ecs": "ECS clusters, services and tasks",
         "ecr": "ECR image repositories", "ec2": "EC2 instances", "vpc": "VPCs, subnets, gateways and security groups",
         "elb": "Load balancers", "mq": "Amazon MQ brokers", "elasticache": "ElastiCache clusters", "rds": "RDS databases",
         "kafka": "MSK (Kafka) clusters", "kinesis": "Kinesis streams", "efs": "EFS file systems", "cloudmap": "Cloud Map services",
         "autoscaling": "Auto scaling"}
# Always-on services: they cost money every hour even with no traffic (user, 10-03: "inform the user, show the infra cost")
ALWAYS_ON = {"ec2": "EC2 instances run (and bill) every hour", "ecs": "ECS services keep their tasks running (Fargate bills per vCPU-hour)",
             "elb": "a load balancer bills per hour (≈ $16+/month)", "mq": "an MQ broker bills per hour (mq.t3.micro ≈ $25/month)",
             "elasticache": "a cache node bills per hour (cache.t4g.micro ≈ $12/month)", "rds": "a database bills per hour (db.t4g.micro ≈ $13/month + storage)",
             "kafka": "an MSK cluster bills per broker-hour (≈ $150+/month; serverless ≈ $540/month)",
             "vpc": "NAT gateways (≈ $35/month each + data) and interface endpoints (≈ $8/month each per AZ) bill per hour"}


class AccessError(Exception):
    pass


# ── where things live ──────────────────────────────────────────────────────────────────────────────────────────────
def role_name(project_id: str, agent: str) -> str:
    return f"orkestra-{project_id.removeprefix('prj_')}-{PERSONA[agent]}"


def role_arn(project_id: str, agent: str) -> str:
    return f"arn:aws:iam::{ACCOUNT}:role/{role_name(project_id, agent)}"


def state_bucket(project_id: str) -> str:
    return f"orkestra-tfstate-{project_id.removeprefix('prj_').replace('_', '-').lower()}"


def deploy_dir(project_id: str) -> Path:
    d = ProjectStore(project_id).root / "deploy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(project_id: str, name: str = "access") -> dict | None:
    f = ProjectStore(project_id).root / "deploy" / f"{name}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(project_id: str, data: dict, name: str = "access") -> None:
    (deploy_dir(project_id) / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def drop(project_id: str, name: str) -> None:
    (ProjectStore(project_id).root / "deploy" / f"{name}.json").unlink(missing_ok=True)


def audit(project_id: str, agent: str, action: str, target: str = "", ok: bool = True, detail: str = "") -> None:
    ProjectStore(project_id).append_log("aws", {"agent": agent, "role": role_name(project_id, agent) if agent in PERSONA else PLATFORM_ROLE,
                                                "action": action, "target": target, "ok": ok, "detail": detail[:400]})


def calls(project_id: str, limit: int = 80) -> list[dict]:
    f = ProjectStore(project_id).root / "logs" / "aws.jsonl"
    try:
        lines = f.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def platform_policies() -> dict:
    iam = BACKEND_DIR / "config" / "iam"
    return {"fence": json.loads((iam / "orkestra-crew-fence.json").read_text(encoding="utf-8")),
            "boundary": json.loads((iam / "orkestra-agent-boundary.json").read_text(encoding="utf-8"))}


# ── reading Terra's Terraform ──────────────────────────────────────────────────────────────────────────────────────
def resource_types(infra: dict[str, str]) -> list[str]:
    return sorted({t for c in infra.values() for t in re.findall(r'^\s*resource\s+"(aws_[a-z0-9_]+)"', c, re.M)})


def services(types: list[str]) -> tuple[list[str], list[str]]:
    """(services in the Terraform, types the crew can never create). Any service is allowed now; only account-wide or
    identity resources are refused (they're in terraform.FORBIDDEN too). Lambda always brings logs and its role."""
    from app.tools.terraform import FORBIDDEN

    found, refused = set(), []
    for t in types:
        if t in FORBIDDEN or t.startswith("aws_default_"):
            refused.append(t)
            continue
        found.add(next((s for p, s in SERVICE_OF if t.startswith(p)), t.removeprefix("aws_").split("_")[0]))
    if found & {"lambda", "ecs", "ec2"}:
        found |= {"logs", "iam"}
    return sorted(found), refused


def label(svc: str) -> str:
    return LABEL.get(svc, svc.upper() if len(svc) <= 3 else svc.replace("_", " ").title())


def name_prefix(infra: dict[str, str], resources: list[dict] | None = None) -> str | None:
    """The project's naming prefix: name_prefix from an auto-loaded tfvars, else the variable's default, else the
    common start of the orkestra-* names in Terra's preview."""
    for path, c in infra.items():  # the user's renames (Naming section) win
        if path.endswith(".auto.tfvars.json"):
            try:
                v = json.loads(c).get("name_prefix")
            except (ValueError, AttributeError):
                v = None
            if isinstance(v, str) and v:
                return v
    for path, c in infra.items():
        if path.endswith(("terraform.tfvars", ".auto.tfvars")):
            if m := re.search(r'^\s*name_prefix\s*=\s*"([^"$]+)"', c, re.M):
                return m.group(1)
    for c in infra.values():
        if m := re.search(r'variable\s+"name_prefix"\s*{[^}]*?default\s*=\s*"([^"$]+)"', c, re.S):
            return m.group(1)
    names = [r.get("name", "") for r in resources or [] if str(r.get("name", "")).startswith("orkestra-")]
    if not names:
        return None
    common = names[0]
    for n in names[1:]:
        while not n.startswith(common):
            common = common[:-1]
    return common.rstrip("-") if len(names) > 1 else names[0].rsplit("-", 1)[0]


def prefix_problem(project_id: str, prefix: str | None) -> str | None:
    if not prefix:
        return "I can't find the project's name prefix (variable \"name_prefix\" in infra/variables.tf)."
    if not re.fullmatch(r"orkestra-[a-z0-9][a-z0-9-]{3,40}", prefix):
        return f"The name prefix “{prefix}” must look like orkestra-<interface>-<env> (lowercase letters, digits, dashes)."
    if re.match(r"orkestra-[0-9a-f]{12}(-|$)", prefix):
        return f"The name prefix “{prefix}” looks like the crew's own role names; pick the interface name instead."
    for r in RESERVED:
        if r.startswith(prefix) or prefix.startswith(r):
            return f"The name prefix “{prefix}” overlaps the platform's own names ({r}…)."
    for other in get_settings().projects_dir.iterdir() if get_settings().projects_dir.exists() else []:
        if other.name == project_id:
            continue
        acc = load(other.name)
        if acc and acc.get("status") == "active" and acc.get("prefix"):
            p2 = acc["prefix"]
            if p2.startswith(prefix) or prefix.startswith(p2):
                return f"Another deployed project ({other.name}) already uses the names {p2}*; give this one its own prefix."
    return None


# ── the policies ───────────────────────────────────────────────────────────────────────────────────────────────────
def _arns(svc: str, p: str) -> list[str]:
    a, r = ACCOUNT, REGION
    return {
        "lambda": [f"arn:aws:lambda:{r}:{a}:function:{p}*", f"arn:aws:lambda:{r}:{a}:layer:{p}*"],
        "sqs": [f"arn:aws:sqs:{r}:{a}:{p}*"],
        "sns": [f"arn:aws:sns:{r}:{a}:{p}*"],
        "logs": [f"arn:aws:logs:{r}:{a}:log-group:/aws/lambda/{p}*", f"arn:aws:logs:{r}:{a}:log-group:/aws/apigateway/{p}*",
                 f"arn:aws:logs:{r}:{a}:log-group:{p}*"],
        "cloudwatch": [f"arn:aws:cloudwatch:{r}:{a}:alarm:{p}*"],
        "events": [f"arn:aws:events:{r}:{a}:rule/{p}*", f"arn:aws:events:{r}:{a}:event-bus/{p}*",
                   f"arn:aws:events:{r}:{a}:event-bus/default"],  # a test event on the default bus changes nothing there
        "s3": [f"arn:aws:s3:::{p}*"],
        "dynamodb": [f"arn:aws:dynamodb:{r}:{a}:table/{p}*"],
        "states": [f"arn:aws:states:{r}:{a}:stateMachine:{p}*", f"arn:aws:states:{r}:{a}:execution:{p}*"],
        "secretsmanager": [f"arn:aws:secretsmanager:{r}:{a}:secret:{p}*"],
        "ssm": [f"arn:aws:ssm:{r}:{a}:parameter/{p}*"],
        "ecs": [f"arn:aws:ecs:{r}:{a}:*/{p}*"],  # cluster/…, service/<cluster>/…, task/<cluster>/…, task-definition/…
        "ecr": [f"arn:aws:ecr:{r}:{a}:repository/{p}*"],
        "kinesis": [f"arn:aws:kinesis:{r}:{a}:stream/{p}*"],
    }.get(svc, [])


def _sid(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s.title())


# What Terraform reads account-wide while planning (Describe/List calls that take no resource ARN)
PLAN_READS = ["sts:GetCallerIdentity", "tag:GetResources", "lambda:List*", "lambda:GetAccountSettings", "sqs:ListQueues", "sns:List*",
              "apigateway:GET", "logs:Describe*", "cloudwatch:Describe*", "cloudwatch:List*", "s3:ListAllMyBuckets", "s3:GetBucketLocation",
              "dynamodb:Describe*", "dynamodb:List*", "events:List*", "states:List*", "ec2:Describe*", "ecs:Describe*", "ecs:List*",
              "ecr:Describe*", "ecr:GetAuthorizationToken", "elasticloadbalancing:Describe*", "autoscaling:Describe*",
              "application-autoscaling:Describe*", "elasticache:Describe*", "mq:List*", "rds:Describe*", "kinesis:List*", "acm:List*",
              "secretsmanager:ListSecrets", "ssm:DescribeParameters", "kms:List*", "iam:GetRole*", "iam:ListRole*",
              "iam:ListAttachedRolePolicies", "iam:GetPolicy*", "iam:GetInstanceProfile", "iam:ListInstanceProfilesForRole"]
# created with tags (no names in their ARNs, or the API takes no resource): ours only if tagged with the project at creation
CREATE_TAGGED = ["ec2:RunInstances", "ec2:CreateVolume", "ec2:CreateLaunchTemplate", "ec2:CreateVpc", "ec2:CreateInternetGateway",
                 "ec2:AllocateAddress", "ec2:CreateKeyPair", "ec2:ImportKeyPair", "ecs:CreateCluster",
                 "ecs:RegisterTaskDefinition", "ecs:CreateCapacityProvider", "mq:CreateBroker", "mq:CreateConfiguration", 
                 "application-autoscaling:RegisterScalableTarget"]
IN_VPC = {"ec2:CreateSecurityGroup": "security-group", "ec2:CreateNetworkInterface": "network-interface", "ec2:CreateNatGateway": "natgateway",
          "ec2:CreateVpcEndpoint": "vpc-endpoint", "ec2:CreateNetworkAcl": "network-acl", "ec2:CreateSubnet": "subnet"}
USE_EXISTING = ["subnet/*", "security-group/*", "vpc/*", "network-interface/*", "key-pair/*", "launch-template/*"]
# the AWS-managed policies a flow role may get (lint allows the same): service-role/* plus these common ones
MANAGED_POLICIES = ["arn:aws:iam::aws:policy/service-role/*", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
                    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy", "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"]
PASS_TO = ["lambda.amazonaws.com", "apigateway.amazonaws.com", "states.amazonaws.com", "events.amazonaws.com", "scheduler.amazonaws.com",
           "pipes.amazonaws.com", "ecs-tasks.amazonaws.com", "ecs.amazonaws.com", "ec2.amazonaws.com", "rds.amazonaws.com",
           "monitoring.rds.amazonaws.com", "firehose.amazonaws.com", "application-autoscaling.amazonaws.com"]
SERVICE_LINKED = ["ecs*", "elasticache*", "rds*", "mq*", "elasticloadbalancing*", "autoscaling*", "spot*"]
# Services whose ARNs carry the resource's name: anything named <prefix>* (IAM refuses a wildcard service in an ARN, so
# each is listed; the boundary's "Named" statement has the same list). Anything else the crew creates is reached through
# its tags. A new service the team needs: add it here and to the boundary (config/iam/orkestra-agent-boundary.json).
NAMED_SERVICES = ["lambda", "sqs", "sns", "s3", "logs", "events", "scheduler", "states", "dynamodb", "ecs", "ecr", "elasticloadbalancing",
                  "autoscaling", "elasticache", "rds", "mq", "secretsmanager", "ssm", "cloudwatch"]
NO_IDENTITY = ["iam:*", "sts:*", "organizations:*", "account:*"]


def terra_policy(project_id: str, prefix: str, svcs: list[str] | None = None) -> dict:
    """Terra: any service, but only this project's resources: named <prefix>*, or tagged project_id=<id> (created only
    with that tag). Existing VPCs, subnets, AMIs may be used, never changed. `svcs` is kept for callers; unused."""
    p, bucket, pid = prefix, state_bucket(project_id), project_id
    tagged = {"StringEquals": {"aws:RequestTag/project_id": pid}}
    roles = [f"arn:aws:iam::{ACCOUNT}:role/{p}*", f"arn:aws:iam::{ACCOUNT}:instance-profile/{p}*"]
    st: list[dict] = [
        {"Sid": "ReadToPlan", "Effect": "Allow", "Action": PLAN_READS, "Resource": "*"},
        {"Sid": "TerraformState", "Effect": "Allow",
         "Action": ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetBucketLocation", "s3:GetBucketVersioning"],
         "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]},
        {"Sid": "AnyServiceNamedForThisProject", "Effect": "Allow", "NotAction": NO_IDENTITY,
         "Resource": [f"arn:aws:{s}:*:*:*{p}*" if s != "s3" else f"arn:aws:s3:::{p}*" for s in NAMED_SERVICES]},
        {"Sid": "AnyServiceTaggedForThisProject", "Effect": "Allow", "NotAction": NO_IDENTITY, "Resource": "*",
         "Condition": {"StringEquals": {"aws:ResourceTag/project_id": pid}}},
        {"Sid": "CreateOnlyTaggedForThisProject", "Effect": "Allow", "Action": CREATE_TAGGED, "Resource": "*", "Condition": tagged},
        {"Sid": "CreateInAVpcOnlyTagged", "Effect": "Allow", "Action": list(IN_VPC), "Resource": [f"arn:aws:ec2:*:*:{t}/*" for t in IN_VPC.values()],
         "Condition": tagged},
        {"Sid": "RouteTablesOnlyTagged", "Effect": "Allow", "Action": "ec2:CreateRouteTable", "Resource": "arn:aws:ec2:*:*:route-table/*", "Condition": tagged},
        {"Sid": "Ec2TagOnlyWhileCreating", "Effect": "Allow", "Action": "ec2:CreateTags", "Resource": "*",
         "Condition": {**tagged, "Null": {"ec2:CreateAction": "false"}}},
        {"Sid": "EcsTagOnlyWhileCreating", "Effect": "Allow", "Action": "ecs:TagResource", "Resource": "*",
         "Condition": {**tagged, "Null": {"ecs:CreateAction": "false"}}},
        {"Sid": "UseExistingVpcsSubnetsAndImages", "Effect": "Allow",
         "Action": ["ec2:RunInstances", "ec2:CreateNetworkInterface", "ec2:CreateSecurityGroup"],
         "Resource": [*[f"arn:aws:ec2:*:*:{t}" for t in USE_EXISTING], "arn:aws:ec2:*::image/*", "arn:aws:ec2:*::snapshot/*"]},
        {"Sid": "UseAwsDefaultParameterGroups", "Effect": "Allow", "Action": ["elasticache:Create*", "rds:Create*"],
         "Resource": ["arn:aws:elasticache:*:*:*:default*", "arn:aws:rds:*:*:*:default*"]},
        {"Sid": "TriggersForTheseFunctionsOnly", "Effect": "Allow", "Resource": "*",
         "Action": ["lambda:CreateEventSourceMapping", "lambda:UpdateEventSourceMapping", "lambda:DeleteEventSourceMapping"],
         "Condition": {"ArnLike": {"lambda:FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{p}*"}}},
        {"Sid": "TriggerTagsOnCreate", "Effect": "Allow", "Action": "lambda:TagResource",
         "Resource": f"arn:aws:lambda:{REGION}:{ACCOUNT}:event-source-mapping:*", "Condition": tagged},
        {"Sid": "CreateApisTaggedForThisProject", "Effect": "Allow", "Action": "apigateway:POST",
         "Resource": [f"arn:aws:apigateway:{REGION}::/restapis", f"arn:aws:apigateway:{REGION}::/apis"], "Condition": tagged},
        {"Sid": "FlowRolesAndInstanceProfiles", "Effect": "Allow", "Resource": roles,
         "Action": ["iam:GetRole*", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:TagRole",
                    "iam:UntagRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:DeleteRole",
                    "iam:*InstanceProfile"]},
        {"Sid": "NewFlowRolesCarryTheCrewBoundary", "Effect": "Allow", "Resource": roles,
         "Action": ["iam:CreateRole", "iam:PutRolePermissionsBoundary"],
         "Condition": {"StringEquals": {"iam:PermissionsBoundary": BOUNDARY_ARN}}},
        {"Sid": "AwsManagedPoliciesOnly", "Effect": "Allow", "Resource": roles, "Action": ["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
         "Condition": {"ArnLike": {"iam:PolicyARN": MANAGED_POLICIES}}},
        {"Sid": "HandFlowRolesToAwsServicesOnly", "Effect": "Allow", "Action": "iam:PassRole", "Resource": roles[0],
         "Condition": {"StringEquals": {"iam:PassedToService": PASS_TO}}},
        {"Sid": "ServiceLinkedRolesTheServicesNeed", "Effect": "Allow", "Action": "iam:CreateServiceLinkedRole",
         "Resource": f"arn:aws:iam::{ACCOUNT}:role/aws-service-role/*", "Condition": {"StringLike": {"iam:AWSServiceName": SERVICE_LINKED}}},
    ]
    return {"Version": "2012-10-17", "Statement": st}


TEST_USE = {  # what a live check may do with the project's own resources (Dev's sanity check, Quinn's live tests)
    "lambda": ["lambda:InvokeFunction", "lambda:GetFunctionConfiguration"],
    "sqs": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes",
            "sqs:GetQueueUrl", "sqs:SendMessage"],
    "sns": ["sns:Publish", "sns:GetTopicAttributes"],
    "s3": ["s3:GetObject", "s3:ListBucket", "s3:PutObject"],
    "dynamodb": ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:DescribeTable", "dynamodb:PutItem"],
    "states": ["states:StartExecution", "states:DescribeExecution", "states:GetExecutionHistory"],
    "events": ["events:PutEvents", "events:DescribeRule"],
    "ecs": ["ecs:DescribeServices", "ecs:DescribeTasks", "ecs:ListTasks", "ecs:DescribeTaskDefinition"],
    "kinesis": ["kinesis:PutRecord", "kinesis:PutRecords", "kinesis:GetRecords", "kinesis:GetShardIterator", "kinesis:DescribeStream"]}
READ_AROUND = ["logs:DescribeLogGroups", "sqs:ListQueues", "apigateway:GET", "lambda:ListLayers", "lambda:ListLayerVersions",
               "ec2:Describe*", "ecs:List*", "ecs:DescribeTaskDefinition", "elasticloadbalancing:Describe*", "elasticache:Describe*", "mq:List*", "mq:DescribeBroker",
               "rds:Describe*", "events:ListRules"]  # where the live flow's parts are and their state: metadata only


def _test_use(prefix: str, svcs: list[str]) -> list[dict]:
    st = []
    for svc, actions in TEST_USE.items():
        if svc in svcs:
            arns = _arns(svc, prefix)
            if svc == "lambda":
                arns = arns[:1]
            st.append({"Sid": f"Use{_sid(svc)}", "Effect": "Allow", "Action": actions, "Resource": arns})
    return st


def dev_policy(project_id: str, prefix: str, svcs: list[str], terra_layers: bool = False, terra_code: bool = False) -> dict:
    """Dev's role. Terra deploys everything (`terra_code`, the platform's packages.tf): Dev only reads the functions
    (to check they run his package) and runs his sanity check, one test message through the live flow and the logs.
    Older projects: he uploads his code into Terra's functions himself (code only: names, memory, triggers stay
    Terra's), and before 10-02 (`terra_layers` False) also publishes and attaches the layers."""
    p = prefix
    st: list[dict] = [{"Sid": "FindThings", "Effect": "Allow", "Resource": "*", "Action": READ_AROUND},
                      {"Sid": "ReadLiveLogs", "Effect": "Allow", "Resource": _arns("logs", p) + [f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/ecs/{p}*"],
                       "Action": ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"]}]
    if terra_code:  # Terra deploys; Dev reads what runs, and pushes his container images (Lambda images, ECS, EC2) to the project's ECR
        st += [{"Sid": "ReadTheseFunctions", "Effect": "Allow", "Resource": [f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{p}*"],
                "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration"]},
               {"Sid": "EcrLogin", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
               {"Sid": "PushImagesToTheseRepositories", "Effect": "Allow", "Resource": _arns("ecr", p),
                "Action": ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
                           "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages", "ecr:DescribeRepositories"]}]
    elif "lambda" in svcs and terra_layers:
        st += [{"Sid": "DeployCodeIntoTheseFunctions", "Effect": "Allow", "Resource": [f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{p}*"],
                "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:UpdateFunctionCode"]}]
    elif "lambda" in svcs:
        st += [{"Sid": "DeployCodeIntoTheseFunctions", "Effect": "Allow", "Resource": [f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{p}*"],
                "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:UpdateFunctionCode",
                           "lambda:UpdateFunctionConfiguration"]},
               {"Sid": "PublishLayers", "Effect": "Allow",
                "Resource": [f"arn:aws:lambda:{REGION}:{ACCOUNT}:layer:{p}*", f"arn:aws:lambda:{REGION}:{ACCOUNT}:layer:{p}*:*"],
                "Action": ["lambda:PublishLayerVersion", "lambda:GetLayerVersion", "lambda:DeleteLayerVersion"]}]
    st += _test_use(p, svcs)
    return {"Version": "2012-10-17", "Statement": st}


def quinn_policy(project_id: str, prefix: str, svcs: list[str]) -> dict:
    p = prefix
    st: list[dict] = [{"Sid": "FindThings", "Effect": "Allow", "Resource": "*", "Action": READ_AROUND},
                      {"Sid": "ReadLiveLogs", "Effect": "Allow", "Resource": _arns("logs", p) + [f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/ecs/{p}*"],
                       "Action": ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"]}]
    st += _test_use(p, svcs)
    return {"Version": "2012-10-17", "Statement": st}


def _can(agent: str, prefix: str, project_id: str, svcs: list[str], terra_layers: bool = False, terra_code: bool = False) -> list[str]:
    named = [label(s) for s in svcs if s not in ("apigateway", "iam")]
    if agent == "tp":
        out = [f"Create, change and delete any AWS service's resources named {prefix}* or tagged project_id = {project_id}"
               + (f" (this design: {', '.join(named)})" if named else ""),
               "Create unnamed things (EC2 instances, security groups, VPCs, MQ brokers…) only with this project's tags, and tag "
               "only while creating, so nothing else can be pulled into reach",
               "Use existing VPCs, subnets and AMIs (launch into them, add his own security groups) without changing them"]
        if "apigateway" in svcs:
            out.append(f"Create and change API Gateway APIs tagged project_id = {project_id} (and only those)")
        out.append(f"Create IAM roles and instance profiles named {prefix}* for the flow, always capped by the crew boundary, "
                   "and hand them only to AWS services (Lambda, ECS tasks, EC2, …)")
        out.append(f"Read and write the Terraform state in s3://{state_bucket(project_id)}")
        return out
    if agent == "de":
        if terra_code:
            out = [f"Read the Lambda functions {prefix}* to check they run his package, and push his container images to the ECR "
                   f"repositories {prefix}* (Terra deploys his code, images and layers, in plans you approve)"]
        elif terra_layers:
            out = ([f"Upload his code into the Lambda functions {prefix}* (code only: layers, names, memory and triggers are Terra's, "
                    "in plans you approve)"] if "lambda" in svcs else [])
        else:
            out = ([f"Deploy his code into the Lambda functions {prefix}* (code and layers only; names, memory and triggers stay "
                    "Terra's)", f"Publish Lambda layers named {prefix}*"] if "lambda" in svcs else [])
        out.append(f"Run a sanity check: send a test message through the flow, read the log groups of {prefix}*")
        return out
    out = [f"Read the log groups of {prefix}*"]
    if "lambda" in svcs:
        out.append(f"Invoke Lambda functions {prefix}*")
    if "sqs" in svcs:
        out.append(f"Send, read and delete test messages on SQS queues {prefix}*")
    for svc in ("sns", "s3", "dynamodb", "states", "events", "ecs", "kinesis"):
        if svc in svcs:
            out.append(f"Use {label(svc)} {prefix}* for test data")
    out.append("Call the deployed API or load balancer over HTTPS (no AWS permission needed for that)")
    return out


CANNOT = ["Touch anything not named with the project prefix or tagged with its project id: colleagues' resources and other "
          "projects are out of reach, and tags can only be set while creating",
          "Change existing VPCs, subnets, route tables or anything account-wide",
          "Work outside eu-west-1",
          "Go beyond the crew boundary (orkestra-agent-boundary), even if a policy said so",
          "Change its own permissions, the platform role or the boundary"]


def draft(project_id: str, infra: dict[str, str] | None = None, resources: list[dict] | None = None) -> dict:
    """Orion's access plan for this project, from Terra's current Terraform (or the files Terra is about to submit).
    Pure: nothing is created."""
    from app.agents.buildkit import files_under
    from app.agents.tp import load_preview

    store = ProjectStore(project_id)
    infra = infra if infra is not None else files_under(store, ("infra/",))
    if not infra:
        raise AccessError("There's no Terraform under infra/ yet.")
    types = resource_types(infra)
    svcs, unknown = services(types)
    prefix = name_prefix(infra, resources if resources is not None else (load_preview(project_id) or {}).get("resources"))
    from app.tools import terraform

    terra_layers = terraform.manages_layers(infra)  # Terra publishes and attaches the layers
    terra_code = terraform.manages_code(infra)  # … and deploys the code: Dev only reads and tests
    builders = {"tp": terra_policy, "de": lambda pid, p, s: dev_policy(pid, p, s, terra_layers, terra_code), "qa": quinn_policy}
    roles = []
    for agent in ("tp", "de", "qa"):
        roles.append({"agent": agent, "persona": PERSONA[agent].title(), "role": role_name(project_id, agent),
                      "arn": role_arn(project_id, agent), "purpose": PURPOSE[agent],
                      "can": _can(agent, prefix or "<prefix>", project_id, svcs, terra_layers, terra_code), "cannot": CANNOT,
                      "policy": builders[agent](project_id, prefix or "orkestra-unknown", svcs)})
    return {"status": "proposed", "prefix": prefix, "services": svcs, "unsupported": unknown, "resource_types": types,
            "state_bucket": state_bucket(project_id), "region": REGION, "boundary": BOUNDARY_ARN, "platform_role": PLATFORM_ROLE_ARN,
            "always_on": {s: ALWAYS_ON[s] for s in svcs if s in ALWAYS_ON},
            "problem": prefix_problem(project_id, prefix) or (
                f"Terra's Terraform uses {', '.join(unknown)}: account-wide or identity resources the crew may never create in "
                "this shared account." if unknown else None),
            "version": store.manifest()["current_version"], "roles": roles, "drafted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def same_access(a: dict | None, b: dict) -> bool:
    return bool(a) and a.get("prefix") == b.get("prefix") and [r["policy"] for r in a.get("roles", [])] == [r["policy"] for r in b["roles"]]


def markdown(plan: dict) -> str:
    lines = [f"# AWS access plan (Orion) · {plan['version']}", "",
             f"Each agent gets its own IAM role for this project only. Names: **{plan['prefix']}\\***. Region: {plan['region']}. "
             f"Every role carries the crew boundary `{plan['boundary'].rsplit('/', 1)[-1]}`, the ceiling it can never exceed.", ""]
    if plan.get("always_on"):
        lines += ["## ⚠️ Costs while it runs", "", "This design has services that bill every hour, even with no traffic:", "",
                  *[f"- **{label(s)}**: {why}" for s, why in plan["always_on"].items()],
                  "", "Terra's plan shows the monthly estimate; tear the project down (Build tab) when you no longer need it.", ""]
    for r in plan["roles"]:
        lines += [f"## {r['persona']}: `{r['role']}`", "", r["purpose"], "", "**Can**", *[f"- {c}" for c in r["can"]], "",
                  "**Cannot**", *[f"- {c}" for c in r["cannot"]], "", "```json", json.dumps(r["policy"], indent=2), "```", ""]
    return "\n".join(lines)


# ── the user's own changes to an agent's policy (Terra, Dev, Quinn; never Orion's platform role) ──────────────────────
# Kept apart from Orion's generated policy (deploy/access_extras.json), so they survive every re-draft, and merged in
# whenever a role's policy is written. The boundary still caps them: nothing here can reach past orkestra-*/eu-west-1.
NEVER_EXTRA = {"iam", "sts", "organizations", "account", "billing", "ce", "budgets"}  # any other service: the boundary still caps it
READ_ONLY = re.compile(r"^[a-z0-9-]+:(Get|List|Describe|Head|BatchGet|Filter|Lookup|Search|Query|Scan)[A-Za-z0-9*]*$|^apigateway:GET$")
MAX_POLICY = 10240  # IAM's limit for a role's inline policies, in characters


def extras(project_id: str) -> dict:
    """{agent: {"statements": [...], "reason", "by", "at", "history": [...]}}"""
    return load(project_id, "access_extras") or {}


def with_extras(project_id: str, role: dict) -> dict:
    """An agent's policy as written to AWS: Orion's statements plus the user's additions."""
    mine = (extras(project_id).get(role["agent"]) or {}).get("statements") or []
    return {**role["policy"], "Statement": [*role["policy"]["Statement"], *mine]} if mine else role["policy"]


def check_extra(project_id: str, agent: str, statements: list) -> tuple[list[dict], list[str]]:
    """Orion's rules for a user's policy change. Returns (the statements as they'll be written, problems)."""
    plan = load(project_id) or {}
    prefix = plan.get("prefix") or ""
    role = next((r for r in plan.get("roles", []) if r["agent"] == agent), None)
    if not role or plan.get("status") != "active":
        return [], [f"{PERSONA.get(agent, agent).title()} has no AWS role for this project yet: Orion creates it with the infrastructure."]
    if not isinstance(statements, list) or len(statements) > 20:
        return [], ["Give a list of at most 20 policy statements."]
    out, problems = [], []
    for i, st in enumerate(statements, 1):
        where = f"Statement {i}"
        if not isinstance(st, dict):
            problems.append(f"{where}: must be an object with Effect, Action and Resource")
            continue
        unknown = set(st) - {"Sid", "Effect", "Action", "Resource", "Condition"}
        if unknown:
            problems.append(f"{where}: {', '.join(sorted(unknown))} isn't allowed here (use Effect, Action, Resource, Condition)")
        effect = st.get("Effect")
        if effect not in ("Allow", "Deny"):
            problems.append(f"{where}: Effect must be Allow or Deny")
        acts = st.get("Action")
        acts = [acts] if isinstance(acts, str) else acts
        res = st.get("Resource")
        res = [res] if isinstance(res, str) else res
        if not acts or not all(isinstance(a, str) and re.fullmatch(r"[a-z0-9-]+:[A-Za-z0-9*]+", a) for a in acts):
            problems.append(f"{where}: Action must be like \"sqs:PurgeQueue\" (service:Action, * allowed)")
            continue
        if not res or not all(isinstance(r, str) and r for r in res):
            problems.append(f"{where}: Resource is required (an ARN of this project's resources)")
            continue
        if effect == "Allow":  # a Deny only takes rights away: always fine
            bad = sorted({a.split(":")[0] for a in acts} & NEVER_EXTRA)
            if bad:
                problems.append(f"{where}: {', '.join(bad)} can't be added: identity and account-level rights stay with Orion and the platform")
            for r in res:
                if r == "*":
                    if not all(READ_ONLY.match(a) for a in acts):
                        problems.append(f"{where}: Resource \"*\" is only for read-only actions (Get/List/Describe…); name this "
                                        f"project's resources instead ({prefix}*)")
                elif prefix not in r and state_bucket(project_id) not in r:
                    problems.append(f"{where}: {r} isn't one of this project's resources (names start with {prefix})")
        out.append({"Sid": re.sub(r"[^A-Za-z0-9]", "", str(st.get("Sid") or "")) or f"AddedByYou{i}", "Effect": effect,
                    "Action": acts, "Resource": res, **({"Condition": st["Condition"]} if st.get("Condition") else {})})
    merged = {**role["policy"], "Statement": [*role["policy"]["Statement"], *out]}
    size = len(json.dumps(merged, separators=(",", ":")))
    if size > MAX_POLICY:
        problems.append(f"The policy would be {size} characters; IAM allows {MAX_POLICY} per role. Remove or combine statements.")
    return out, problems


def apply_extra(project_id: str, agent: str, statements: list[dict], reason: str, by: str) -> dict:
    """Write the user's change to the agent's role (the platform role does it) and record it. Blocking."""
    plan = load(project_id) or {}
    role = next(r for r in plan["roles"] if r["agent"] == agent)
    ex = extras(project_id)
    prev = ex.get(agent) or {}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {"statements": statements, "reason": reason, "by": by, "at": now,
             "history": [*(prev.get("history") or []), {"at": now, "by": by, "reason": reason, "count": len(statements)}]}
    ex[agent] = entry
    doc = {**role["policy"], "Statement": [*role["policy"]["Statement"], *statements]} if statements else role["policy"]
    _session().client("iam").put_role_policy(RoleName=role["role"], PolicyName=TASK_POLICY, PolicyDocument=json.dumps(doc))
    audit(project_id, "cto", "iam:PutRolePolicy", f"{role['role']}/{TASK_POLICY}", True, f"your change: {len(statements)} statement(s)")
    save(project_id, ex, "access_extras")
    return entry


# ── doing it (platform role) ───────────────────────────────────────────────────────────────────────────────────────
def _session():
    import boto3

    return boto3.session.Session(region_name=REGION)


def grant(project_id: str, plan: dict) -> dict:
    """Create (or update) the three roles and the Terraform state bucket. Blocking: call via asyncio.to_thread."""
    from botocore.exceptions import ClientError

    s = _session()
    iam, s3 = s.client("iam"), s.client("s3")
    trust = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": PLATFORM_ROLE_ARN}, "Action": "sts:AssumeRole"}]})
    for r in plan["roles"]:
        tags = [{"Key": "created_by", "Value": "orkestra"}, {"Key": "project_id", "Value": project_id}, {"Key": "agent", "Value": r["agent"]}]
        try:
            iam.create_role(RoleName=r["role"], AssumeRolePolicyDocument=trust, PermissionsBoundary=BOUNDARY_ARN, Tags=tags,
                            Description=f"Orkestra {r['persona']} for {project_id}: {r['purpose']}"[:1000], MaxSessionDuration=3600)
            audit(project_id, "cto", "iam:CreateRole", r["role"])
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                audit(project_id, "cto", "iam:CreateRole", r["role"], False, str(e))
                raise
            iam.update_assume_role_policy(RoleName=r["role"], PolicyDocument=trust)
        iam.put_role_policy(RoleName=r["role"], PolicyName=TASK_POLICY, PolicyDocument=json.dumps(with_extras(project_id, r)))
        audit(project_id, "cto", "iam:PutRolePolicy", f"{r['role']}/{TASK_POLICY}")
    bucket = plan["state_bucket"]
    try:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})
        audit(project_id, "cto", "s3:CreateBucket", bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            audit(project_id, "cto", "s3:CreateBucket", bucket, False, str(e))
            raise
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [{"Key": "created_by", "Value": "orkestra"},
                                                              {"Key": "project_id", "Value": project_id}]})
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    return {**plan, "status": "active", "granted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def revoke(project_id: str, plan: dict, delete_state: bool) -> None:
    """Delete the roles (and, after a successful destroy, the empty state bucket). Blocking."""
    from botocore.exceptions import ClientError

    s = _session()
    iam = s.client("iam")
    for r in plan.get("roles", []):
        try:
            for name in iam.list_role_policies(RoleName=r["role"])["PolicyNames"]:
                iam.delete_role_policy(RoleName=r["role"], PolicyName=name)
            iam.delete_role(RoleName=r["role"])
            audit(project_id, "cto", "iam:DeleteRole", r["role"])
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise
    if delete_state:
        bucket = s.resource("s3").Bucket(plan["state_bucket"])
        try:
            bucket.object_versions.delete()
            bucket.delete()
            audit(project_id, "cto", "s3:DeleteBucket", plan["state_bucket"])
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchBucket":
                raise


def assume(project_id: str, agent: str, wait: int = 60) -> dict:
    """Short-lived credentials for an agent's role (retries while a just-created role propagates). Blocking."""
    from botocore.exceptions import ClientError

    sts = _session().client("sts")
    deadline = time.time() + wait
    while True:
        try:
            c = sts.assume_role(RoleArn=role_arn(project_id, agent), RoleSessionName=f"{PERSONA[agent]}-{project_id}"[:64],
                                DurationSeconds=3600)["Credentials"]
            audit(project_id, agent, "sts:AssumeRole", role_name(project_id, agent))
            return {"AWS_ACCESS_KEY_ID": c["AccessKeyId"], "AWS_SECRET_ACCESS_KEY": c["SecretAccessKey"],
                    "AWS_SESSION_TOKEN": c["SessionToken"], "AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION}
        except ClientError as e:
            if e.response["Error"]["Code"] != "AccessDenied" or time.time() > deadline:
                audit(project_id, agent, "sts:AssumeRole", role_name(project_id, agent), False, str(e))
                raise AccessError(f"Couldn't act as {role_name(project_id, agent)}: {e}") from e
            time.sleep(5)


def session_for(creds: dict):
    import boto3

    return boto3.session.Session(aws_access_key_id=creds["AWS_ACCESS_KEY_ID"], aws_secret_access_key=creds["AWS_SECRET_ACCESS_KEY"],
                                 aws_session_token=creds["AWS_SESSION_TOKEN"], region_name=REGION)
