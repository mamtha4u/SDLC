"""Resource names, owned by the user (the Naming section). No AI call.

Terra keeps every name in one place: `infra/names.tf` declares `variable "name_prefix"` and `variable "names"` (a map of
short key → the part after the prefix), and every resource is named "${var.name_prefix}-${var.names["key"]}". The user's
choices go to `infra/names.auto.tfvars.json`, which Terraform loads automatically and which wins over the defaults; the
next deploy plan then shows the renamed resources being replaced. The same names are swapped in the HLD, LLD, diagram
and previews, so every document says what will be built.
"""
from __future__ import annotations

import json
import re
import time

from app.agents.buildkit import files_under
from app.services.storage import ProjectStore

NAMES_TF = "infra/names.tf"
USER_NAMES = "infra/names.auto.tfvars.json"
DOCS = ("02_hld.md", "03_lld.md", "diagrams/architecture.json", "diagrams/architecture.drawio", "diagrams/design.json",
        "infra/plan_preview.json", "infra/README.md")
SUFFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
KIND = (  # what a key/name probably is, for the screen (Terra's preview gives the real type when it can)
    (r"dlq", "SQS dead-letter queue"), (r"\.fifo$|queue", "SQS queue"), (r"role", "IAM role"), (r"polic", "IAM policy"),
    (r"layer|lxml|logger", "Lambda layer"), (r"log", "CloudWatch log group"), (r"api|gw|gateway", "API Gateway"),
    (r"topic|sns", "SNS topic"), (r"bucket|s3", "S3 bucket"), (r"table|ddb|dynamo", "DynamoDB table"), (r"alarm", "CloudWatch alarm"),
)
TYPE_LABEL = {"aws_lambda_function": "Lambda function", "aws_sqs_queue": "SQS queue", "aws_iam_role": "IAM role",
              "aws_lambda_layer_version": "Lambda layer", "aws_cloudwatch_log_group": "CloudWatch log group",
              "aws_api_gateway_rest_api": "API Gateway (REST)", "aws_apigatewayv2_api": "API Gateway (HTTP)",
              "aws_sns_topic": "SNS topic", "aws_s3_bucket": "S3 bucket", "aws_dynamodb_table": "DynamoDB table",
              "aws_cloudwatch_metric_alarm": "CloudWatch alarm", "aws_iam_role_policy": "IAM inline policy"}


class NamingError(ValueError):
    pass


# The user's naming convention (user, 10-03: "by default orkestra; if the user wants a specific prefix we must allow it,
# from the requirement stage, or later"). Set on the AWS tab before Terra writes the infrastructure; Archie and Terra
# build with it (it wins over the requirement's build.naming). Once Terra's names.tf exists, names are edited directly.
CONVENTION = "naming/convention.json"


def convention(project_id: str) -> dict | None:
    try:
        return json.loads(ProjectStore(project_id).read(CONVENTION))
    except Exception:  # noqa: BLE001 (not set)
        return None


def set_convention(project_id: str, prefix: str, pattern: str = "", by: str = "") -> dict:
    from app.services import aws_access

    prefix = prefix.strip().lower().rstrip("-")
    if not prefix.startswith("orkestra-") or len(prefix) <= len("orkestra-"):
        raise NamingError("In this sandbox every name starts with orkestra-: add your team's part after it, e.g. orkestra-mint-dev")
    if not re.fullmatch(r"orkestra-[a-z0-9][a-z0-9-]{0,40}", prefix):
        raise NamingError("Use lowercase letters, digits and dashes after orkestra- (at most 40 characters)")
    problem = aws_access.prefix_problem(project_id, prefix)
    if problem:
        raise NamingError(problem)
    data = {"prefix": prefix, "pattern": pattern.strip()[:300], "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ProjectStore(project_id).write(CONVENTION, json.dumps(data, indent=2))
    return data


def convention_block(project_id: str) -> str:
    """For Archie's and Terra's prompts: the user's convention, if they set one after the requirement."""
    c = convention(project_id)
    if not c:
        return ""
    return (f"# The user's naming convention (set {c['at']}; it wins over the requirement)\n"
            f"- Prefix: `{c['prefix']}`: every resource name starts with it (name_prefix in infra/names.tf).\n"
            + (f"- After the prefix: {c['pattern']}\n" if c.get("pattern") else "- After the prefix: keep the team's style from the requirement.\n"))


def _block(text: str, name: str) -> str:
    m = re.search(rf'variable\s+"{name}"\s*{{', text)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1]


def _defaults(infra: dict[str, str]) -> tuple[str | None, dict[str, str]]:
    text = "\n".join(c for p, c in infra.items() if p.endswith(".tf"))
    m = re.search(r'default\s*=\s*"([^"$]+)"', _block(text, "name_prefix"))
    body = _block(text, "names")
    d = re.search(r"default\s*=\s*{", body)
    names: dict[str, str] = {}
    if d:
        depth, i = 1, d.end()
        while i < len(body) and depth:
            depth += {"{": 1, "}": -1}.get(body[i], 0)
            i += 1
        for k, v in re.findall(r'"?([A-Za-z_][A-Za-z0-9_-]*)"?\s*[=:]\s*"([^"$]*)"', body[d.end():i - 1]):
            names[k] = v
    return (m.group(1) if m else None), names


SECONDARY = ("aws_cloudwatch_log_group", "aws_lambda_permission", "aws_iam_role_policy", "aws_iam_role_policy_attachment")


def _used_by(infra: dict[str, str]) -> dict[str, str]:
    """name key → the Terraform resource type that uses it (a Lambda's key is also used by its log group: the
    function wins)."""
    text = "\n".join(c for p, c in infra.items() if p.endswith(".tf"))
    out: dict[str, str] = {}
    for m in re.finditer(r'resource\s+"(aws_[a-z0-9_]+)"\s+"[^"]+"\s*{', text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        for key in re.findall(r'var\.names(?:\["([^"]+)"\]|\.([A-Za-z_][A-Za-z0-9_]*))', text[m.end():i - 1]):
            k = key[0] or key[1]
            if k not in out or (out[k] in SECONDARY and m.group(1) not in SECONDARY):
                out[k] = m.group(1)
    return out


def state(project_id: str) -> dict:
    """{editable, reason, prefix, items:[{key, name, full, kind, default}], deployed}."""
    from app.agents.ta import load_design
    from app.agents.tp import load_preview
    from app.services import aws_access

    store = ProjectStore(project_id)
    infra = files_under(store, ("infra/",))
    prefix, names = _defaults(infra)
    deployed = (aws_access.load(project_id, "deploy") or {}).get("status") in ("deployed", "partial")
    if not names:
        design = load_design(project_id) or {}
        planned = [r for r in design.get("resources", []) if r.get("name") and " (" not in r["name"]  # not "x (versioning)" sub-settings
                   and "tfstate" not in r["name"] and r.get("type", "") in TYPE_LABEL]  # the platform owns the state bucket
        return {"editable": False, "deployed": deployed, "prefix": None, "convention": convention(project_id),
                "reason": "Names become editable once Terra writes the infrastructure (they're built from one names file). "
                          "Until then these are Archie's planned names." if design else "No resources are designed yet.",
                "items": [{"key": r["name"], "name": r["name"], "full": r["name"], "kind": TYPE_LABEL[r["type"]], "default": r["name"]}
                          for r in planned]}
    user = {}
    try:
        user = json.loads(infra.get(USER_NAMES) or "{}")
    except ValueError:
        pass
    eff_prefix = user.get("name_prefix") or prefix or ""
    eff = {**names, **{k: v for k, v in (user.get("names") or {}).items() if k in names}}
    preview = {r.get("name", ""): r for r in (load_preview(project_id) or {}).get("resources", [])}
    used_by = _used_by(infra)
    items = []
    for k, suffix in eff.items():
        full = f"{eff_prefix}-{suffix}"
        r = preview.get(full) or preview.get(f"/aws/lambda/{full}")
        kind = (TYPE_LABEL.get(used_by[k], used_by[k]) if k in used_by else TYPE_LABEL.get(r["type"], r["type"]) if r
                else next((lbl for pat, lbl in KIND if re.search(pat, f"{k} {suffix}", re.I)), "Resource"))
        items.append({"key": k, "name": suffix, "full": full, "kind": kind, "default": names[k]})
    return {"editable": True, "reason": "", "prefix": eff_prefix, "default_prefix": prefix, "items": items, "deployed": deployed,
            "convention": convention(project_id)}


def _swap(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:  # longest first, whole names only ("…-transform" never matches inside "…-transform-role")
        text = re.sub(rf"(?<![A-Za-z0-9_.-]){re.escape(old)}(?![A-Za-z0-9_-]|\.[A-Za-z0-9])", new, text)
    return text


def apply(project_id: str, prefix: str, names: dict[str, str], dry_run: bool = False) -> dict:
    """Validate and save the user's names; swap them in the documents. Returns {state, renamed:[(old, new)]}.
    `dry_run`: validate and work out the renames, change nothing (Orion reviews a live rename first)."""
    from app.services import aws_access

    before = state(project_id)
    if not before["editable"]:
        raise NamingError(before["reason"])
    prefix = prefix.strip()
    if prefix != before["prefix"]:
        problem = aws_access.prefix_problem(project_id, prefix)
        if problem:
            raise NamingError(problem)
    known = {i["key"]: i for i in before["items"]}
    clean: dict[str, str] = {}
    for k, v in names.items():
        if k not in known:
            raise NamingError(f"Unknown resource key {k!r}")
        v = v.strip()
        if not SUFFIX.fullmatch(v):
            raise NamingError(f"{known[k]['kind']} “{v}”: use letters, digits, dot, dash or underscore (start with a letter or digit)")
        if known[k]["name"].endswith(".fifo") and not v.endswith(".fifo"):
            raise NamingError(f"“{v}” must end with .fifo: it's a FIFO queue (AWS requires the suffix)")
        if len(f"{prefix}-{v}") > 64:
            raise NamingError(f"“{prefix}-{v}” is longer than 64 characters (AWS limit for Lambda functions and IAM roles)")
        clean[k] = v
    fulls = [f"{prefix}-{clean.get(k, i['name'])}" for k, i in known.items()]
    dup = {f for f in fulls if fulls.count(f) > 1}
    if dup:
        raise NamingError(f"Two resources would get the same name: {', '.join(sorted(dup))}")
    store = ProjectStore(project_id)
    user = {"name_prefix": prefix, "names": {k: clean.get(k, i["name"]) for k, i in known.items()}}
    pairs = sorted(((i["full"], f"{prefix}-{user['names'][k]}") for k, i in known.items() if i["full"] != f"{prefix}-{user['names'][k]}"),
                   key=lambda p: -len(p[0]))
    if dry_run:
        return {"state": before, "renamed": pairs}
    store.write(USER_NAMES, json.dumps(user, indent=2))
    if pairs:
        for path in DOCS:
            try:
                text = store.read(path).decode("utf-8")
            except Exception:  # noqa: BLE001  (a document that doesn't exist yet)
                continue
            new = _swap(text, pairs)
            if new != text:
                store.write(path, new)
        store.append_changelog(store.manifest()["current_version"], ["Renamed by the user: " + "; ".join(f"{a} → {b}" for a, b in pairs)])
    return {"state": state(project_id), "renamed": pairs}
