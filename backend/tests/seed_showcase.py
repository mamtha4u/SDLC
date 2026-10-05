"""Seed a LOCAL data dir with a realistic showcase (no LLM, no AWS), for visual QA of every screen in every theme.

  user  showcase / showcase-pass-1
  5 projects: one fully built and live in AWS (waiting on the final gate), one interviewing with Echo, one completed,
  one blocked on budget, one draft.
Never point it at the host's data dir. Usage (repo root):
  $env:ORKESTRA_DATA_DIR = "<scratch>\\showcase-data"; src\\backend\\.venv\\Scripts\\python -m tests.seed_showcase   (cwd src/backend)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import datetime, timedelta, timezone

assert os.environ.get("ORKESTRA_DATA_DIR") and not os.environ["ORKESTRA_DATA_DIR"].startswith("/opt/orkestra"), "set ORKESTRA_DATA_DIR to a scratch folder"

NOW = datetime.now(timezone.utc)
random.seed(7)


def ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


def iso(minutes: float) -> str:
    return ago(minutes).isoformat()


P = "orkestra-orders-dev"
REQ = """# Partner orders ingest · Requirement v1.1

## 1. Purpose
Partners send purchase orders as XML over HTTPS. The flow validates each order, transforms it to the team's JSON order
event and queues it (FIFO, per order id) for the fulfilment service.

## 2. Interface
| | Source | Target |
|---|---|---|
| System | Partner portal (B2B) | Fulfilment service |
| Message | `Order` XML (v3) | `order.received` JSON |
| Transport | HTTPS POST `/orders` (API key) | SQS FIFO `orders.fifo` |

## 3. Rules
- Reject unreadable XML or a wrong root element with **400** and nothing queued.
- `OrderId`, `Customer/Name`, `Amount` are mandatory; amounts have 2 decimals; currency defaults to `EUR`.
- Customer e-mail and phone are **PII**: never logged.
- Every log line carries the correlation id (team logger layer, `logger.py`).

## 4. Volumes and non-functional
About 2,000 orders/day, peaks of 20/s. p95 under 800 ms. Retention 14 days. Region eu-west-1.

## 8.1 Logging (CR-001)
Use the team's `logger.py` as a Lambda layer; `mask_pii()` must mask e-mail and phone.
"""
PLAN = {
    "summary": "API Gateway → Lambda (Python 3.14, lxml) → SQS FIFO, with the team logger as a layer. Six steps, each with your approval.",
    "services": [{"service": "API Gateway", "purpose": "HTTPS entry point with API key"}, {"service": "Lambda", "purpose": "validate + transform"},
                 {"service": "SQS FIFO", "purpose": "ordered delivery per order id"}, {"service": "CloudWatch Logs", "purpose": "structured logs, 14 days"}],
    "steps": [{"agent": "ba", "task": "Map Order XML → order.received JSON with worked examples for every rule", "approval": "the mapping"},
              {"agent": "ta", "task": "HLD, LLD and the draw.io architecture; quality gate 80% coverage", "approval": "the design"},
              {"agent": "tp", "task": "Terraform for every LLD resource, validated, previewed", "approval": "the infrastructure"},
              {"agent": "de", "task": "Lambda code + pytest, every example a test", "approval": "the code"},
              {"agent": "qa", "task": "End-to-end tests, then live tests in AWS", "approval": "QA and the live result"}],
    "research": [{"claim": "lxml supports Python 3.14", "finding": "lxml 6.1.3 ships 36 cp314 wheels incl. manylinux x86_64", "verdict": "confirmed",
                  "source": "https://pypi.org/project/lxml/6.1.3/"},
                 {"claim": "Lambda has a python3.14 runtime", "finding": "Listed in the Lambda runtimes table", "verdict": "confirmed",
                  "source": "https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html"},
                 {"claim": "SQS FIFO allows 300 msg/s without batching", "finding": "300 per API action per second, 3,000 with batching", "verdict": "confirmed",
                  "source": "https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-fifo.html"}],
    "risks": [{"risk": "Partner retries create duplicates", "mitigation": "MessageDeduplicationId = OrderId", "evidence": "FIFO 5-minute dedup window"},
              {"risk": "PII in logs", "mitigation": "mask_pii() in the logger layer, tested", "evidence": "Requirement §3"}],
    "open_points": ["Confirm the partner API key rotation owner"],
}
ROWS = [
    ("order_id", "ORD1001", "M", "string", "copy", "Order/OrderId", "ORD1001", "M", "string", False, "FIFO group + dedup id"),
    ("customer.name", "Ravi Kumar", "M", "string", "copy", "Order/Customer/Name", "Ravi Kumar", "M", "string", False, "trimmed"),
    ("customer.email", "r***@mail.com", "O", "string", "derived", "Order/Customer/Email", "ravi@mail.com", "O", "string", True, "masked in logs"),
    ("amount", "1500.00", "M", "decimal", "derived", "Order/Amount", "1500", "M", "number", False, "2 decimals"),
    ("currency", "EUR", "M", "string", "constant", "-", "-", "-", "-", False, "default when absent"),
    ("source", "partner-portal", "M", "string", "constant", "-", "-", "-", "-", False, ""),
]
MAPPING = {
    "title": "Order XML → order.received JSON", "summary": "Six target fields from the partner Order v3; two constants; email is PII.",
    "direction": "inbound", "source": {"system": "Partner portal", "message_name": "Order", "format": "XML", "description": "Order v3 schema"},
    "target": {"system": "Fulfilment service", "message_name": "order.received", "format": "JSON", "description": "Team order event"},
    "rows": [dict(zip(("target", "target_sample", "target_moc", "target_type", "rule_kind", "source_path", "source_sample", "source_moc",
                       "source_type", "pii", "comments"), r), logic={"copy": "copy", "constant": "constant", "derived": "format"}[r[4]]) for r in ROWS],
    "rules": [{"rule": "Readable XML", "condition": "body isn't well-formed XML", "action": "400, nothing queued"},
              {"rule": "Root element", "condition": "root isn't <Order>", "action": "400, nothing queued"},
              {"rule": "Mandatory fields", "condition": "OrderId, Customer/Name or Amount missing/empty", "action": "400 naming the field"}],
    "samples": [{"name": n, "description": d, "input": "<Order>…</Order>", "expect": e, "expected": x} for n, d, e, x in (
        ("valid_order", "Happy path", "output", '{"order_id":"ORD1001","customer":{"name":"Ravi Kumar"},"amount":"1500.00","currency":"EUR"}'),
        ("currency_given", "Currency in the message wins", "output", '{"order_id":"ORD1002","currency":"INR"}'),
        ("whitespace_name", "Names are trimmed", "output", '{"customer":{"name":"Anna Berg"}}'),
        ("not_xml", "Unreadable body", "reject", "invalid XML"),
        ("wrong_root", "<Invoice> instead of <Order>", "reject", "root element must be Order"),
        ("missing_amount", "Amount absent", "reject", "Amount is mandatory"))],
    "assumptions": ["Amounts never exceed 10^9", "One order per request"], "queries": [{"question": "Is Customer/Phone ever sent?", "owner": "Partner team"}],
    "changes": "", "check_attempts": 1,
}
MAPPING["proof"] = [{"name": s["name"], "expect": s["expect"], "pass": True, "actual": s["expected"], "verified": True, "checked": 4} for s in MAPPING["samples"]]
ARCH = {
    "title": "Partner orders ingest", "subtitle": "eu-west-1 · orkestra-orders-dev",
    "sources": [{"id": "partner", "label": "Partner portal", "sub": "HTTPS POST /orders", "icon": "external_system", "details": []}],
    "path": [{"id": "api", "label": "API Gateway", "sub": "REST · API key", "icon": "api_gateway", "details": []},
             {"id": "fn", "label": "Lambda transform", "sub": "Python 3.14 · 256 MB", "icon": "lambda", "details": ["Validate XML", "Map to JSON", "Send to FIFO"]}],
    "destinations": [{"id": "q", "label": "orders.fifo", "sub": "SQS FIFO · dedup by OrderId", "icon": "sqs_queue", "details": []}],
    "support": [{"id": "logs", "label": "CloudWatch Logs", "sub": "14 days", "icon": "cloudwatch_logs", "under": "fn"},
                {"id": "layer", "label": "Logger layer", "sub": "team logger.py", "icon": "lambda_layer", "under": "fn"}],
    "edges": [{"from": "partner", "to": "api", "label": "XML", "kind": "data"}, {"from": "api", "to": "fn", "label": "proxy", "kind": "data"},
              {"from": "fn", "to": "q", "label": "order.received", "kind": "data"}, {"from": "fn", "to": "logs", "label": "", "kind": "support"},
              {"from": "layer", "to": "fn", "label": "", "kind": "support"}],
}
DESIGN = {
    "summary": "A three-hop flow: API Gateway (API key) → Lambda transform with lxml and the team logger layer → SQS FIFO keyed by OrderId.",
    "hld_markdown": "# HLD\n\n## Context\nPartners POST orders; the fulfilment service consumes `order.received`.\n\n## Components\n- API Gateway REST, stage `dev`\n- Lambda `orkestra-orders-dev-transform`\n- SQS FIFO `orkestra-orders-dev-orders.fifo` + DLQ\n",
    "lld_markdown": "# LLD\n\n| Resource | Setting | Value |\n|---|---|---|\n| Lambda | runtime | python3.14 |\n| Lambda | memory | 256 MB |\n| Lambda | timeout | 10 s |\n| SQS | content-based dedup | off (OrderId) |\n| Logs | retention | 14 days |\n",
    "changes": "", "open_points": ["API key rotation owner"], "architecture": ARCH,
    "decisions": [{"decision": "SQS FIFO", "why": "per-order ordering and 5-minute dedup", "alternatives": "standard queue + idempotent consumer"},
                  {"decision": "lxml in a layer", "why": "keeps the function zip small; shared", "alternatives": "vendored in the function"}],
    "resources": [{"name": f"{P}-transform", "type": "Lambda", "purpose": "validate + transform", "key_settings": "py3.14, 256 MB, 10 s"},
                  {"name": f"{P}-orders.fifo", "type": "SQS FIFO", "purpose": "events", "key_settings": "dedup by OrderId, DLQ after 3"},
                  {"name": f"{P}-api", "type": "API Gateway", "purpose": "entry", "key_settings": "REST, API key, stage dev"}],
    "quality_gates": {"min_coverage_percent": 80, "rules": ["Every worked example is a test", "No PII in any log line"], "set_by": "requirement"},
}


def seed_aws(pid: str) -> None:
    """The new flow's AWS side: Terra's inventory (the AWS page), Dev's code deploy + sanity check, Quinn's live report."""
    from app.services import aws_access, inventory
    from app.services.storage import ProjectStore

    arn = lambda kind, name: f"arn:aws:{kind}:eu-west-1:144831534428:{name}"  # noqa: E731
    fn, q, dlq = f"{P}-transform", f"{P}-orders.fifo", f"{P}-orders-dlq.fifo"
    qurl = lambda n: f"https://sqs.eu-west-1.amazonaws.com/144831534428/{n}"  # noqa: E731
    rows = [
        {"address": "aws_lambda_function.transform", "type": "aws_lambda_function", "values": {
            "function_name": fn, "runtime": "python3.14", "handler": "handler.lambda_handler", "memory_size": 256, "timeout": 10,
            "architectures": ["arm64"], "role": arn("iam:", f"role/{P}-transform-role").replace("eu-west-1:", ""), "environment": [{"variables": {"QUEUE_URL": qurl(q), "LOG_LEVEL": "INFO"}}],
            "reserved_concurrent_executions": -1, "ephemeral_storage": [{"size": 512}], "tracing_config": [{"mode": "PassThrough"}],
            "logging_config": [{"log_format": "JSON", "log_group": f"/aws/lambda/{fn}"}], "publish": False, "package_type": "Zip", "layers": [],
            "description": "", "kms_key_arn": None, "arn": arn("lambda", f"function:{fn}"), "invoke_arn": f"arn:aws:apigateway:eu-west-1:lambda:path/2015-03-31/functions/{arn('lambda', f'function:{fn}')}/invocations",
            "version": "$LATEST", "last_modified": "2026-10-02T05:10:12.000+0000", "source_code_size": 18233, "tags": {}}},
        {"address": "aws_sqs_queue.orders", "type": "aws_sqs_queue", "values": {
            "name": q, "fifo_queue": True, "content_based_deduplication": False, "deduplication_scope": "messageGroup", "fifo_throughput_limit": "perMessageGroupId",
            "message_retention_seconds": 345600, "visibility_timeout_seconds": 60, "delay_seconds": 0, "max_message_size": 262144, "receive_wait_time_seconds": 0,
            "sqs_managed_sse_enabled": True, "redrive_policy": json.dumps({"deadLetterTargetArn": arn("sqs", dlq), "maxReceiveCount": 3}),
            "arn": arn("sqs", q), "url": qurl(q), "id": qurl(q)}},
        {"address": "aws_sqs_queue.dlq", "type": "aws_sqs_queue", "values": {
            "name": dlq, "fifo_queue": True, "message_retention_seconds": 1209600, "visibility_timeout_seconds": 30, "sqs_managed_sse_enabled": True,
            "delay_seconds": 0, "max_message_size": 262144, "arn": arn("sqs", dlq), "url": qurl(dlq), "id": qurl(dlq)}},
        {"address": "aws_api_gateway_rest_api.api", "type": "aws_api_gateway_rest_api", "values": {
            "name": f"{P}-api", "description": "Partner orders", "endpoint_configuration": [{"types": ["REGIONAL"]}], "api_key_source": "HEADER",
            "minimum_compression_size": "", "disable_execute_api_endpoint": False, "id": "k3x9q2", "root_resource_id": "a1b2c3",
            "execution_arn": "arn:aws:execute-api:eu-west-1:144831534428:k3x9q2", "created_date": "2026-10-02T05:01:44Z"}},
        {"address": "aws_api_gateway_stage.dev", "type": "aws_api_gateway_stage", "values": {
            "stage_name": "dev", "rest_api_id": "k3x9q2", "xray_tracing_enabled": False, "cache_cluster_enabled": False, "variables": {},
            "invoke_url": "https://k3x9q2.execute-api.eu-west-1.amazonaws.com/dev", "id": "ags-k3x9q2-dev"}},
        {"address": "aws_cloudwatch_log_group.fn", "type": "aws_cloudwatch_log_group", "values": {
            "name": f"/aws/lambda/{fn}", "retention_in_days": 14, "log_group_class": "STANDARD", "kms_key_id": "", "skip_destroy": False,
            "arn": arn("logs", f"log-group:/aws/lambda/{fn}")}},
        {"address": "aws_iam_role.fn", "type": "aws_iam_role", "values": {
            "name": f"{P}-transform-role", "permissions_boundary": "arn:aws:iam::144831534428:policy/orkestra-agent-boundary", "max_session_duration": 3600,
            "assume_role_policy": json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}),
            "arn": f"arn:aws:iam::144831534428:role/{P}-transform-role", "unique_id": "AROAEXAMPLE", "create_date": "2026-10-02T05:01:40Z"}},
        {"address": "aws_lambda_permission.api", "type": "aws_lambda_permission", "values": {
            "statement_id": "AllowApiGateway", "action": "lambda:InvokeFunction", "function_name": fn, "principal": "apigateway.amazonaws.com",
            "source_arn": "arn:aws:execute-api:eu-west-1:144831534428:k3x9q2/*/*"}},
        {"address": "aws_api_gateway_resource.orders", "type": "aws_api_gateway_resource", "values": {"path_part": "orders", "path": "/orders", "rest_api_id": "k3x9q2", "id": "r0rd3r"}},
        {"address": "aws_api_gateway_method.post", "type": "aws_api_gateway_method", "values": {"http_method": "POST", "authorization": "NONE", "api_key_required": True}},
        {"address": "aws_api_gateway_integration.post", "type": "aws_api_gateway_integration", "values": {"type": "AWS_PROXY", "integration_http_method": "POST", "timeout_milliseconds": 29000}},
        {"address": "aws_iam_role_policy.send", "type": "aws_iam_role_policy", "values": {"name": "send", "role": f"{P}-transform-role",
            "policy": json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sqs:SendMessage", "Resource": arn("sqs", q)}]})}},
        {"address": "aws_iam_role_policy_attachment.logs", "type": "aws_iam_role_policy_attachment", "values": {
            "role": f"{P}-transform-role", "policy_arn": "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"}},
    ]
    for r in rows:
        r["name"] = r["address"].split(".")[1]
        r["sensitive"] = {}
    opt = lambda *ks, **desc: {k: {"kind": "number" if isinstance(desc.get(k), int) else "string", "optional": True, "desc": desc.get(k, "") if isinstance(desc.get(k), str) else ""} for k in ks}  # noqa: E731
    schema = {
        "aws_lambda_function": {**opt("runtime", "handler", "description", "kms_key_arn", "package_type"),
                                "memory_size": {"kind": "number", "optional": True, "desc": "Amount of memory in MB your Lambda Function can use at runtime. Defaults to 128."},
                                "timeout": {"kind": "number", "optional": True, "desc": "Amount of time your Lambda Function has to run in seconds. Defaults to 3."},
                                "reserved_concurrent_executions": {"kind": "number", "optional": True, "desc": "Amount of reserved concurrent executions. -1 removes the limit."},
                                "publish": {"kind": "bool", "optional": True, "desc": "Whether to publish creation/change as new Lambda Function Version."},
                                "function_name": {"kind": "string", "required": True}, "role": {"kind": "string", "required": True},
                                "architectures": {"kind": "json", "optional": True, "desc": "Instruction set architecture: x86_64 or arm64."},
                                "layers": {"kind": "json", "optional": True}, "tags": {"kind": "json", "optional": True},
                                "environment": {"kind": "block", "optional": True}, "ephemeral_storage": {"kind": "block", "optional": True, "desc": "Size of /tmp (512–10240 MB)."},
                                "tracing_config": {"kind": "block", "optional": True}, "logging_config": {"kind": "block", "optional": True},
                                **{k: {"kind": "string", "computed": True} for k in ("arn", "invoke_arn", "version", "last_modified")},
                                "source_code_size": {"kind": "number", "computed": True}},
        "aws_sqs_queue": {**{k: {"kind": "number", "optional": True, "desc": d} for k, d in (
            ("message_retention_seconds", "How long SQS keeps a message (60 s – 14 days). Default 4 days."),
            ("visibility_timeout_seconds", "How long a received message stays hidden. Default 30 s."), ("delay_seconds", "Delivery delay."),
            ("max_message_size", "Up to 256 KiB."), ("receive_wait_time_seconds", "Long polling wait (0–20 s)."))},
            **{k: {"kind": "bool", "optional": True} for k in ("fifo_queue", "content_based_deduplication", "sqs_managed_sse_enabled")},
            **opt("name", "deduplication_scope", "fifo_throughput_limit", "redrive_policy"),
            **{k: {"kind": "string", "computed": True} for k in ("arn", "url", "id")}},
        "aws_cloudwatch_log_group": {"retention_in_days": {"kind": "number", "optional": True, "desc": "Days to keep log events. 0 = forever."},
                                     **opt("name", "log_group_class", "kms_key_id"), "skip_destroy": {"kind": "bool", "optional": True},
                                     "arn": {"kind": "string", "computed": True}},
    }
    config = {"aws_lambda_function.transform": ["architectures", "environment", "filename", "function_name", "handler", "lifecycle", "logging_config",
                                                "memory_size", "role", "runtime", "source_code_hash", "timeout"],
              "aws_sqs_queue.orders": ["content_based_deduplication", "fifo_queue", "message_retention_seconds", "name", "redrive_policy",
                                       "sqs_managed_sse_enabled", "visibility_timeout_seconds"],
              "aws_sqs_queue.dlq": ["fifo_queue", "message_retention_seconds", "name", "sqs_managed_sse_enabled"],
              "aws_api_gateway_rest_api.api": ["description", "endpoint_configuration", "name"], "aws_api_gateway_stage.dev": ["deployment_id", "rest_api_id", "stage_name"],
              "aws_cloudwatch_log_group.fn": ["name", "retention_in_days"], "aws_iam_role.fn": ["assume_role_policy", "name", "permissions_boundary"]}
    inv = inventory.build(pid, rows, schema, config, None, "v1.1")
    inv["applied_at"] = iso(362)
    inventory.save(pid, inv)
    dep = aws_access.load(pid, "deploy") or {}
    cd = {"functions": {"transform": {"function_name": fn, "source_dir": "src/transform", "layers": ["lxml", "logger"]}},
          "layers": {"lxml": f"{P}-lxml-layer", "logger": f"{P}-logger-layer"}}
    dep.update(outputs={"api_url": "https://k3x9q2.execute-api.eu-west-1.amazonaws.com/dev/orders", "queue_url": qurl(q), "dlq_url": qurl(dlq),
                        "function_name": fn, "code_deploy": cd}, applies=1, last_intent={"reason": "build", "tickets": [], "version": "v1.1", "changes": "", "summary": ""},
               applied_at=iso(362))
    aws_access.save(pid, dep, "deploy")
    layers = {f"{P}-lxml-layer": {"key": "x", "arn": arn("lambda", f"layer:{P}-lxml-layer:1"), "version": 1, "kb": 5214, "published_at": iso(250), "code_version": "v1.1"},
              f"{P}-logger-layer": {"key": "y", "arn": arn("lambda", f"layer:{P}-logger-layer:2"), "version": 2, "kb": 3, "published_at": iso(125), "code_version": "v1.2"}}
    aws_access.save(pid, {"status": "deployed", "version": "v1.2", "role": aws_access.role_name(pid, "de"), "deploys": 2, "live_ok": 0, "deployed_at": iso(125),
                          "functions": {fn: {"key": "transform", "source_dir": "src/transform", "sha": "abc", "kb": 18, "layers": [l["arn"] for l in layers.values()],
                                             "handler": "handler.lambda_handler", "runtime": "python3.14", "code_version": "v1.2", "deployed_at": iso(125)}},
                          "layers": layers,
                          "sanity": {"passed": True, "summary": "One valid order went through the API into the FIFO queue as the mapped JSON; no errors in the logs.",
                                     "calls": 3, "version": "v1.2", "steps": [
                                         {"what": "POST /orders with valid_order", "observed": "200 in 211 ms, {\"accepted\": true}", "ok": True},
                                         {"what": "Read the output queue", "observed": "1 message, body equals the example, MessageGroupId=ORD1001", "ok": True},
                                         {"what": f"Read /aws/lambda/{fn} (last 5 min)", "observed": "4 lines, no ERROR, email masked as r***", "ok": True}]}}, "code")
    # Terra deploys everything (10-03): Dev's packages are live under Terraform
    code = aws_access.load(pid, "code")
    for f in code["functions"].values():
        f["by"] = "terra"
    for layer in code["layers"].values():
        layer["by"] = "terra"
    aws_access.save(pid, {**code, "code_by": "terra", "layers_by": "terra"}, "code")
    live = lambda **x: {"applied": x["fingerprint"], "applied_version": x["version"], "applied_at": iso(130), "handed_at": iso(140), **x}  # noqa: E731
    aws_access.save(pid, {"code": {"transform": live(fingerprint="abc", kb=18, version="v1.2", function_name=fn, source_dir="src/transform")},
                          "layers": {"lxml": live(fingerprint="x", kb=5214, version="v1.1", name=f"{P}-lxml-layer"),
                                     "logger": live(fingerprint="y", kb=3, version="v1.2", name=f"{P}-logger-layer")}}, "packages")
    if os.environ.get("SHOWCASE_DRIFT"):  # someone raised a queue's visibility timeout and edited the code in the console
        aws_access.save(pid, {
            "checked_at": iso(8), "auto": True, "mode": "terra", "clean": False, "status": "found", "total": 13, "unchanged": 12,
            "settings": [{"address": "aws_sqs_queue.orders", "type": "aws_sqs_queue", "name": q, "deleted": False,
                          "fields": [{"key": "visibility_timeout_seconds", "terraform": "60", "aws": "45"}]}],
            "deleted": [], "fingerprint": "f1", "raised": "f1",
            "code": [{"function_name": fn, "key": "transform", "expected_version": "v1.2", "expected_sha": "abc", "live_sha": "zzz", "last_modified": iso(12),
                      "files": [{"file": "order_transform.py", "change": "changed in AWS", "diff": "--- Dev's v1.2/order_transform.py\n+++ AWS now/order_transform.py\n"
                                 "@@ -3,4 +3,5 @@\n def transform(xml: str) -> dict:\n     root = etree.fromstring(xml.encode())\n+    print(xml)  # debugging in the console\n"
                                 "     return {'order_id': root.findtext('OrderId')}"}]}],
            "restore": {"counts": {"create": 0, "update": 1, "replace": 0, "delete": 0},
                        "changes": [{"address": "aws_sqs_queue.orders", "type": "aws_sqs_queue", "name": q, "action": "update", "fields": ["visibility_timeout_seconds"]}]}},
            "drift")
    s = ProjectStore(pid)
    s.write("reports/live_qa.json", json.dumps({
        "summary": "Five of seven scenarios pass live. A retried duplicate after 6 minutes is delivered twice, and a 2 MB order times out at 10 s.",
        "checks": [{"id": "LIVE-01", "title": "Valid order lands in the queue as mapped JSON", "passed": True, "evidence": "POST /orders → 200 in 182 ms; body matches valid_order; MessageGroupId=ORD1001"},
                   {"id": "LIVE-02", "title": "Malformed XML is rejected, nothing queued", "passed": True, "evidence": "400 {\"error\":\"invalid XML\"}; queue empty after 10 s"},
                   {"id": "LIVE-03", "title": "Duplicate within 5 minutes is delivered once", "passed": True, "evidence": "2 POSTs, 1 message (dedup by OrderId)"},
                   {"id": "LIVE-04", "title": "Correlation id on every log line", "passed": True, "evidence": "12/12 lines carry correlation_id"},
                   {"id": "LIVE-05", "title": "Currency defaults to EUR", "passed": True, "evidence": "message currency=EUR when the XML has none"},
                   {"id": "LIVE-06", "title": "Retried duplicate after 6 minutes", "passed": False, "evidence": "2 messages with OrderId ORD1001 (dedup window is 5 min)"},
                   {"id": "LIVE-07", "title": "2 MB order (largest allowed)", "passed": False, "evidence": "504 after 10.0 s; log: Task timed out after 10.00 seconds"}],
        "version": "v1.2", "deployed_version": "v1.1", "calls": 38, "role": aws_access.role_name(pid, "qa"),
        "created": ["TKT-003", "TKT-004"], "closed": ["TKT-001", "TKT-002"], "reopened": [],
        "open": [{"label": "TKT-003", "title": "Retried duplicate delivered twice after 6 minutes", "assignee": "de", "severity": "major", "status": "open"},
                 {"label": "TKT-004", "title": "2 MB orders time out at 10 s", "assignee": "tp", "severity": "critical", "status": "open"}]}))
    for name in ("aws_inventory", "code_deploy"):
        s.write(f"reports/{name}.md", f"# {name.replace('_', ' ').title()}\n\nSee the AWS and Build tabs.\n")


def seed_testing(pid: str) -> None:
    """Quinn's approved test plan, two live runs (the second a retest), and the latest results by scenario id."""
    from app.agents import qa
    from app.services import aws_access
    from app.services.storage import ProjectStore

    s = ProjectStore(pid)
    cases = [("TC-01", "Valid order lands in the queue as mapped JSON", "happy path", "high", "valid_order"),
             ("TC-02", "Order without Currency defaults to EUR", "happy path", "medium", "no_currency"),
             ("TC-03", "Malformed XML is rejected, nothing queued", "negative", "high", "bad_xml"),
             ("TC-04", "Missing OrderId is rejected", "negative", "high", "missing_order_id"),
             ("TC-05", "Wrong root element is rejected", "negative", "medium", "wrong_root"),
             ("TC-06", "Retried duplicate after 6 minutes is delivered once", "edge case", "high", "duplicate_late"),
             ("TC-07", "2 MB order (largest allowed) is accepted", "edge case", "high", "big_order"),
             ("TC-08", "Correlation id on every log line, no PII", "logging", "high", "valid_order")]
    plan = {"summary": "Eight scenarios on the live API: both happy paths, three rejections, the duplicate and size edges, and the logging rules.",
            "scope": "Partner POST /orders → Lambda transform → orders FIFO queue (and its DLQ), end to end in AWS.",
            "approach": "From the outside like the partner, with Quinn's own role: HTTPS calls, then the queue, the DLQ and the logs.",
            "out_of_scope": [{"what": "SQS outage", "why": "can't be simulated without breaking the infrastructure; Dev's unit tests cover it with moto"}],
            "entry_criteria": ["Dev's code deployed and his sanity check passed"], "exit_criteria": ["Every high-priority scenario passes", "No open critical or major ticket"],
            "changes": "", "version": "v1.2", "status": "approved", "approved_at": (NOW - timedelta(minutes=100)).strftime("%Y-%m-%d %H:%M UTC"),
            "written_at": (NOW - timedelta(minutes=110)).strftime("%Y-%m-%d %H:%M UTC"), "basis": "",
            "cases": [{"id": i, "title": t, "category": c, "priority": p, "requirement_ref": f"Atlas's example {d}", "preconditions": "",
                       "steps": "POST /orders with Content-Type application/xml, then read orders.fifo once (and the DLQ for rejections)",
                       "test_data": d, "expected": "200 and the mapped JSON in the queue" if c == "happy path" else "400, nothing queued" if c == "negative" else "as the requirement says"}
                      for i, t, c, p, d in cases]}
    s.write(qa.PLAN_JSON, json.dumps(plan, indent=2))
    plan["basis"] = qa.plan_basis(pid)
    s.write(qa.PLAN_JSON, json.dumps(plan, indent=2))
    s.write(qa.PLAN_MD, qa.plan_markdown(plan))
    ev = {"TC-06": (False, "2 messages with OrderId ORD1001 (SQS dedup window is 5 min) → TKT-003"),
          "TC-07": (False, "504 after 10.0 s; log: Task timed out after 10.00 seconds → TKT-004")}
    s.write(qa.LIVE_JSON, json.dumps({
        "summary": "Six of eight scenarios pass live. A retried duplicate after 6 minutes is delivered twice, and a 2 MB order times out at 10 s.",
        "checks": [{"id": i, "title": t, "passed": ev.get(i, (True, ""))[0], "evidence": ev.get(i, (True, "200 in 180 ms; body matches the example"))[1]}
                   for i, t, *_ in cases],
        "version": "v1.2", "deployed_version": "v1.2", "calls": 41, "role": aws_access.role_name(pid, "qa"), "not_run": [], "signoff": "",
        "risks": ["An SQS outage is only covered by Dev's unit tests (moto)"], "left_for_user": "ORD1001-Q9 waiting in orkestra-orders-dev-orders.fifo",
        "created": ["TKT-003", "TKT-004"], "closed": ["TKT-001", "TKT-002"], "reopened": []}))
    aws_access.save(pid, {"runs": [
        {"run": 1, "at": (NOW - timedelta(minutes=95)).strftime("%Y-%m-%d %H:%M UTC"), "version": "v1.1", "deployed_version": "v1.1", "kind": "full",
         "passed": 6, "failed": 2, "not_run": 0, "created": ["TKT-001", "TKT-002"], "closed": [], "reopened": [], "checks": [], "calls": 36},
        {"run": 2, "at": (NOW - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M UTC"), "version": "v1.2", "deployed_version": "v1.2", "kind": "retest",
         "passed": 6, "failed": 2, "not_run": 0, "created": ["TKT-003", "TKT-004"], "closed": ["TKT-001", "TKT-002"], "reopened": [], "checks": [], "calls": 41}]}, "qa_runs")


async def seed_tickets(db, pid: str) -> None:
    from app.db.models import Ticket, TicketComment

    specs = [
        (1, "Currency missing when the XML has none", "code", "major", "closed", "qa", "LIVE-05", "v1.1", "v1.2", 300,
         [("qa", "created", "Opened TKT-001 and assigned it to Dev."), ("de", "status", "open → in progress. On it."),
          ("de", "status", "in progress → resolved; assigned to Quinn. Fixed in v1.2 with a regression test, deployed to AWS. Quinn, please retest."),
          ("qa", "status", "resolved → closed. Retested live in v1.2: fixed ✅. currency=EUR in the queued message.")]),
        (2, "Correlation id missing from log lines", "code", "minor", "closed", "qa", "LIVE-04", "v1.1", "v1.2", 290,
         [("qa", "created", "Opened TKT-002 and assigned it to Dev."), ("de", "status", "in progress → resolved; assigned to Quinn. Fixed in v1.2."),
          ("qa", "status", "resolved → closed. 12/12 lines carry correlation_id.")]),
        (3, "Retried duplicate delivered twice after 6 minutes", "code", "major", "open", "de", "LIVE-06", "v1.2", None, 31,
         [("qa", "created", "Opened TKT-003 and assigned it to Dev."),
          ("user", "comment", "Partners retry up to 15 minutes later; can we dedup on OrderId for longer than SQS's 5 minutes?")]),
        (4, "2 MB orders time out at 10 s", "infra", "critical", "open", "tp", "LIVE-07", "v1.2", None, 31,
         [("qa", "created", "Opened TKT-004 and assigned it to Terra."), ("cto", "comment", "Terra, timeout and memory are yours: check the LLD's sizing note.")]),
        (5, "Bad XML returned 500 instead of 400", "code", "major", "resolved", "qa", "LIVE-02", "v1.1", "v1.2", 140,
         [("qa", "created", "Opened TKT-005 and assigned it to Dev."), ("de", "status", "in progress → resolved; assigned to Quinn. Fixed in v1.2: the parser error maps to 400.")]),
        (6, "Phone number visible in DLQ messages", "code", "critical", "in_progress", "de", None, "v1.2", None, 20,
         [("user", "created", "Opened TKT-006 and assigned it to Dev."), ("de", "status", "open → in progress. On it.")]),
    ]
    for n, title, area, sev, status, assignee, check, found, fixed, at, history in specs:
        reporter = history[0][0]
        t = Ticket(project_id=pid, number=n, title=title, description="", steps=f"Run check {check} against the live API" if check else "Send an order that fails in the Lambda; read the DLQ",
                   expected="One message per OrderId" if n == 3 else "Response within the timeout" if n == 4 else "No PII anywhere" if n == 6 else "As in the mapping",
                   actual="Two messages" if n == 3 else "504 after 10.0 s" if n == 4 else "+44 7700 900123 in the DLQ body" if n == 6 else "See the evidence",
                   severity=sev, area=area, status=status, assignee=assignee, reporter=reporter, check_id=check, version_found=found, version_fixed=fixed,
                   created_at=ago(at), updated_at=ago(max(1, at - 20)))
        db.add(t)
        await db.flush()
        for i, (who, kind, text) in enumerate(history):
            db.add(TicketComment(ticket_id=t.id, author=who, kind=kind, text=text, created_at=ago(at - i * 6)))


async def main() -> None:
    from app.core.security import hash_password
    from app.db.base import SessionLocal, init_db
    from app.db.models import AgentState, Approval, ChangeRequest, CrewMessage, Event, Intake, LlmCall, Project, User
    from app.services import aws_access, diagram
    from app.services.storage import ProjectStore
    from tests.live_deploy import INFRA

    await init_db()
    async with SessionLocal() as db:
        u = User(username="showcase", display_name="Priya", password_hash=hash_password("showcase-pass-1"), theme="aurora")
        db.add(u)
        await db.flush()
        specs = [("Partner orders ingest", "XML orders from partners → JSON events on a FIFO queue", "waiting", 0.86, 9.42, 20, "violet",
                  "Waiting for your approval: Quinn: 2 ticket(s) open", 30),
                 ("Flight standby sync", "Standby aircraft updates from ops to the crew app", "running", 0.12, 0.41, 10, "cyan",
                  "Echo is interviewing you about the requirement", 2),
                 ("Invoice archive", "Monthly invoices to S3 with an index in DynamoDB", "completed", 1.0, 11.8, 15, "emerald", "Done: live in AWS and tested", 2900),
                 ("Crew roster feed", "Roster changes from the planning system", "waiting", 0.55, 5.0, 5, "amber",
                  "Budget reached: raise it to let Dev continue", 400),
                 ("Baggage events", "Bag scan events to the tracking API", "draft", 0.0, 0.0, 20, "rose", "New project", 5000)]
        projects = []
        for name, desc, status, prog, cost, budget, accent, act, age in specs:
            p = Project(owner_id=u.id, name=name, description=desc, status=status, progress=prog, cost_usd=cost, budget_usd=budget,
                        accent=accent, last_activity=act, created_at=ago(age + 600), updated_at=ago(age))
            db.add(p)
            await db.flush()
            projects.append(p)
        main_p, echo_p, done_p, blocked_p, _ = projects
        await db.commit()
        pids = [p.id for p in projects]

    # ── files ────────────────────────────────────────────────────────────────
    for p, (name, *_rest) in zip(pids, specs):
        ProjectStore(p).create(name)
    s = ProjectStore(pids[0])
    files = {
        "00_requirement.md": REQ, "plan.md": "# Orion's plan\n\n" + PLAN["summary"], "01_data_mapping.md": "# Data mapping\n\n" + MAPPING["summary"],
        "mapping/01_data_mapping.json": json.dumps(MAPPING, indent=2), "02_hld.md": DESIGN["hld_markdown"], "03_lld.md": DESIGN["lld_markdown"],
        "diagrams/design.json": json.dumps(DESIGN, indent=2), "diagrams/architecture.json": json.dumps(ARCH, indent=2),
        "diagrams/architecture.drawio": diagram.build(ARCH, "Partner orders ingest · v1.1"),
        **{k: v.replace("__ID__", "orders").replace("orkestra-deploycheck-orders", P).replace("__PID__", pids[0]) for k, v in INFRA.items()},
        "inputs/logger.py": "def mask_pii(s):\n    return s\n",
        "layers/logger/python/logger.py": "import json, logging\n\n\ndef mask_pii(value: str) -> str:\n    return value[:1] + '***' if value else value\n",
        "layers/lxml/requirements.txt": "lxml==6.1.3\n",
        "src/transform/order_transform.py": "from lxml import etree\n\n\ndef transform(xml: str) -> dict:\n    root = etree.fromstring(xml.encode())\n    return {'order_id': root.findtext('OrderId')}\n",
        "tests/test_order_transform.py": "def test_valid_order():\n    assert True\n", "qa/test_e2e.py": "def test_qa_001_valid_order():\n    assert True\n",
    }
    for k, v in files.items():
        s.write(k, v)
    tests = [{"id": f"tests/test_order_transform.py::test_examples[{n}]", "name": n, "outcome": "passed", "message": "", "time": 0.01}
             for n in [x["name"] for x in MAPPING["samples"]]] + [
             {"id": f"tests/test_handler.py::test_{n}", "name": n, "outcome": "passed", "message": "", "time": 0.02}
             for n in ("api_event_ok", "bad_xml_400", "fifo_ids", "pii_masked", "bug_001_currency_default", "live_001_correlation_id")]
    cov = {"percent": 94.2, "files": {"src/transform/handler.py": {"percent": 91.0, "missing": [41, 42, 57]},
                                     "src/transform/order_transform.py": {"percent": 100.0, "missing": []},
                                     "layers/logger/python/logger.py": {"percent": 88.0, "missing": [19, 20]}}}
    s.write("reports/pytest.json", json.dumps({"ok": True, "total": len(tests), "passed": len(tests), "failed": 0, "error": 0, "tests": tests,
                                               "coverage": cov, "output": ""}))
    s.write("reports/code.json", json.dumps({"summary": "Handler parses the API Gateway event, transforms with lxml, sends to the FIFO queue with "
                                             "OrderId as group and dedup id; the logger layer masks PII.", "notes": ["mask_pii() fixed per CR-001"],
                                             "changes": "Fixed LIVE-001: correlation id on every log line", "files": sorted(k for k in files if k.startswith(("src/", "layers/", "tests/"))),
                                             "version": "v1.1", "test_runs": 4, "coverage": 94.2, "coverage_gate": 80, "fixed_bugs": ["BUG-001"]}))
    plan_qa = [{"id": f"QA-00{i}", "title": t, "type": ty, "expected": e} for i, (t, ty, e) in enumerate((
        ("Valid order lands in FIFO", "positive", "200 + message body equals the example"), ("Malformed XML", "negative", "400, queue empty"),
        ("Duplicate order within 5 min", "edge", "one message"), ("SQS unavailable", "failure", "502, retried by partner"),
        ("p95 latency", "non-functional", "< 800 ms")), 1)]
    s.write("reports/qa.json", json.dumps({"summary": "Driven like API Gateway with moto as AWS; all five scenarios pass after Dev's fix.", "plan": plan_qa,
                                           "results": {p["id"]: {"outcome": "passed", "test": f"qa/test_e2e.py::test_{p['id'].lower().replace('-', '_')}"} for p in plan_qa},
                                           "bugs": [{"id": "BUG-001", "status": "fixed", "severity": "major", "title": "Currency missing when absent in XML",
                                                     "test_id": "test_qa_001", "steps": "POST order without Currency", "expected": "EUR", "actual": "null",
                                                     "found_in": "v1", "fixed_in": "v1.1"}],
                                           "version": "v1.1", "runs": 3, "tests": {"total": 5, "passed": 5, "failed": 0, "error": 0, "tests": []}}))
    s.write("infra/plan_preview.json", json.dumps({"summary": "13 resources: REST API with API key, the transform Lambda + 2 layers, FIFO queue + DLQ, logs, IAM.",
                                                   "resources": [{"address": a, "type": t, "name": n, "settings": st, "tags": "created_by, project_id", "monthly_usd": c}
                                                                 for a, t, n, st, c in (
                                                                     ("aws_lambda_function.transform", "aws_lambda_function", f"{P}-transform", "py3.14 · 256 MB · 10 s", 0.42),
                                                                     ("aws_sqs_queue.orders", "aws_sqs_queue", f"{P}-orders.fifo", "FIFO · dedup by OrderId", 0.03),
                                                                     ("aws_sqs_queue.dlq", "aws_sqs_queue", f"{P}-orders-dlq.fifo", "maxReceive 3", 0.0),
                                                                     ("aws_api_gateway_rest_api.api", "aws_api_gateway_rest_api", f"{P}-api", "REST · API key", 0.21),
                                                                     ("aws_cloudwatch_log_group.fn", "aws_cloudwatch_log_group", f"/aws/lambda/{P}-transform", "14 days", 0.05))],
                                                   "notes": ["Dev provides src/transform/ and layers/logger/python/logger.py", "lxml layer built from layers/lxml/requirements.txt"],
                                                   "changes": "", "files": sorted(k for k in files if k.startswith("infra/")),
                                                   "validate": {"available": True, "ok": True, "diagnostics": []}, "version": "v1.1", "research": []}))
    s.write("reports/live_qa.json", json.dumps({
        "summary": "All six worked examples went through the live API into the FIFO queue with the expected bodies; bad XML gets 400 and nothing is queued.",
        "checks": [{"id": "LIVE-01", "title": "Valid order lands in the queue as mapped JSON", "passed": True, "evidence": "POST /orders → 200 in 182 ms; body matches valid_order; MessageGroupId=ORD1001"},
                   {"id": "LIVE-02", "title": "Malformed XML is rejected, nothing queued", "passed": True, "evidence": "400 {\"error\":\"invalid XML\"}; queue empty after 10 s"},
                   {"id": "LIVE-03", "title": "Duplicate order is delivered once", "passed": True, "evidence": "2 POSTs, 1 message (dedup by OrderId)"},
                   {"id": "LIVE-04", "title": "Correlation id on every log line", "passed": True, "evidence": "12/12 lines carry correlation_id"},
                   {"id": "LIVE-05", "title": "No PII in logs", "passed": True, "evidence": "email logged as r***"}],
        "bugs": [{"id": "LIVE-001", "status": "fixed", "severity": "minor", "title": "Correlation id missing from logs", "test_id": "live_001", "check_id": "LIVE-04",
                  "steps": "POST a valid order, read the logs", "expected": "correlation_id on each line", "actual": "missing", "found_in": "v1", "fixed_in": "v1.1", "live": True}],
        "version": "v1.1", "deployed_version": "v1.1", "calls": 31, "role": aws_access.role_name(pids[0], "qa")}))
    for name in ("deploy_plan", "deploy", "live_qa", "aws_access", "qa_report", "pytest"):
        s.write(f"reports/{name}.md", f"# {name.replace('_', ' ').title()}\n\nSee the Build tab.\n")
    for b in ("BUG-001", "LIVE-001"):
        s.write(f"bugs/{b}.md", f"# {b}\n\nFixed in v1.1.\n")
    acc = aws_access.draft(pids[0])
    acc.update(status="active", granted_at=iso(70))
    aws_access.save(pids[0], acc)
    changes = [{"address": a, "type": a.split(".")[0], "name": n, "action": "create"} for a, n in (
        ("aws_api_gateway_rest_api.api", f"{P}-api"), ("aws_lambda_function.echo", f"{P}-transform"), ("aws_sqs_queue.out", f"{P}-orders.fifo"),
        ("aws_iam_role.fn", f"{P}-transform-role"), ("aws_cloudwatch_log_group.fn", f"/aws/lambda/{P}-transform"))]
    aws_access.save(pids[0], {"status": "deployed", "version": "v1.1", "role": aws_access.role_name(pids[0], "tp"), "applied_at": iso(55), "error": None,
                              "outputs": {"api_url": "https://k3x9q2.execute-api.eu-west-1.amazonaws.com/dev/orders",
                                          "queue_url": f"https://sqs.eu-west-1.amazonaws.com/144831534428/{P}-orders.fifo"},
                              "plan": {"version": "v1.1", "role": aws_access.role_name(pids[0], "tp"), "planned_at": iso(62),
                                       "counts": {"create": 13, "update": 0, "replace": 0, "delete": 0}, "changes": changes,
                                       "layers": [{"layer": "lxml", "zip": "build/layers/lxml.zip", "kb": 5214, "cached": False}]}}, "deploy")
    seed_aws(pids[0])
    seed_testing(pids[0])
    for i, (agent, action, target) in enumerate((("cto", "iam:CreateRole", aws_access.role_name(pids[0], "tp")), ("cto", "iam:PutRolePolicy", "orkestra-task"),
                                                 ("cto", "s3:CreateBucket", acc["state_bucket"]), ("tp", "sts:AssumeRole", aws_access.role_name(pids[0], "tp")),
                                                 ("tp", "terraform plan", "13 to add, 0 to change, 0 to destroy"), ("tp", "terraform apply", "13 to add"),
                                                 ("qa", "HTTPS POST", "https://k3x9q2.execute-api.eu-west-1.amazonaws.com/dev/orders"),
                                                 ("qa", "sqs:ReceiveMessage", f"{P}-orders.fifo"), ("de", "logs:FilterLogEvents", f"/aws/lambda/{P}-transform"))):
        aws_access.audit(pids[0], agent, action, target, True, "")

    # ── database rows ────────────────────────────────────────────────────────
    agents = ["cto", "intake", "ba", "ta", "tp", "de", "qa"]
    acts = {"cto": "Crew roles created for orkestra-orders-dev*", "intake": "Requirement v1.1 signed off", "ba": "Approved by you: the data mapping",
            "ta": "Briefed Dev: the infrastructure for v1.1 is ready", "tp": "In AWS: 13 to add, 0 to change, 0 to destroy", "de": "Approved by you: Dev's fix for TKT-001, TKT-002",
            "qa": "5/7 live checks passed · 2 new ticket(s) · closed TKT-001, TKT-002"}
    costs = {"cto": 0.92, "intake": 1.31, "ba": 0.44, "ta": 1.62, "tp": 0.71, "de": 3.1, "qa": 1.32}
    models = {"cto": "opus-5-5", "intake": "opus-5-5", "ba": "sonnet-5", "ta": "opus-5-5", "tp": "sonnet-5", "de": "sonnet-5", "qa": "sonnet-5"}
    async with SessionLocal() as db:
        for pid, status_of in ((pids[0], {"qa": "needs_approval"}), (pids[1], {"intake": "working"}), (pids[2], {}), (pids[3], {"de": "blocked"}), (pids[4], None)):
            for k in agents:
                if status_of is None:
                    st, act = "waiting", ""
                elif pid == pids[1]:
                    st, act = status_of.get(k, "waiting"), "💬 Asking about the standby priority rules" if k == "intake" else ""
                elif pid == pids[3]:
                    order = agents.index(k)
                    st = status_of.get(k, "done" if order < 5 else "waiting")
                    act = "Project budget $5.00 reached (spent $5.00). Raise the budget to continue." if k == "de" else ""
                else:
                    st, act = status_of.get(k, "done"), acts[k] if pid == pids[0] else "Done"
                db.add(AgentState(project_id=pid, agent=k, status=st, activity=act, started_at=ago(90) if st != "waiting" else None,
                                  tokens_in=random.randint(20, 400) * 1000 if st != "waiting" else 0, tokens_out=random.randint(3, 60) * 1000 if st != "waiting" else 0,
                                  cost_usd=costs[k] if pid == pids[0] and st != "waiting" else 0.0))
        db.add(Intake(project_id=pids[0], status="signed_off", requirement_md=REQ, plan=PLAN, signed_off_at=ago(600), answers={}, uploads=[{"name": "logger.py", "kind": "code", "chars": 41}],
                      rounds=[{"round": 1, "mode": "review", "at": iso(700), "headline": "Almost there: two gaps", "understanding": "XML orders → JSON on FIFO",
                               "completeness": 0.82, "services": PLAN["services"], "gaps": [], "suggestions": [], "assumptions": [], "conflicts": [], "ready_for_signoff": False},
                              {"round": 2, "mode": "review", "at": iso(640), "headline": "Ready to sign off", "understanding": "XML orders → JSON on FIFO, PII masked",
                               "completeness": 1.0, "services": PLAN["services"], "gaps": [], "suggestions": [], "assumptions": [], "conflicts": [], "ready_for_signoff": True}],
                      chat=[{"role": "echo", "text": "Hi Priya! What should this flow do, in a sentence or two?", "ts": iso(760)},
                            {"role": "user", "text": "Partners POST XML orders; we validate, map to our JSON event and put them on a FIFO queue.", "ts": iso(758)},
                            {"role": "echo", "text": "Got it. Which fields are mandatory in the partner's Order?", "ts": iso(757), "quick_replies": ["OrderId, Name, Amount", "All of them"]},
                            {"role": "user", "text": "OrderId, Name, Amount", "ts": iso(755)}],
                      decisions={}, gap_answers={}))
        db.add(Intake(project_id=pids[1], status="collecting", answers={}, uploads=[], rounds=[], decisions={}, gap_answers={},
                      chat=[{"role": "echo", "text": "Hi Priya! I'm Echo. Tell me about **Flight standby sync**: who sends what, and who needs it?", "ts": iso(9)},
                            {"role": "user", "text": "Ops publishes standby aircraft changes; the crew app needs them within a minute.", "ts": iso(7)},
                            {"role": "echo", "text": "Nice and clear. How do ops publish today: a file drop, an API, or a message queue?", "ts": iso(6),
                             "quick_replies": ["SFTP file drop", "REST API", "Message queue (MQ)", "Not sure yet"], "filled": ["source_system"]},
                            {"role": "user", "text": "Message queue (MQ)", "ts": iso(4)},
                            {"role": "echo", "text": "Thanks! Are updates ever **out of order** for the same aircraft, and does the order matter for the crew app?", "ts": iso(3),
                             "quick_replies": ["Order matters", "Latest wins", "Doesn't matter"]}]))
        for pid in pids[2:]:
            db.add(Intake(project_id=pid, status="signed_off" if pid != pids[4] else "collecting", requirement_md=REQ if pid != pids[4] else None,
                          plan=PLAN if pid != pids[4] else None, answers={}, uploads=[], rounds=[], chat=[], decisions={}, gap_answers={}))
        gates = [("plan", "cto", "Orion's plan", 640), ("mapping", "ba", "Atlas's data mapping", 560), ("design", "ta", "Archie's design (HLD, LLD, diagram)", 470),
                 ("infra", "tp", "Terra's infrastructure and the crew's AWS access (v1.1)", 400),
                 ("infra_check", "tp", "Check the infrastructure in AWS (7 resources)", 360), ("code", "de", "Dev's code (v1.1), deployed and sanity-checked", 240),
                 ("test_plan", "qa", "Quinn's test plan: 8 scenarios (2 happy path, 3 negative, 2 edge case, 1 logging)", 100),
                 ("live_bugs", "qa", "Quinn: 2 ticket(s) open (TKT-001 → Dev; TKT-002 → Dev)", 90), ("code", "de", "Dev's fix for TKT-001, TKT-002 (v1.2), deployed", 60)]
        for stage, agent, title, at in gates:
            db.add(Approval(project_id=pids[0], stage=stage, agent=agent, title=title, summary=f"{title}: ready.", artifacts=[], status="approved",
                            created_at=ago(at + 5), decided_at=ago(at)))
        db.add(Approval(project_id=pids[0], stage="live_bugs", agent="qa", title="Quinn: 2 ticket(s) open (TKT-003 → Dev; TKT-004 → Terra)",
                        summary="5 of 7 live checks pass. A retried duplicate is delivered twice, and 2 MB orders time out. Approving sends each ticket to its fixer.",
                        artifacts=["reports/live_qa.md"], status="pending", created_at=ago(30)))
        if os.environ.get("SHOWCASE_DRIFT"):  # visual QA of the drift gate (10-03): the gate the automatic watch raises
            db.add(Approval(project_id=pids[0], stage="drift", agent="tp", status="pending", created_at=ago(8), artifacts=["reports/drift.md"],
                            title=f"Changed in AWS outside Terraform: 1 resource(s) with changed settings, code changed in {P}-transform",
                            summary="Terra compared AWS with the Terraform state and Dev's packages (automatic check)."))
        await seed_tickets(db, pids[0])
        db.add(ChangeRequest(project_id=pids[0], number=1, text="Use our team logger.py as a layer; it must mask e-mail and phone.", attachments=["logger.py"],
                             source="user", status="done", route="requirement", version_from="v1", version_to="v1.1",
                             triage={"summary": "Add the team logger as a Lambda layer with PII masking", "route": "requirement", "reason": "New logging rule",
                                     "brief": "Echo: confirm the masking rules", "affected_agents": [{"agent": "ta", "why": "new layer"}, {"agent": "de", "why": "uses logger"}],
                                     "needs_from_user": []},
                             diff="--- v1\n+++ v1.1\n+## 8.1 Logging (CR-001)\n+Use the team's logger.py as a Lambda layer.\n", created_at=ago(650), updated_at=ago(600)))
        crew = [("user", "crew", "decision", "Approved: Orion's plan", 640), ("cto", "ba", "handoff", "Atlas, the user approved Orion's plan. Use requirement **v1.1**.", 639),
                ("ba", "cto", "ack", "On it: mapping v1.1, every rule gets worked examples.", 638),
                ("ba", "crew", "think", "The partner sends Amount as an integer; the event needs 2 decimals, so that's a derived rule, not a copy.", 600),
                ("ba", "cto", "handoff", "Mapping ready: 6 fields, 6 worked examples, all agreed on the first check.", 562),
                ("ta", "cto", "handoff", "Design ready: HLD, LLD, diagram with 6 components; quality gate 80% coverage.", 472),
                ("tp", "de", "update", "Dev, my Terraform packages src/transform/ and layers/logger/python/. The lxml layer is built from layers/lxml/requirements.txt.", 402),
                ("de", "crew", "work", "🧪 Test run 1: 9/12 passed, 3 failing · coverage 71% (gate 80%)", 340),
                ("de", "crew", "work", "🧪 Test run 3: 12/12 passed ✅ · coverage 94.2% (gate 80%)", 310),
                ("qa", "de", "issue", "🐞 **BUG-001** (major): Currency missing when absent in XML. Expected: EUR Actual: null", 200),
                ("de", "qa", "ack", "Quinn, on it: fixing BUG-001 in v1.1, with a regression test.", 190), ("qa", "de", "fix", "Confirmed fixed in v1.1: BUG-001. Thanks Dev.", 125),
                ("cto", "tp", "assign", f"Terra, you'll act as `{aws_access.role_name(pids[0], 'tp')}`. You can: create, change and delete Lambda, SQS, logs named {P}*.", 78),
                ("cto", "crew", "update", "Roles ready, each capped by the crew boundary. Terraform state goes to a private, versioned bucket.", 70),
                ("tp", "cto", "handoff", "Deploy plan for v1.1: **13 to add, 0 to change, 0 to destroy**.", 62),
                ("tp", "qa", "handoff", "Deployed v1.1 to AWS ✅. Quinn, it's live: over to you.", 55),
                ("qa", "crew", "work", "🌐 POST /dev/orders → **200** (182 ms)", 50), ("qa", "crew", "work", "📬 orkestra-orders-dev-orders.fifo: 1 message(s)", 49),
                ("tp", "cto", "handoff", "Orion, the user checked the infrastructure for v1.1 in AWS and gave the go-ahead ✅: Lambda function "
                 f"{P}-transform, SQS queue {P}-orders.fifo, SQS queue {P}-orders-dlq.fifo, REST API {P}-api and 3 more.", 359),
                ("cto", "ta", "handoff", "Archie, Terra's infrastructure for v1.1 is live and the user confirmed it matches. Please brief Dev: it's ready for the code.", 358),
                ("ta", "de", "handoff", f"Dev, the infrastructure is ready and waiting for your code 🚀. Build to my LLD (v1.1). Terra's functions run placeholder code until you deploy yours:\n"
                 f"- **{P}-transform** ← your `src/transform/` (layers: lxml, logger)\nLayers you publish: lxml → {P}-lxml-layer, logger → {P}-logger-layer.", 357),
                ("de", "ta", "ack", "Thanks Archie, on it. Writing the code for v1.1 into Terra's functions; every one of Atlas's 6 examples becomes a test case before I deploy.", 356),
                ("de", "crew", "work", f"Deployed v1.1 with `{aws_access.role_name(pids[0], 'de')}`:\n- 📦 layer **{P}-lxml-layer** v1 (5214 KB)\n- ⚡ **{P}-transform**: code 18 KB, 2 layer(s)", 250),
                ("de", "crew", "work", "🌐 POST /dev/orders → **200** (211 ms)", 248),
                ("de", "cto", "handoff", "✅ v1.1 is deployed and sanity-checked: one valid order went through the API into the FIFO queue; no errors in the logs.", 246),
                ("qa", "cto", "handoff", "Live QA done: 5/7 live checks passed · 2 new ticket(s) · closed TKT-001, TKT-002.", 31),
                ("qa", "de", "issue", "🎫 **TKT-003** (major, code): Retried duplicate delivered twice after 6 minutes.", 31),
                ("qa", "tp", "issue", "🎫 **TKT-004** (critical, infra): 2 MB orders time out after 10 s.", 31),
                ("qa", "user", "question", "Quinn: 2 ticket(s) open is ready for your review. Approve it, or raise a change request.", 30)]
        for sender, to, kind, text, at in crew:
            db.add(CrewMessage(project_id=pids[0], sender=sender, to=to, kind=kind, text=text, data={}, created_at=ago(at)))
        for i in range(60):
            agent = random.choice(agents)
            pid = random.choice(pids[:4])
            mk = models[agent]
            price = {"opus-5-5": (4.0, 20.0), "sonnet-5": (2.0, 10.0)}[mk]
            tin, tout, cr = random.randint(2, 40) * 1000, random.randint(1, 12) * 1000, random.randint(0, 30) * 1000
            db.add(LlmCall(project_id=pid, owner_id=u.id, agent=agent, model_key=mk, model_id=f"eu.anthropic.claude-{mk}", purpose=random.choice(["chat", "plan", "code", "qa", "design"]),
                           input_tokens=tin, output_tokens=tout, cache_read_tokens=cr, cache_write_tokens=0,
                           cost_usd=round(tin / 1e6 * price[0] + tout / 1e6 * price[1] + cr / 1e6 * 0.2, 4), price={"input": price[0], "output": price[1]},
                           duration_ms=random.randint(2000, 40000), stop_reason="end_turn", created_at=ago(random.randint(10, 60 * 24 * 20))))
        for i, (agent, typ, msg) in enumerate((("qa", "approval.requested", "Approval needed: the flow works live in AWS"), ("tp", "build.updated", "Terra deployed the flow to AWS"),
                                               ("cto", "build.updated", "Orion created the crew's AWS roles"), ("qa", "agent.state", "5/5 live checks passed"))):
            db.add(Event(project_id=pids[0], user_id=u.id, type=typ, agent=agent, message=msg, data={}, created_at=ago(30 + i * 8)))
        await db.commit()
    from app.services import signoff

    print("sign-offs", await signoff.backfill(pids[0]))
    print("seeded", pids)


if __name__ == "__main__":
    asyncio.run(main())
