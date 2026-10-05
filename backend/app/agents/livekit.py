"""Tools for working with the live flow in AWS, shared by Dev's sanity check and Quinn's live tests.

Each agent acts with its own short-lived role (services/aws_access), and the tools refuse anything that isn't the
project's own: HTTPS only to the endpoints in Terra's outputs, queues/functions/log groups only with the project prefix.
Every call is in the AWS audit trail and shows up in the crew room.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import replace
from urllib.parse import urlparse

from app.agents.base import AgentError, Tool, obj
from app.orchestrator import crewchat
from app.services import aws_access
from app.tools import terraform

HTTP = Tool(
    name="http_request",
    description="Call the deployed flow over HTTPS, like the real caller. Only the project's own endpoints (the URLs in the "
                "deployment outputs) are allowed. Returns the status, headers and body.",
    schema=obj({"method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "url": {"type": "string"},
                "headers": {"type": "string", "description": "one 'Name: value' per line, or empty"},
                "body": {"type": "string", "description": "request body, or empty"}}),
)
SQS_SEND = Tool(
    name="sqs_send",
    description="Put a message on one of the project's SQS queues (e.g. the flow's input queue). For a FIFO queue give a "
                "group id (and a deduplication id unless content-based deduplication is on).",
    schema=obj({"queue_url": {"type": "string"}, "body": {"type": "string"},
                "group_id": {"type": "string", "description": "FIFO only, else empty"},
                "dedup_id": {"type": "string", "description": "FIFO only, else empty"},
                "attributes": {"type": "string", "description": "message attributes as JSON {name: string value}, or empty"}}),
)
SQS_RECEIVE = Tool(
    name="sqs_receive",
    description="Read the messages waiting in one of the project's SQS queues (long-polls up to wait_seconds, max 20). Reading "
                "consumes: every message you read is deleted, so read each queue once per check and keep what it returned. There is "
                "no peek: re-reading a message counts as another receive (it moves to the DLQ after maxReceiveCount), and on a FIFO "
                "queue an unread older message holds back every newer one in its group.",
    schema=obj({"queue_url": {"type": "string"}, "wait_seconds": {"type": "integer"}}),
)
INVOKE = Tool(
    name="invoke_lambda",
    description="Invoke one of the project's Lambda functions directly with a JSON payload. Returns its result, any error and the log tail.",
    schema=obj({"function_name": {"type": "string"}, "payload": {"type": "string", "description": "JSON text"}}),
)
LOGS = Tool(
    name="read_logs",
    description="Recent CloudWatch log events from one of the project's log groups (e.g. /aws/lambda/<function>).",
    schema=obj({"log_group": {"type": "string"}, "filter_pattern": {"type": "string", "description": "CloudWatch filter pattern, or empty"},
                "minutes": {"type": "integer", "description": "how far back, max 60"}}),
)
# Any flow shape, not only API → Lambda → SQS (user, 10-05: an EventBridge schedule → Lambda → S3 flow failed Dev's check
# because nothing could read S3): start a flow the way it really starts, and read whatever it produces.
S3_LIST = Tool(
    name="s3_list",
    description="List the newest objects in one of the project's S3 buckets (optionally under a prefix): key, size, time.",
    schema=obj({"bucket": {"type": "string"}, "prefix": {"type": "string", "description": "key prefix, or empty"}}),
)
S3_GET = Tool(
    name="s3_get",
    description="Read one object from one of the project's S3 buckets (text, first 6000 characters).",
    schema=obj({"bucket": {"type": "string"}, "key": {"type": "string"}}),
)
S3_PUT = Tool(
    name="s3_put",
    description="Upload a test object to one of the project's S3 buckets (for flows triggered by an S3 upload).",
    schema=obj({"bucket": {"type": "string"}, "key": {"type": "string"}, "body": {"type": "string"},
                "content_type": {"type": "string", "description": "e.g. application/json, or empty"}}),
)
DDB_READ = Tool(
    name="dynamodb_read",
    description="Read one of the project's DynamoDB tables: one item by its key (JSON, e.g. {\"order_id\": \"ORD1\"}), or up to 10 items "
                "when the key is empty.",
    schema=obj({"table": {"type": "string"}, "key": {"type": "string", "description": "the item's key as JSON, or empty"}}),
)
EVENTS_PUT = Tool(
    name="events_put",
    description="Send a test event to EventBridge (the default bus or the project's own bus), for flows started by an event rule. "
                "A flow on a schedule has no event to send: invoke its target directly instead (invoke_lambda).",
    schema=obj({"bus": {"type": "string", "description": "default, or the project's bus name"}, "source": {"type": "string"},
                "detail_type": {"type": "string"}, "detail": {"type": "string", "description": "the event detail as JSON"}}),
)
SNS_PUBLISH = Tool(
    name="sns_publish",
    description="Publish a test message to one of the project's SNS topics (for flows started by a topic).",
    schema=obj({"topic_arn": {"type": "string"}, "message": {"type": "string"}, "subject": {"type": "string", "description": "or empty"}}),
)
CALLS = {"http_request", "sqs_send", "sqs_receive", "invoke_lambda", "read_logs", "s3_list", "s3_get", "s3_put", "dynamodb_read",
         "events_put", "sns_publish"}
TRIGGERS = ("HTTP ", "sent to ", "invoked ", "s3 put ", "event put ", "published to ")  # how evidence lines start
DESTINATION_READS = ("message(s)", "object(s)", "bytes", "item(s)")


def front_doors(hosts: set[str]) -> set[str]:
    """The hosts a real caller uses (API Gateway, function URLs, load balancers, custom domains), not AWS service APIs
    that appear as resource URLs in the outputs (10-05: a DLQ's https://sqs… URL made a schedule-driven flow "need" an
    HTTP call)."""
    return {h for h in hosts if "execute-api" in h or "lambda-url" in h or h.endswith(".elb.amazonaws.com")
            or not h.endswith("amazonaws.com")}


def allowed_hosts(outputs: dict) -> set[str]:
    """The project's own endpoints from the outputs: HTTPS URLs (API Gateway, function URLs) and load balancers, whose
    listener is often plain HTTP (an ALB's DNS name may come without a scheme)."""
    hosts = set()

    def walk(v) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.startswith(("https://", "http://")):
            hosts.add(urlparse(v).hostname or "")
        elif isinstance(v, str) and v.endswith(".elb.amazonaws.com") and " " not in v:
            hosts.add(v)
    walk(outputs)
    return hosts - {""}


def tools(pid: str, agent: str, creds: dict, outputs: dict, box: dict) -> list[Tool]:
    """The live tools for `agent` (de | qa) with its credentials. `box["calls"]` counts real calls (evidence)."""
    prefix = (aws_access.load(pid) or {}).get("prefix") or "orkestra-"
    hosts = allowed_hosts(outputs)
    box.setdefault("calls", 0)
    box.setdefault("log", [])

    def mine(name: str) -> str:
        n = name.rsplit(":", 1)[-1].rsplit("/", 1)[-1] if name.startswith(("arn:", "https://")) else name
        for aws_path in terraform.AWS_LOG_PATHS:
            n = n.removeprefix(aws_path)
        if not n.startswith(prefix):
            raise AgentError(f"{name} isn't one of this project's resources ({prefix}*); your role can't reach it.")
        return name

    def note(line: str) -> None:
        box["calls"] += 1
        box["log"].append(line)

    async def http_request(a: dict):
        u = urlparse(a["url"])
        plain_ok = u.scheme == "http" and str(u.hostname).endswith(".elb.amazonaws.com")  # an ALB's HTTP listener
        if (u.scheme != "https" and not plain_ok) or u.hostname not in hosts:
            raise AgentError(f"Only the project's own endpoints are allowed: {sorted(hosts) or 'none in the outputs'}. A flow with "
                             "no HTTP front door starts another way: sqs_send, sns_publish, events_put, s3_put or invoke_lambda.")
        import httpx

        headers = dict(line.split(":", 1) for line in a["headers"].splitlines() if ":" in line)
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=35, follow_redirects=False) as client:
            r = await client.request(a["method"], a["url"], headers={k.strip(): v.strip() for k, v in headers.items()},
                                     content=a["body"].encode() if a["body"] else None)
        ms = int((time.monotonic() - t0) * 1000)
        note(f"HTTP {a['method']} {u.path or '/'} → {r.status_code}")
        aws_access.audit(pid, agent, f"HTTPS {a['method']}", a["url"], r.status_code < 500, f"{r.status_code} in {ms} ms")
        await crewchat.say(pid, agent, "crew", f"🌐 {a['method']} {u.path or '/'} → **{r.status_code}** ({ms} ms)", "work")
        keep = ("content-type", "x-amzn-requestid", "x-amz-apigw-id", "x-amzn-errortype")
        return json.dumps({"status": r.status_code, "ms": ms, "headers": {k: v for k, v in r.headers.items() if k.lower() in keep},
                           "body": r.text[:6000]})

    async def sqs_send(a: dict):
        q = mine(a["queue_url"])

        def go():
            kw = {"QueueUrl": q, "MessageBody": a["body"]}
            if a["group_id"]:
                kw["MessageGroupId"] = a["group_id"]
            if a["dedup_id"]:
                kw["MessageDeduplicationId"] = a["dedup_id"]
            if a["attributes"]:
                try:
                    attrs = json.loads(a["attributes"])
                except ValueError as exc:
                    raise AgentError("attributes must be JSON {name: value}") from exc
                kw["MessageAttributes"] = {k: {"DataType": "String", "StringValue": str(v)} for k, v in attrs.items()}
            return aws_access.session_for(creds).client("sqs").send_message(**kw)["MessageId"]
        mid = await asyncio.to_thread(go)
        note(f"sent to {q.rsplit('/', 1)[-1]}")
        aws_access.audit(pid, agent, "sqs:SendMessage", q, True, mid)
        await crewchat.say(pid, agent, "crew", f"📨 sent a test message to {q.rsplit('/', 1)[-1]}", "work")
        return json.dumps({"message_id": mid})

    async def sqs_receive(a: dict):
        q = mine(a["queue_url"])

        def go():
            sqs = aws_access.session_for(creds).client("sqs")
            msgs = sqs.receive_message(QueueUrl=q, MaxNumberOfMessages=10, WaitTimeSeconds=max(0, min(int(a["wait_seconds"]), 20)),
                                       AttributeNames=["All"], MessageAttributeNames=["All"]).get("Messages", [])
            if msgs:  # always: peeking without deleting blocked FIFO groups and pushed good messages into the DLQ (10-02)
                sqs.delete_message_batch(QueueUrl=q, Entries=[{"Id": str(i), "ReceiptHandle": m["ReceiptHandle"]} for i, m in enumerate(msgs)])
            return [{"body": m["Body"][:6000], "attributes": {k: v for k, v in m.get("Attributes", {}).items()
                                                              if k in ("MessageGroupId", "MessageDeduplicationId", "SentTimestamp", "ApproximateReceiveCount")},
                     "message_attributes": {k: v.get("StringValue") for k, v in (m.get("MessageAttributes") or {}).items()}} for m in msgs]
        msgs = await asyncio.to_thread(go)
        note(f"{q.rsplit('/', 1)[-1]}: {len(msgs)} message(s)")
        aws_access.audit(pid, agent, "sqs:ReceiveMessage", q, True, f"{len(msgs)} message(s)")
        await crewchat.say(pid, agent, "crew", f"📬 {q.rsplit('/', 1)[-1]}: {len(msgs)} message(s)", "work")
        return json.dumps(msgs)

    async def invoke_lambda(a: dict):
        fn = mine(a["function_name"])

        def go():
            r = aws_access.session_for(creds).client("lambda").invoke(FunctionName=fn, Payload=a["payload"].encode(), LogType="Tail")
            return {"status": r["StatusCode"], "function_error": r.get("FunctionError"), "result": r["Payload"].read().decode(errors="replace")[:6000],
                    "log_tail": base64.b64decode(r.get("LogResult") or b"").decode(errors="replace")[-3000:]}
        out = await asyncio.to_thread(go)
        note(f"invoked {fn}" + (f" → {out['function_error']}" if out["function_error"] else " → ok"))
        aws_access.audit(pid, agent, "lambda:InvokeFunction", fn, not out["function_error"], out["function_error"] or "")
        await crewchat.say(pid, agent, "crew", f"⚡ invoked {fn}" + (f" → error {out['function_error']}" if out["function_error"] else " → ok"), "work")
        return json.dumps(out)

    async def read_logs(a: dict):
        group = mine(a["log_group"])

        def go():
            kw = {"logGroupName": group, "startTime": int((time.time() - 60 * max(1, min(int(a["minutes"]), 60))) * 1000), "limit": 100}
            if a["filter_pattern"]:
                kw["filterPattern"] = a["filter_pattern"]
            ev = aws_access.session_for(creds).client("logs").filter_log_events(**kw).get("events", [])
            return [e["message"][:1500] for e in ev][-60:]
        lines = await asyncio.to_thread(go)
        note(f"logs {group}: {len(lines)} event(s)")
        aws_access.audit(pid, agent, "logs:FilterLogEvents", group, True, f"{len(lines)} event(s)")
        return "\n".join(lines) or ("(no log events in that window. CloudWatch shows new events within ~10-20 s: wait and read again. "
                                    "If a function you ran still has none, its logs aren't reaching CloudWatch (usually the role's log "
                                    "permission): that's a real problem to report.)")

    s3 = lambda: aws_access.session_for(creds).client("s3")  # noqa: E731

    async def s3_list(a: dict):
        b = mine(a["bucket"])

        def go():
            objs = s3().list_objects_v2(Bucket=b, Prefix=a["prefix"], MaxKeys=1000).get("Contents", [])
            objs.sort(key=lambda o: o["LastModified"], reverse=True)
            return [{"key": o["Key"], "size": o["Size"], "modified": o["LastModified"].isoformat()} for o in objs[:50]]
        objs = await asyncio.to_thread(go)
        note(f"s3 list {b}/{a['prefix']}: {len(objs)} object(s)")
        aws_access.audit(pid, agent, "s3:ListBucket", b, True, f"{len(objs)} object(s)")
        await crewchat.say(pid, agent, "crew", f"🪣 {b}/{a['prefix']}: {len(objs)} object(s)", "work")
        return json.dumps(objs)

    async def s3_get(a: dict):
        b = mine(a["bucket"])

        def go():
            o = s3().get_object(Bucket=b, Key=a["key"])
            data = o["Body"].read(200_000)
            return {"key": a["key"], "size": o.get("ContentLength"), "content_type": o.get("ContentType"),
                    "body": data.decode("utf-8", errors="replace")[:6000]}
        out = await asyncio.to_thread(go)
        note(f"s3 get {b}/{a['key']}: {out['size']} bytes")
        aws_access.audit(pid, agent, "s3:GetObject", f"{b}/{a['key']}", True, f"{out['size']} bytes")
        await crewchat.say(pid, agent, "crew", f"🪣 read {a['key']} ({out['size']} bytes)", "work")
        return json.dumps(out)

    async def s3_put(a: dict):
        b = mine(a["bucket"])

        def go():
            kw = {"Bucket": b, "Key": a["key"], "Body": a["body"].encode()}
            if a["content_type"]:
                kw["ContentType"] = a["content_type"]
            return s3().put_object(**kw).get("ETag", "")
        etag = await asyncio.to_thread(go)
        note(f"s3 put {b}/{a['key']}")
        aws_access.audit(pid, agent, "s3:PutObject", f"{b}/{a['key']}", True, etag)
        await crewchat.say(pid, agent, "crew", f"🪣 uploaded a test object {a['key']}", "work")
        return json.dumps({"etag": etag})

    async def dynamodb_read(a: dict):
        t = mine(a["table"])

        def go():
            ddb = aws_access.session_for(creds).client("dynamodb")
            if a["key"].strip():
                try:
                    key = json.loads(a["key"])
                except ValueError as exc:
                    raise AgentError("key must be JSON, e.g. {\"order_id\": \"ORD1\"}") from exc
                from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
                item = ddb.get_item(TableName=t, Key={k: TypeSerializer().serialize(v) for k, v in key.items()}).get("Item")
                return [{k: TypeDeserializer().deserialize(v) for k, v in item.items()}] if item else []
            from boto3.dynamodb.types import TypeDeserializer
            items = ddb.scan(TableName=t, Limit=10).get("Items", [])
            return [{k: TypeDeserializer().deserialize(v) for k, v in it.items()} for it in items]
        items = await asyncio.to_thread(go)
        note(f"dynamodb {t}: {len(items)} item(s)")
        aws_access.audit(pid, agent, "dynamodb:Read", t, True, f"{len(items)} item(s)")
        await crewchat.say(pid, agent, "crew", f"🗄 {t}: {len(items)} item(s)", "work")
        return json.dumps(items, default=str)[:8000]

    async def events_put(a: dict):
        bus = a["bus"].strip() or "default"
        if bus != "default":
            mine(bus)
        try:
            json.loads(a["detail"])
        except ValueError as exc:
            raise AgentError("detail must be JSON") from exc

        def go():
            r = aws_access.session_for(creds).client("events").put_events(Entries=[{
                "EventBusName": bus, "Source": a["source"], "DetailType": a["detail_type"], "Detail": a["detail"]}])
            return r.get("FailedEntryCount", 0), (r.get("Entries") or [{}])[0]
        failed, entry = await asyncio.to_thread(go)
        note(f"event put on {bus}" + (" → failed" if failed else ""))
        aws_access.audit(pid, agent, "events:PutEvents", bus, not failed, entry.get("EventId") or entry.get("ErrorMessage", ""))
        await crewchat.say(pid, agent, "crew", f"📡 sent a test event ({a['source']} / {a['detail_type']}) to the {bus} bus", "work")
        return json.dumps(entry)

    async def sns_publish(a: dict):
        topic = mine(a["topic_arn"])

        def go():
            kw = {"TopicArn": topic, "Message": a["message"]}
            if a["subject"]:
                kw["Subject"] = a["subject"][:100]
            return aws_access.session_for(creds).client("sns").publish(**kw)["MessageId"]
        mid = await asyncio.to_thread(go)
        note(f"published to {topic.rsplit(':', 1)[-1]}")
        aws_access.audit(pid, agent, "sns:Publish", topic, True, mid)
        await crewchat.say(pid, agent, "crew", f"📣 published a test message to {topic.rsplit(':', 1)[-1]}", "work")
        return json.dumps({"message_id": mid})

    return [replace(HTTP, handler=http_request), replace(SQS_SEND, handler=sqs_send), replace(SQS_RECEIVE, handler=sqs_receive),
            replace(INVOKE, handler=invoke_lambda), replace(LOGS, handler=read_logs), replace(S3_LIST, handler=s3_list),
            replace(S3_GET, handler=s3_get), replace(S3_PUT, handler=s3_put), replace(DDB_READ, handler=dynamodb_read),
            replace(EVENTS_PUT, handler=events_put), replace(SNS_PUBLISH, handler=sns_publish)]


def deployment_brief(deployed: dict, inventory: dict | None) -> str:
    """What's live, for an agent's prompt: the outputs and the resources."""
    res = [r for r in (inventory or {}).get("resources", []) if r.get("primary")]
    return ("Terraform outputs:\n```json\n" + json.dumps(deployed.get("outputs") or {}, indent=2)[:6000] + "\n```\n"
            + ("Resources in AWS (with their AWS console page):\n" + "\n".join(f"- {r['kind']}: {r['name']}" + (f" · console: {r['console']}" if r.get("console") else "")
                                                                         for r in res[:60]) if res else
               "Resources Terra created:\n" + "\n".join(f"- {c['address']} {c['name']}" for c in ((deployed.get("plan") or {}).get("changes") or [])[:60])))
