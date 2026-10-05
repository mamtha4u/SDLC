"""The AWS page: every resource the project has in AWS, with its settings, built after each apply.

For each resource: what Terra set in the Terraform ("set"), the other settings you may change, which AWS or the
provider filled with defaults ("defaults"), and read-only facts such as ARNs and URLs ("facts"), plus a link to the
resource in the AWS console. The data comes from `terraform show -json` (the state after the apply), the plan's
configuration (which settings the Terraform writes) and the provider's schema (descriptions, what's changeable).

Changing settings never touches AWS directly: the user's edits become an infrastructure change request for Orion and
Terra (`change_text`), Terra changes the Terraform, the user approves the plan, Terra applies it, the user checks again.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from app.services import aws_access

R = aws_access.REGION
CONSOLE = f"https://{R}.console.aws.amazon.com"
SERVICE = (  # type prefix → (group, label)
    ("aws_lambda_", "lambda", "Lambda"), ("aws_apigatewayv2_", "apigateway", "API Gateway"), ("aws_api_gateway_", "apigateway", "API Gateway"),
    ("aws_sqs_", "sqs", "SQS"), ("aws_sns_", "sns", "SNS"), ("aws_cloudwatch_log_", "logs", "CloudWatch Logs"),
    ("aws_cloudwatch_metric_alarm", "alarms", "CloudWatch alarms"), ("aws_cloudwatch_event_", "events", "EventBridge"),
    ("aws_iam_", "iam", "IAM"), ("aws_s3_", "s3", "S3"), ("aws_dynamodb_", "dynamodb", "DynamoDB"), ("aws_sfn_", "states", "Step Functions"),
    ("aws_secretsmanager_", "secrets", "Secrets Manager"), ("aws_ssm_", "ssm", "Systems Manager"),
    ("aws_scheduler_", "events", "EventBridge"), ("aws_ecs_", "ecs", "ECS"), ("aws_ecr_", "ecr", "ECR"),
    ("aws_lb", "elb", "Load balancing"), ("aws_alb", "elb", "Load balancing"), ("aws_instance", "ec2", "EC2"),
    ("aws_launch_template", "ec2", "EC2"), ("aws_autoscaling_", "ec2", "EC2"), ("aws_appautoscaling_", "ecs", "ECS"),
    ("aws_key_pair", "ec2", "EC2"), ("aws_eip", "vpc", "VPC and networking"), ("aws_vpc", "vpc", "VPC and networking"),
    ("aws_subnet", "vpc", "VPC and networking"), ("aws_route", "vpc", "VPC and networking"),
    ("aws_internet_gateway", "vpc", "VPC and networking"), ("aws_nat_gateway", "vpc", "VPC and networking"),
    ("aws_security_group", "vpc", "VPC and networking"), ("aws_network_", "vpc", "VPC and networking"),
    ("aws_mq_", "mq", "Amazon MQ"), ("aws_elasticache_", "elasticache", "ElastiCache"), ("aws_db_", "rds", "RDS"),
    ("aws_rds_", "rds", "RDS"), ("aws_msk_", "msk", "MSK (Kafka)"), ("aws_kinesis_", "kinesis", "Kinesis"),
    ("aws_efs_", "efs", "EFS"),
)
PRIMARY = {"aws_lambda_function", "aws_sqs_queue", "aws_api_gateway_rest_api", "aws_apigatewayv2_api", "aws_cloudwatch_log_group",
           "aws_iam_role", "aws_sns_topic", "aws_s3_bucket", "aws_dynamodb_table", "aws_cloudwatch_metric_alarm",
           "aws_cloudwatch_event_rule", "aws_sfn_state_machine", "aws_secretsmanager_secret", "aws_ssm_parameter",
           "aws_api_gateway_stage", "aws_apigatewayv2_stage", "aws_lambda_event_source_mapping",
           "aws_scheduler_schedule", "aws_ecs_cluster", "aws_ecs_service", "aws_ecs_task_definition", "aws_ecr_repository",
           "aws_lb", "aws_alb", "aws_lb_target_group", "aws_lb_listener", "aws_instance", "aws_autoscaling_group", "aws_vpc",
           "aws_subnet", "aws_nat_gateway", "aws_internet_gateway", "aws_vpc_endpoint", "aws_security_group", "aws_mq_broker",
           "aws_elasticache_replication_group", "aws_elasticache_cluster", "aws_db_instance", "aws_rds_cluster",
           "aws_msk_cluster", "aws_kinesis_stream", "aws_efs_file_system"}
KIND = {"aws_lambda_function": "Lambda function", "aws_lambda_permission": "Invoke permission", "aws_lambda_event_source_mapping": "Trigger (event source)",
        "aws_sqs_queue": "SQS queue", "aws_sqs_queue_policy": "Queue policy", "aws_sqs_queue_redrive_policy": "Redrive policy",
        "aws_api_gateway_rest_api": "REST API", "aws_api_gateway_resource": "API resource (path)", "aws_api_gateway_method": "API method",
        "aws_api_gateway_integration": "API integration", "aws_api_gateway_deployment": "API deployment", "aws_api_gateway_stage": "API stage",
        "aws_api_gateway_method_settings": "API method settings", "aws_apigatewayv2_api": "HTTP API", "aws_apigatewayv2_route": "API route",
        "aws_apigatewayv2_integration": "API integration", "aws_apigatewayv2_stage": "API stage",
        "aws_cloudwatch_log_group": "Log group", "aws_cloudwatch_metric_alarm": "Alarm", "aws_iam_role": "IAM role",
        "aws_iam_role_policy": "Inline policy", "aws_iam_role_policy_attachment": "Managed policy attachment", "aws_sns_topic": "SNS topic",
        "aws_sns_topic_subscription": "Subscription", "aws_s3_bucket": "S3 bucket", "aws_dynamodb_table": "DynamoDB table",
        "aws_cloudwatch_event_rule": "EventBridge rule", "aws_cloudwatch_event_target": "Rule target", "aws_sfn_state_machine": "State machine",
        "aws_scheduler_schedule": "Schedule", "aws_scheduler_schedule_group": "Schedule group",
        "aws_ecs_cluster": "ECS cluster", "aws_ecs_service": "ECS service", "aws_ecs_task_definition": "Task definition",
        "aws_ecr_repository": "ECR repository", "aws_ecr_lifecycle_policy": "Image lifecycle policy",
        "aws_lb": "Load balancer", "aws_alb": "Load balancer", "aws_lb_listener": "Listener", "aws_lb_listener_rule": "Listener rule",
        "aws_lb_target_group": "Target group", "aws_lb_target_group_attachment": "Target", "aws_instance": "EC2 instance",
        "aws_launch_template": "Launch template", "aws_autoscaling_group": "Auto Scaling group", "aws_vpc": "VPC",
        "aws_subnet": "Subnet", "aws_route_table": "Route table", "aws_route_table_association": "Route table association",
        "aws_route": "Route", "aws_internet_gateway": "Internet gateway", "aws_nat_gateway": "NAT gateway", "aws_eip": "Elastic IP",
        "aws_vpc_endpoint": "VPC endpoint", "aws_security_group": "Security group",
        "aws_vpc_security_group_ingress_rule": "Inbound rule", "aws_vpc_security_group_egress_rule": "Outbound rule",
        "aws_security_group_rule": "Security group rule", "aws_mq_broker": "MQ broker", "aws_mq_configuration": "MQ configuration",
        "aws_elasticache_replication_group": "Cache (Redis/Valkey)", "aws_elasticache_cluster": "Cache cluster",
        "aws_elasticache_subnet_group": "Cache subnet group", "aws_db_instance": "Database", "aws_rds_cluster": "Database cluster",
        "aws_db_subnet_group": "Database subnet group", "aws_msk_cluster": "Kafka cluster", "aws_kinesis_stream": "Kinesis stream",
        "aws_efs_file_system": "File system", "aws_appautoscaling_target": "Scaling target", "aws_appautoscaling_policy": "Scaling policy"}
SKIP = {"tags_all",  # the default tags merged in: shown once for the project
        # plumbing: how Terraform ships the placeholder code (Dev's real code is shown separately), redeploy triggers
        "filename", "source_code_hash", "code_sha256", "source_code_size", "last_modified", "s3_bucket", "s3_key", "s3_object_version",
        "image_uri", "replace_security_groups_on_destroy", "replacement_security_group_ids", "timeouts", "triggers", "skip_destroy",
        "qualified_arn", "qualified_invoke_arn", "signing_job_arn", "signing_profile_version_arn"}


def _sensitive(x) -> bool:
    """terraform show marks sensitive values with `true` inside a structure that mirrors the value: a list setting
    comes back as [false, …] or [{}] even when nothing in it is sensitive."""
    if x is True:
        return True
    if isinstance(x, list):
        return any(_sensitive(i) for i in x)
    if isinstance(x, dict):
        return any(_sensitive(v) for v in x.values())
    return False
NAME_KEYS = {"name", "function_name", "bucket", "layer_name", "alarm_name", "table_name", "topic_name", "name_prefix"}  # renamed via names
FACT_HINT = re.compile(r"(^|_)(arn|id|url|uri|invoke_arn|version|last_modified|code_sha256|source_code_size|created_date|"
                       r"execution_arn|root_resource_id|api_endpoint|qualified_arn|unique_id|create_date|signing_job_arn|state|"
                       r"state_reason|arn_suffix|hosted_zone_id|bucket_domain_name|bucket_regional_domain_name|stream_arn|"
                       r"stream_label)$")


def _service(rtype: str) -> tuple[str, str]:
    return next(((g, label) for p, g, label in SERVICE if rtype.startswith(p)), ("other", "Other"))


def display_name(rtype: str, v: dict) -> str:
    for k in ("function_name", "name", "bucket", "role", "statement_id", "path_part", "stage_name", "route_key", "http_method", "alarm_name"):
        if isinstance(v.get(k), str) and v[k]:
            return v[k]
    if v.get("url"):
        return str(v["url"]).rsplit("/", 1)[-1]
    if v.get("function_name"):
        return v["function_name"]
    return str(v.get("id") or "")[:80]


def console_url(rtype: str, v: dict) -> str | None:
    name = v.get("function_name") or v.get("name") or ""
    if rtype == "aws_lambda_function":
        return f"{CONSOLE}/lambda/home?region={R}#/functions/{quote(name)}"
    if rtype == "aws_lambda_event_source_mapping" and v.get("function_name"):
        return f"{CONSOLE}/lambda/home?region={R}#/functions/{quote(str(v['function_name']).rsplit(':', 1)[-1])}?tab=configure"
    if rtype == "aws_sqs_queue" and v.get("url"):
        return f"{CONSOLE}/sqs/v3/home?region={R}#/queues/{quote(v['url'], safe='')}"
    if rtype == "aws_api_gateway_rest_api" and v.get("id"):
        return f"{CONSOLE}/apigateway/main/apis/{v['id']}/resources?api={v['id']}&region={R}"
    if rtype == "aws_api_gateway_stage" and v.get("rest_api_id"):
        return f"{CONSOLE}/apigateway/main/apis/{v['rest_api_id']}/stages?api={v['rest_api_id']}&region={R}"
    if rtype in ("aws_apigatewayv2_api", "aws_apigatewayv2_stage") and (v.get("api_id") or v.get("id")):
        return f"{CONSOLE}/apigateway/main/api-detail?api={v.get('api_id') or v.get('id')}&region={R}"
    if rtype == "aws_cloudwatch_log_group" and name:
        return f"{CONSOLE}/cloudwatch/home?region={R}#logsV2:log-groups/log-group/{quote(name, safe='').replace('%', '$25')}"
    if rtype == "aws_cloudwatch_metric_alarm" and v.get("alarm_name"):
        return f"{CONSOLE}/cloudwatch/home?region={R}#alarmsV2:alarm/{quote(v['alarm_name'])}"
    if rtype == "aws_iam_role" and name:
        return f"https://us-east-1.console.aws.amazon.com/iam/home#/roles/details/{quote(name)}"
    if rtype == "aws_sns_topic" and v.get("arn"):
        return f"{CONSOLE}/sns/v3/home?region={R}#/topic/{v['arn']}"
    if rtype == "aws_s3_bucket" and v.get("bucket"):
        return f"https://{R}.console.aws.amazon.com/s3/buckets/{v['bucket']}?region={R}"
    if rtype == "aws_dynamodb_table" and name:
        return f"{CONSOLE}/dynamodbv2/home?region={R}#table?name={quote(name)}"
    if rtype == "aws_cloudwatch_event_rule" and name:
        return f"{CONSOLE}/events/home?region={R}#/eventbus/default/rules/{quote(name)}"
    if rtype == "aws_sfn_state_machine" and v.get("arn"):
        return f"{CONSOLE}/states/home?region={R}#/statemachines/view/{quote(v['arn'], safe='')}"
    if rtype == "aws_scheduler_schedule" and name:
        return f"{CONSOLE}/scheduler/home?region={R}#schedules/{quote(v.get('group_name') or 'default')}/{quote(name)}"
    rid = str(v.get("id") or "")
    if rtype == "aws_ecs_cluster" and name:
        return f"{CONSOLE}/ecs/v2/clusters/{quote(name)}/services?region={R}"
    if rtype == "aws_ecs_service" and name and v.get("cluster"):
        return f"{CONSOLE}/ecs/v2/clusters/{quote(str(v['cluster']).rsplit('/', 1)[-1])}/services/{quote(name)}/health?region={R}"
    if rtype == "aws_ecs_task_definition" and v.get("family"):
        return f"{CONSOLE}/ecs/v2/task-definitions/{quote(v['family'])}/{v.get('revision') or ''}/containers?region={R}"
    if rtype == "aws_ecr_repository" and name and v.get("registry_id"):
        return f"{CONSOLE}/ecr/repositories/private/{v['registry_id']}/{quote(name)}?region={R}"
    ec2 = f"{CONSOLE}/ec2/home?region={R}#"
    vpc = f"{CONSOLE}/vpcconsole/home?region={R}#"
    links = {"aws_instance": f"{ec2}InstanceDetails:instanceId={rid}", "aws_launch_template": f"{ec2}LaunchTemplateDetails:launchTemplateId={rid}",
             "aws_autoscaling_group": f"{ec2}AutoScalingGroupDetails:id={quote(name)}", "aws_security_group": f"{ec2}SecurityGroup:groupId={rid}",
             "aws_eip": f"{ec2}ElasticIpDetails:AllocationId={rid}", "aws_vpc": f"{vpc}VpcDetails:VpcId={rid}",
             "aws_subnet": f"{vpc}SubnetDetails:subnetId={rid}", "aws_route_table": f"{vpc}RouteTableDetails:RouteTableId={rid}",
             "aws_internet_gateway": f"{vpc}InternetGatewayDetails:InternetGatewayId={rid}",
             "aws_nat_gateway": f"{vpc}NatGatewayDetails:NatGatewayId={rid}", "aws_vpc_endpoint": f"{vpc}EndpointDetails:vpcEndpointId={rid}"}
    if rtype in links and rid:
        return links[rtype]
    if rtype in ("aws_lb", "aws_alb") and v.get("arn"):
        return f"{ec2}LoadBalancer:loadBalancerArn={v['arn']}"
    if rtype in ("aws_lb_listener", "aws_lb_listener_rule") and v.get("load_balancer_arn"):  # shown on its load balancer
        return f"{ec2}LoadBalancer:loadBalancerArn={v['load_balancer_arn']}"
    if rtype == "aws_lb_listener" and str(v.get("arn") or "").count("/") >= 3:
        return f"{ec2}LoadBalancer:loadBalancerArn={v['arn'].replace(':listener/', ':loadbalancer/').rsplit('/', 1)[0]}"
    if rtype == "aws_lb_target_group" and v.get("arn"):
        return f"{ec2}TargetGroup:targetGroupArn={v['arn']}"
    if rtype == "aws_mq_broker" and rid:
        return f"{CONSOLE}/amazon-mq/home?region={R}#/brokers/details?id={rid}"
    if rtype.startswith("aws_elasticache_") and rid:
        return f"{CONSOLE}/elasticache/home?region={R}"
    if rtype == "aws_db_instance" and v.get("identifier"):
        return f"{CONSOLE}/rds/home?region={R}#database:id={v['identifier']};is-cluster=false"
    if rtype == "aws_rds_cluster" and v.get("cluster_identifier"):
        return f"{CONSOLE}/rds/home?region={R}#database:id={v['cluster_identifier']};is-cluster=true"
    return None


def _kind(value, s: dict | None) -> str:
    if s and s.get("kind") in ("string", "number", "bool"):
        return s["kind"]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str) or value is None:
        return "string" if not s or s.get("kind") != "block" else "json"
    return "json"


def _base(address: str) -> str:
    return re.sub(r"\[[^\]]*\]$", "", address)


def build(project_id: str, rows: list[dict], schema: dict[str, dict], config: dict[str, list[str]], code: dict | None,
          version: str | None, drift: dict[str, list[str]] | None = None) -> dict:
    """`drift`: address → settings that differ in AWS from Terraform's state (changed in the console, outside Terraform)."""
    resources = []
    for r in rows:
        t, v, sens = r["type"], r["values"], r.get("sensitive") or {}
        sch = schema.get(t) or {}
        set_keys = set(config.get(_base(r["address"]), []))
        group, glabel = _service(t)
        out = {"address": r["address"], "type": t, "kind": KIND.get(t, t.removeprefix("aws_").replace("_", " ")), "service": group,
               "service_label": glabel, "name": display_name(t, v), "primary": t in PRIMARY, "console": console_url(t, v),
               "set": [], "defaults": [], "facts": []}
        for k in sorted(v):
            if k in SKIP:
                continue
            s = sch.get(k)
            value = v[k]
            hidden = _sensitive(sens.get(k)) or bool(s and s.get("sensitive"))
            if hidden:
                value = "(sensitive, hidden)"
            item = {"key": k, "value": value, "kind": _kind(v[k], s), "desc": (s or {}).get("desc", "")}
            editable = not hidden and k not in NAME_KEYS  # names change through the names file (rename), not as a setting
            if k in set_keys:
                out["set"].append({**item, "editable": editable})
            elif s and (s.get("optional") or s.get("required")):
                out["defaults"].append({**item, "editable": editable})
            elif s or FACT_HINT.search(k):
                if value not in (None, "", [], {}):
                    out["facts"].append({"key": k, "value": value, "kind": item["kind"], "desc": item["desc"]})
            elif value not in (None, "", [], {}):  # no schema: show it, changeable through Terra
                out["defaults"].append({**item, "editable": not hidden})
        if t == "aws_lambda_function" and code:
            fn = (code.get("functions") or {}).get(out["name"])
            out["code"] = fn or {"placeholder": True}
        if drift and drift.get(r["address"]):
            out["drift"] = drift[r["address"]]
        resources.append(out)
    order = list(dict.fromkeys([g for _, g, _ in SERVICE] + ["other"]))  # API Gateway has two type prefixes: one group
    resources.sort(key=lambda x: (order.index(x["service"]), not x["primary"], x["address"]))
    services = []
    for g in order:
        rs = [x for x in resources if x["service"] == g]
        if rs:
            services.append({"key": g, "label": rs[0]["service_label"], "count": len(rs), "primary": sum(x["primary"] for x in rs)})
    return {"version": version, "resources": resources, "services": services, "count": len(resources),
            "drifted": sum(1 for x in resources if x.get("drift")),
            "settings": sum(len(x["set"]) + len(x["defaults"]) for x in resources),
            "tags": {"created_by": "orkestra", "project_id": project_id}}


def load(project_id: str) -> dict | None:
    return aws_access.load(project_id, "inventory")


def save(project_id: str, inv: dict) -> None:
    aws_access.save(project_id, inv, "inventory")


class ChangeError(ValueError):
    pass


def parse_value(raw, kind: str):
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).strip().lower() in ("true", "false"):
            return str(raw).strip().lower() == "true"
        raise ChangeError(f"“{raw}” isn't true or false")
    if kind == "number":
        try:
            f = float(raw)
        except (TypeError, ValueError):
            raise ChangeError(f"“{raw}” isn't a number") from None
        return int(f) if f.is_integer() else f
    if kind == "json" and isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw  # Terra reads it as an instruction
    return raw


def fmt(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "(not set)" if value is None or value == "" else str(value)


def change_text(inv: dict, changes: list[dict], note: str, allow_empty: bool = False) -> tuple[str, list[dict]]:
    """Validate the user's edits against the inventory; return the change request text and the clean list
    (`allow_empty`: renames come with it, so no setting change is fine)."""
    by = {r["address"]: r for r in inv.get("resources", [])}
    clean, lines = [], []
    for c in changes:
        r = by.get(c.get("address", ""))
        if not r:
            raise ChangeError(f"{c.get('address')} isn't one of this project's resources")
        item = next((i for i in r["set"] + r["defaults"] if i["key"] == c.get("key")), None)
        if c.get("key") in NAME_KEYS:
            raise ChangeError(f"{r['name']}: rename it with the pencil next to its name (names live in one names file)")
        if not item or not item.get("editable"):
            raise ChangeError(f"{r['name'] or r['address']}: {c.get('key')} can't be changed")
        new = parse_value(c.get("to"), item["kind"])
        if fmt(new) == fmt(item["value"]):
            continue
        clean.append({"address": r["address"], "name": r["name"], "kind": r["kind"], "key": item["key"], "from": item["value"], "to": new})
        lines.append(f"- {r['kind']} **{r['name']}** (`{r['address']}`): `{item['key']}` {fmt(item['value'])} → **{fmt(new)}**")
    if not clean and not note.strip() and not allow_empty:
        raise ChangeError("Nothing changed")
    text = "Change these AWS settings (from the AWS page):\n" + "\n".join(lines) if lines else ""
    if note.strip():
        text = (text + "\n\n" if text else "") + f"Note: {note.strip()}"
    return text, clean
