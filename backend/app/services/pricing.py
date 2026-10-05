"""What the flow costs: AWS list prices for eu-west-1 and a cost model per resource.

Prices come from the public AWS Price List (offer files per service and region, no credentials needed), never from
memory: every price carries its source URL and AWS's publication date. They're cached for a week in the data dir; a
snapshot in config/pricing_eu-west-1.json is the fallback when AWS can't be reached. The UI computes any volume from
`model()` + `prices()` (requests, compute, storage, with AWS's own tiers), so moving the slider is instant.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request

from app.core.config import BACKEND_DIR, get_settings

REGION = "eu-west-1"
SOURCE = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{service}/current/eu-west-1/index.json"
WANT = {  # key → (service code, usage type in the offer file)
    "lambda_requests": ("AWSLambda", "EU-Request"), "lambda_requests_arm": ("AWSLambda", "EU-Request-ARM"),
    "lambda_gbs": ("AWSLambda", "EU-Lambda-GB-Second"), "lambda_gbs_arm": ("AWSLambda", "EU-Lambda-GB-Second-ARM"),
    "sqs_standard": ("AWSQueueService", "EU-Requests-Tier1"), "sqs_fifo": ("AWSQueueService", "EU-Requests-FIFO-Tier1"),
    "sqs_free": ("AWSQueueService", "Global-Requests"),
    "apigw_rest": ("AmazonApiGateway", "EU-ApiGatewayRequest"), "apigw_http": ("AmazonApiGateway", "EU-ApiGatewayHttpRequest"),
    "logs_ingest": ("AmazonCloudWatch", "EU-DataProcessing-Bytes"), "logs_storage": ("AmazonCloudWatch", "EU-TimedStorage-ByteHrs"),
    "alarm": ("AmazonCloudWatch", "EU-CW:AlarmMonitorUsage"), "sns_requests": ("AmazonSNS", "EU-Requests-Tier1"),
}
SNAPSHOT = BACKEND_DIR / "config" / "pricing_eu-west-1.json"
MAX_AGE = 7 * 86400
FREE = ("aws_iam_", "aws_api_gateway_resource", "aws_api_gateway_method", "aws_api_gateway_integration", "aws_api_gateway_deployment",
        "aws_api_gateway_stage", "aws_api_gateway_method_settings", "aws_apigatewayv2_route", "aws_apigatewayv2_integration",
        "aws_apigatewayv2_stage", "aws_lambda_permission", "aws_lambda_event_source_mapping", "aws_sqs_queue_policy",
        "aws_sqs_queue_redrive", "aws_cloudwatch_event_target", "aws_sns_topic_subscription", "aws_sns_topic_policy")


def extract(service: str, offer: dict) -> dict:
    """The prices we use from one offer file: key → {tiers: [{from, to, usd}], unit, description}."""
    out = {}
    for key, (svc, usage) in WANT.items():
        if svc != service:
            continue
        for sku, p in offer.get("products", {}).items():
            if p.get("attributes", {}).get("usagetype") != usage:
                continue
            tiers, unit, desc = [], "", ""
            for term in offer["terms"]["OnDemand"].get(sku, {}).values():
                for dim in term["priceDimensions"].values():
                    end = dim.get("endRange")
                    tiers.append({"from": float(dim["beginRange"]), "to": None if end in (None, "Inf") else float(end),
                                  "usd": float(dim["pricePerUnit"]["USD"])})
                    unit, desc = dim["unit"], dim["description"] if not desc else desc
            out[key] = {"tiers": sorted(tiers, key=lambda t: t["from"]), "unit": unit, "description": desc}
            break
    return out


def _fetch() -> dict:
    data = {"region": REGION, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": {}, "prices": {}}
    for service in sorted({s for s, _ in WANT.values()}):
        url = SOURCE.format(service=service)
        with urllib.request.urlopen(url, timeout=90) as r:  # noqa: S310 (fixed https AWS URL)
            offer = json.loads(r.read())
        data["sources"][service] = {"url": url, "published": offer.get("publicationDate")}
        data["prices"].update(extract(service, offer))
    missing = set(WANT) - set(data["prices"])
    if missing:
        raise ValueError(f"AWS price list: no price for {sorted(missing)}")
    return data


_lock = asyncio.Lock()


async def prices() -> dict:
    cache = get_settings().data_dir / "pricing.json"
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if time.time() - cache.stat().st_mtime < MAX_AGE:
            return cached
    except (OSError, ValueError):
        cached = None
    async with _lock:
        try:
            data = await asyncio.to_thread(_fetch)
            cache.write_text(json.dumps(data), encoding="utf-8")
            return data
        except Exception as exc:  # noqa: BLE001  (no internet / AWS changed the file): last good copy, else the snapshot
            fallback = cached or json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            return {**fallback, "stale": f"Couldn't refresh the AWS price list ({str(exc)[:120]}); showing prices fetched {fallback.get('fetched_at')}"}


def _blocks(text: str) -> dict[str, str]:
    """address → body of every resource block in the Terraform."""
    out = {}
    for m in re.finditer(r'resource\s+"(aws_[a-z0-9_]+)"\s+"([^"]+)"\s*{', text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out[f"{m.group(1)}.{m.group(2)}"] = text[m.end():i - 1]
    return out


def _num(body: str, key: str) -> float | None:
    m = re.search(rf"^\s*{key}\s*=\s*(\d+(?:\.\d+)?)\s*$", body, re.M)
    return float(m.group(1)) if m else None


def model(project_id: str) -> dict:
    """Every resource with how it's charged and the settings that drive it, from the live inventory when there is one,
    else from Terra's Terraform and preview (before anything exists)."""
    from app.agents.buildkit import files_under
    from app.agents.tp import load_preview
    from app.services import inventory
    from app.services.storage import ProjectStore

    preview = load_preview(project_id) or {}
    terra = {r.get("address"): r for r in preview.get("resources", [])}
    inv = inventory.load(project_id)
    text = "\n".join(c for p, c in files_under(ProjectStore(project_id), ("infra/",)).items() if p.endswith(".tf"))
    blocks = _blocks(text)
    rows: list[dict] = []
    if inv and inv.get("resources"):
        for r in inv["resources"]:
            vals = {i["key"]: i["value"] for i in r["set"] + r["defaults"] + r["facts"]}
            rows.append({"address": r["address"], "type": r["type"], "name": r["name"], "kind": r["kind"], "service": r["service"], "values": vals})
    else:
        for addr, body in blocks.items():
            t = addr.split(".")[0]
            nm = (terra.get(addr) or {}).get("name") or addr
            vals: dict = {}
            for k in ("memory_size", "timeout", "retention_in_days"):
                if (n := _num(body, k)) is not None:
                    vals[k] = n
            vals["architectures"] = ["arm64"] if re.search(r'architectures\s*=\s*\[\s*"arm64"', body) else ["x86_64"]
            vals["fifo_queue"] = bool(re.search(r"fifo_queue\s*=\s*true", body)) or nm.endswith(".fifo")
            rows.append({"address": addr, "type": t, "name": nm, "kind": inventory.KIND.get(t, t), "service": inventory._service(t)[0], "values": vals})
    dlq_names = set()
    for r in rows:
        rp = r["values"].get("redrive_policy")
        if isinstance(rp, str) and "deadLetterTargetArn" in rp:
            try:
                dlq_names.add(json.loads(rp)["deadLetterTargetArn"].rsplit(":", 1)[-1])
            except (ValueError, KeyError):
                pass
    for addr, body in blocks.items():  # before the inventory exists: the DLQ is whatever a redrive policy points at
        if m := re.search(r"deadLetterTargetArn\s*=\s*aws_sqs_queue\.([a-z0-9_]+)\.arn", body):
            dlq_names.add(f"aws_sqs_queue.{m.group(1)}")
    out = []
    for r in rows:
        t, v = r["type"], r["values"]
        base = r["address"].split("[")[0]
        params: dict = {}
        if t == "aws_lambda_function":
            how = "lambda"
            params = {"memory_mb": int(v.get("memory_size") or 128), "arch": "arm64" if "arm64" in (v.get("architectures") or []) else "x86_64",
                      "timeout_s": v.get("timeout")}
        elif t == "aws_sqs_queue":
            how = "sqs"
            params = {"fifo": bool(v.get("fifo_queue")) or str(r["name"]).endswith(".fifo"),
                      "dlq": r["name"] in dlq_names or base in dlq_names or bool(re.search(r"dlq|dead", str(r["name"]), re.I))}
        elif t == "aws_api_gateway_rest_api":
            how = "apigw_rest"
        elif t == "aws_apigatewayv2_api":
            how = "apigw_http" if str(v.get("protocol_type", "HTTP")).upper() == "HTTP" else "unpriced"
        elif t == "aws_cloudwatch_log_group":
            how = "logs"
            params = {"retention_days": int(v.get("retention_in_days") or 0)}
        elif t == "aws_cloudwatch_metric_alarm":
            how = "alarm"
        elif t == "aws_sns_topic":
            how = "sns"
        elif t.startswith(FREE):
            how = "free"
        else:
            how = "unpriced"
        tr = terra.get(base) or terra.get(r["address"]) or next((x for x in terra.values() if x.get("name") == r["name"]), None)
        out.append({"address": r["address"], "type": t, "name": r["name"], "kind": r["kind"], "service": r["service"], "model": how,
                    "params": params, "terra_usd": (tr or {}).get("monthly_usd")})
    return {"resources": out, "from": "aws" if inv and inv.get("resources") else "terraform", "terra_total": sum(x.get("monthly_usd") or 0 for x in terra.values())}
