"""The crew's AWS access (Orion's per-agent roles), the deploy rules for Terraform, and the deploy API. No AWS calls."""
import json

import pytest
from fastapi.testclient import TestClient

from app.agents.base import AgentError
from app.agents.buildkit import WorkingSet
from app.main import app
from app.orchestrator.flow import STAGES
from app.services import aws_access
from app.services.storage import ProjectStore
from app.tools import terraform
from tests.conftest import signup
from tests.live_deploy import INFRA

PID = "prj_0123456789ab"
PREFIX = "orkestra-orders-dev"
FLOW = ["aws_lambda_function", "aws_sqs_queue", "aws_api_gateway_rest_api", "aws_iam_role", "aws_cloudwatch_log_group"]


def _infra(pid: str = PID) -> dict[str, str]:
    return {k: v.replace("__ID__", "abc123").replace("__PID__", pid) for k, v in INFRA.items() if k.startswith("infra/")}


def test_services_and_prefix_come_from_the_terraform():
    # any service is allowed now (user, 10-03); only account-wide / identity resources are refused
    svcs, refused = aws_access.services(FLOW + ["aws_kinesis_stream", "aws_ecs_service", "aws_instance", "aws_mq_broker", "aws_nat_gateway"])
    assert svcs == ["apigateway", "ec2", "ecs", "iam", "kinesis", "lambda", "logs", "mq", "sqs", "vpc"] and refused == []
    assert aws_access.services(["aws_default_vpc", "aws_iam_user", "aws_glue_job"]) == (["glue"], ["aws_default_vpc", "aws_iam_user"])
    assert aws_access.services(["aws_lambda_function"])[0] == ["iam", "lambda", "logs"]  # a function brings its role and logs
    assert aws_access.label("glue") == "Glue" and aws_access.label("mq") == "Amazon MQ brokers"
    assert aws_access.name_prefix(_infra()) == "orkestra-deploycheck-abc123"
    over = {**_infra(), "infra/terraform.tfvars": 'name_prefix = "orkestra-orders-prod"\n'}
    assert aws_access.name_prefix(over) == "orkestra-orders-prod"  # auto-loaded tfvars win
    names = [{"name": "orkestra-orders-dev-transform"}, {"name": "orkestra-orders-dev-api"}, {"name": "/orders"}]
    assert aws_access.name_prefix({}, names) == "orkestra-orders-dev"


@pytest.mark.parametrize("prefix,ok", [("orkestra-orders-dev", True), ("orkestra-", False), ("orkestra", False), (None, False),
                                       ("orkestra-host", False), ("orkestra-tfstate-x", False), ("orkestra-0123456789ab", False),
                                       ("Orkestra-Orders", False), ("colleague-app", False)])
def test_prefixes_that_could_reach_other_things_are_refused(client, prefix, ok):
    assert (aws_access.prefix_problem(PID, prefix) is None) == ok


def _acts(st: dict) -> list[str]:
    a = st.get("Action") or []
    return [a] if isinstance(a, str) else a


def _res(st: dict) -> list[str]:
    r = st.get("Resource") or []
    return [r] if isinstance(r, str) else r


USE_ONLY = {"ec2:RunInstances", "ec2:CreateNetworkInterface", "ec2:CreateSecurityGroup", "elasticache:Create*", "rds:Create*"}


def test_no_role_can_write_outside_the_project():
    """Every write is scoped to this project: its names, its tags (set only while creating), its state bucket; or it only
    *uses* existing subnets/AMIs/default groups without changing them."""
    svcs = aws_access.services(FLOW + ["aws_ecs_service", "aws_instance", "aws_mq_broker"])[0]
    reads = ("get", "list", "describe")  # incl. apigateway:GET
    for build in (aws_access.terra_policy, aws_access.dev_policy, aws_access.quinn_policy):
        pol = build(PID, PREFIX, svcs)
        assert len(json.dumps(pol, separators=(",", ":"))) < 10_240  # IAM's inline policy limit
        for st in pol["Statement"]:
            assert st["Effect"] == "Allow" and (st.get("Action") or st.get("NotAction")) and st["Resource"]
            if st.get("NotAction"):  # "any service": only on this project's names or tags, never identity
                assert {"iam:*", "sts:*"} <= set(st["NotAction"]), st["Sid"]
                assert all(PREFIX in r for r in _res(st)) or PID in json.dumps(st.get("Condition", {})), st["Sid"]
                continue
            writes = [a for a in _acts(st) if not a.split(":")[1].lower().startswith(reads) and a not in ("sts:GetCallerIdentity", "tag:GetResources", "ecr:GetAuthorizationToken")]
            if not writes:
                continue
            scoped = all(PREFIX in r or "orkestra-tfstate-0123456789ab" in r for r in _res(st))
            if not scoped and set(writes) <= USE_ONLY and "Condition" not in st:  # launch into existing subnets, AWS default groups
                assert not any("instance/" in r or "volume/" in r for r in _res(st)), st["Sid"]
                continue
            assert scoped or "Condition" in st, (build.__name__, st["Sid"])  # tag- or function-scoped otherwise
            if "Condition" in st and not scoped:
                c = json.dumps(st["Condition"])
                assert PID in c or PREFIX in c or "aws-service-role" in "".join(_res(st)), st["Sid"]


def test_tags_can_only_be_set_while_creating():
    """A colleague's resource can't be tagged project_id=<ours> to pull it into reach."""
    for st in aws_access.terra_policy(PID, PREFIX)["Statement"]:
        tagging = [a for a in _acts(st) if a in ("ec2:CreateTags", "ecs:TagResource", "tag:TagResources") or a.endswith(":TagResource")]
        if tagging and "Resource" in st and _res(st) == ["*"]:
            null = st["Condition"].get("Null", {})
            assert any(k.endswith(":CreateAction") and v == "false" for k, v in null.items()), st["Sid"]
    boundary = aws_access.platform_policies()["boundary"]
    for st in boundary["Statement"]:
        if st["Effect"] == "Allow" and _res(st) == ["*"] and any(a in ("ec2:CreateTags", "ecs:TagResource") for a in _acts(st)):
            assert any(k.endswith(":CreateAction") for k in st["Condition"].get("Null", {})), st["Sid"]


def test_terra_can_only_make_flow_roles_with_the_boundary():
    pol = aws_access.terra_policy(PID, PREFIX, aws_access.services(FLOW)[0])
    create = next(s for s in pol["Statement"] if "iam:CreateRole" in _acts(s))
    assert create["Condition"]["StringEquals"]["iam:PermissionsBoundary"] == aws_access.BOUNDARY_ARN
    attach = next(s for s in pol["Statement"] if "iam:AttachRolePolicy" in _acts(s))
    assert all(p.startswith("arn:aws:iam::aws:policy/") for p in attach["Condition"]["ArnLike"]["iam:PolicyARN"])
    passrole = next(s for s in pol["Statement"] if _acts(s) == ["iam:PassRole"])
    assert {"lambda.amazonaws.com", "ecs-tasks.amazonaws.com", "ec2.amazonaws.com"} <= set(passrole["Condition"]["StringEquals"]["iam:PassedToService"])
    assert all(PREFIX in r for r in _res(create) + _res(passrole))
    assert not any(a.startswith("iam:") for s in aws_access.quinn_policy(PID, PREFIX, ["lambda", "sqs"])["Statement"] for a in _acts(s))


def test_terra_builds_any_service_but_only_this_projects():
    pol = aws_access.terra_policy(PID, PREFIX)
    named = next(s for s in pol["Statement"] if s["Sid"] == "AnyServiceNamedForThisProject")
    assert f"arn:aws:ecs:*:*:*{PREFIX}*" in named["Resource"] and f"arn:aws:mq:*:*:*{PREFIX}*" in named["Resource"] and "iam:*" in named["NotAction"]
    tagged = next(s for s in pol["Statement"] if s["Sid"] == "AnyServiceTaggedForThisProject")
    assert tagged["Condition"] == {"StringEquals": {"aws:ResourceTag/project_id": PID}}
    create = next(s for s in pol["Statement"] if s["Sid"] == "CreateOnlyTaggedForThisProject")
    assert {"ec2:RunInstances", "ec2:CreateVpc", "ecs:CreateCluster", "mq:CreateBroker"} <= set(create["Action"])
    assert create["Condition"] == {"StringEquals": {"aws:RequestTag/project_id": PID}}
    in_vpc = next(s for s in pol["Statement"] if s["Sid"] == "CreateInAVpcOnlyTagged")  # never route tables of a colleague's VPC
    assert not any("route-table" in r for r in _res(in_vpc)) and "ec2:CreateVpcEndpoint" in in_vpc["Action"]


def test_the_boundary_fits_and_keeps_the_hard_lines():
    b = aws_access.platform_policies()["boundary"]
    assert len(json.dumps(b, separators=(",", ":"))) < 6_144
    deny = {s["Sid"]: s for s in b["Statement"] if s["Effect"] == "Deny"}
    assert {"RolesNeedThisBoundary", "NoLoosening", "NotThePlatform", "S3ObjectsOnlyOurs", "IrelandOnly"} <= set(deny)
    assert deny["NoLoosening"]["Resource"] == "*" and "iam:DeleteRolePermissionsBoundary" in deny["NoLoosening"]["Action"]
    assert "arn:aws:s3:::orkestra-deploy-*" in deny["NotThePlatform"]["Resource"]
    named = next(s for s in b["Statement"] if s["Sid"] == "Named")  # Terra's named services must all fit under the boundary
    assert named["Resource"] == [f"arn:aws:{s}:*:*:*orkestra-*" if s != "s3" else "arn:aws:s3:::orkestra-*" for s in aws_access.NAMED_SERVICES]
    assert {"elasticloadbalancing", "ecs", "ecr", "mq", "elasticache", "rds"} <= set(aws_access.NAMED_SERVICES)  # the team's core (ALB too)


def test_no_arn_names_a_wildcard_service():
    """IAM refuses `arn:aws:*:…` ("Resource vendor must be fully qualified"), though the simulator accepts it (10-03)."""
    docs = [aws_access.platform_policies()["boundary"], aws_access.platform_policies()["fence"], aws_access.terra_policy(PID, PREFIX),
            aws_access.dev_policy(PID, PREFIX, ["lambda", "ecs"], True, True), aws_access.quinn_policy(PID, PREFIX, ["lambda", "sqs"])]
    for d in docs:
        for st in d["Statement"]:
            for r in _res(st) + ([st["NotResource"]] if isinstance(st.get("NotResource"), str) else st.get("NotResource") or []):
                assert r == "*" or not r.startswith("arn:aws:*"), r


def test_role_and_bucket_names():
    assert aws_access.role_name(PID, "tp") == "orkestra-0123456789ab-terra"
    assert aws_access.role_name(PID, "qa").endswith("-quinn") and aws_access.role_name(PID, "de").endswith("-dev")
    assert aws_access.state_bucket("prj_AB_cd") == "orkestra-tfstate-ab-cd"  # S3 names: lowercase, no underscores


def test_the_platform_policies_on_disk_are_valid_and_fit():
    pol = aws_access.platform_policies()
    assert len(json.dumps(pol["fence"], separators=(",", ":"))) < 10_240 - 1_000  # room for the host role's other inline policies
    assert len(json.dumps(pol["boundary"], separators=(",", ":"))) < 6_144  # managed policy limit
    deny = {s["Sid"] for s in pol["fence"]["Statement"] if s["Effect"] == "Deny"}
    assert {"NeverChangeThePlatformOrTheBoundary", "IrelandOnlyExceptClaude"} <= deny
    region = next(s for s in pol["fence"]["Statement"] if s["Sid"] == "IrelandOnlyExceptClaude")
    assert "bedrock:*" in region["NotAction"] and "aws-marketplace:*" in region["NotAction"]  # denying these breaks Claude


def test_deploy_lint_rules():
    assert terraform.lint(_infra(), PID) == []  # the live deploy check's flow follows every rule
    base = _infra()

    def problems(extra: str) -> str:
        return " ".join(terraform.lint({**base, "infra/extra.tf": extra}, PID))
    assert "aws_iam_policy" in problems('resource "aws_iam_policy" "p" {\n  name = "orkestra-x"\n}\n')
    assert "aws_api_gateway_account" in problems('resource "aws_api_gateway_account" "a" {\n}\n')
    assert "permissions_boundary" in problems('resource "aws_iam_role" "r" {\n  name = "orkestra-x"\n  assume_role_policy = "{}"\n}\n')
    assert "service-role" in problems('resource "aws_iam_role_policy_attachment" "a" {\n  role = "x"\n'
                                      '  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"\n}\n')
    assert "dynamodb_table" in problems('terraform {\n  backend "s3" {\n    dynamodb_table = "locks"\n  }\n}\n')
    no_backend = {k: v.replace('backend "s3" {}', "") for k, v in base.items()}
    assert any("backend" in p for p in terraform.lint(no_backend, PID))


def test_working_set_keeps_unchanged_files_and_refuses_others():
    w = WorkingSet({"src/a.py": "a", "tests/test_a.py": "t"}, ("src/", "tests/"), "Dev")
    assert w.apply({"files": [{"path": "src/b.py", "content": "b"}], "delete": []}) == {"src/a.py": "a", "src/b.py": "b", "tests/test_a.py": "t"}
    assert w.apply({"files": [], "delete": ["src/a.py"]}) == {"src/b.py": "b", "tests/test_a.py": "t"}
    with pytest.raises(AgentError):
        w.apply({"files": [{"path": "infra/main.tf", "content": "x"}], "delete": []})
    with pytest.raises(AgentError):
        WorkingSet({}, ("src/",), "Dev").apply({"files": [], "delete": []})


def test_infrastructure_first_then_code_then_live_tests():
    # approve → Terra's kickoff (then tp.iac) → roles → create in AWS
    assert STAGES["design"]["next"] == "tp.talk" and STAGES["design"]["redo"] == "ta.design"
    assert STAGES["infra"]["next"] == "cto.grant"
    assert STAGES["infra_check"]["next"] == "cto.infra_ready" and STAGES["infra_check"]["redo"] == "tp.iac"  # you check AWS
    assert STAGES["code"]["next"] == "cto.tickets" and STAGES["live_bugs"]["next"] == "cto.tickets"
    assert STAGES["deploy"]["next"] == "tp.apply"
    assert STAGES["live"]["final"] and STAGES["live"]["next"] is None


def test_terra_deploys_devs_packages_rules():
    base = _infra()  # Terra deploys everything (user, 10-03): the reference flow follows every rule
    assert terraform.manages_code(base) and terraform.manages_layers(base)
    mine = "\n".join(v for k, v in base.items() if k not in terraform.PLATFORM_FILES)
    assert terraform.code_later(mine, True, True) == []
    assert terraform._code_deploy_keys(mine) == {"echo"}

    def problems(files: dict) -> str:
        return " ".join(terraform.lint(files, PID))
    ignored = {k: v.replace("  layers     = [", "  lifecycle {\n    ignore_changes = [filename, source_code_hash]\n  }\n  layers = [")
               for k, v in base.items()}
    assert "don't ignore filename, source_code_hash" in problems(ignored)  # every new package must show up in the plan
    no_lookup = {k: v.replace('lookup(var.code_packages, "echo", data.archive_file.placeholder_echo.output_path)',
                              "data.archive_file.placeholder_echo.output_path") for k, v in base.items()}
    assert "lookup(var.code_packages" in problems(no_lookup)
    other_key = {k: v.replace('lookup(local.code_hashes, "echo"', 'lookup(local.code_hashes, "other"') for k, v in base.items()}
    assert "different keys" in problems(other_key)
    wrong_key = {k: v.replace('"echo", data.archive_file', '"echo2", data.archive_file') for k, v in base.items()}
    assert "code_deploy\" has no function 'echo2'" in problems(wrong_key)
    packaged = {**base, "infra/extra.tf": 'data "archive_file" "x" {\n  type = "zip"\n  source_dir = "${path.module}/../src/echo"\n'
                                           '  output_path = "x.zip"\n}\n'}
    assert "placeholder" in problems(packaged)
    layered = {**base, "infra/extra.tf": 'resource "aws_lambda_layer_version" "l" {\n  layer_name = "orkestra-x"\n}\n'}
    assert "don't declare aws_lambda_layer_version yourself" in problems(layered)
    no_names = {k: v.replace("layer_names = {", "other_names = {") for k, v in base.items()}
    assert "layer_names" in problems(no_names)
    no_output = {k: v.split('output "code_deploy"')[0] for k, v in base.items()}
    assert "code_deploy" in problems(no_output)


def test_older_projects_keep_their_rules():
    """Before 10-03 Dev uploaded the code (functions ignore filename/source_code_hash); 10-02's layers.tf published layers."""
    base = _infra()
    legacy = {k: v for k, v in base.items() if k != terraform.PACKAGES_TF_PATH} | {terraform.LAYERS_TF_PATH: terraform.LAYERS_TF}
    assert terraform.manages_layers(legacy) and not terraform.manages_code(legacy)
    assert "needs lifecycle { ignore_changes = [filename, source_code_hash] }" in " ".join(terraform.lint(legacy, PID))
    ignored = {k: v.replace("  layers     = [", "  lifecycle {\n    ignore_changes = [filename, source_code_hash, layers]\n  }\n  layers = [")
               for k, v in legacy.items()}
    assert "don't ignore `layers`" in " ".join(terraform.lint(ignored, PID))


def test_dev_hands_code_and_layer_packages_to_terra(tmp_path):
    from app.agents import de

    pid = f"prj_pkgs{tmp_path.name[-6:]}"
    work = tmp_path / "code_work"
    (work / "build" / "layers").mkdir(parents=True)
    (work / "build" / "layers" / "util.zip").write_bytes(b"zip-1")
    (work / "src" / "echo").mkdir(parents=True)
    (work / "src" / "echo" / "handler.py").write_text("def handler(e, c):\n    return 1\n")
    (work / "src" / "echo" / "test_handler.py").write_text("x")  # never shipped
    built = [{"layer": "util", "zip": "build/layers/util.zip", "kb": 1, "key": "fp1"}]
    cd = {"functions": {"echo": {"function_name": "orkestra-x-echo", "source_dir": "src/echo/", "layers": ["util"]}},
          "layers": {"util": "orkestra-x-util"}}
    root = aws_access.deploy_dir(pid) / "work" / terraform.PACKAGES_DIR
    root.mkdir(parents=True)
    (root / "util.zip").write_bytes(b"old-flat")  # the 10-02 layout: moved under layers/
    handed = de.hand_over(pid, work, built, cd, "v1")
    assert [(h["key"], h["from"], h["function_name"], h["source_dir"]) for h in handed["code"]] == [("echo", "placeholder", "orkestra-x-echo", "src/echo")]
    assert [(h["key"], h["name"]) for h in handed["layers"]] == [("util", "orkestra-x-util")]
    assert (root / "code" / "echo.zip").read_bytes() == terraform.zip_folder(work / "src" / "echo")
    assert (root / "layers" / "util.zip").read_bytes() == b"zip-1" and not (root / "util.zip").exists()
    infra = {terraform.PACKAGES_TF_PATH: terraform.PACKAGES_TF, "infra/main.tf": ""}
    tfvars = json.loads(terraform.package_vars(infra, root)[terraform.PACKAGES_TFVARS])
    assert tfvars == {"code_packages": {"echo": "../packages/code/echo.zip"}, "layer_packages": {"util": "../packages/layers/util.zip"},
                      "image_packages": {}}
    rec = de.load_packages(pid)  # Terra applied them
    for kind in ("code", "layers"):
        for r in rec[kind].values():
            r.update(applied=r["fingerprint"], applied_version="v1")
    aws_access.save(pid, rec, de.PACKAGES)
    assert de.hand_over(pid, work, built, cd, "v1") == {"code": [], "layers": [], "images": []}  # nothing new: Dev checks and tests right away
    (work / "src" / "echo" / "handler.py").write_text("def handler(e, c):\n    return 2\n")
    again = de.hand_over(pid, work, built, cd, "v2")
    assert [(h["key"], h["from"], h["version"]) for h in again["code"]] == [("echo", "v1", "v2")] and again["layers"] == []
    legacy = {terraform.LAYERS_TF_PATH: terraform.LAYERS_TF, "infra/main.tf": ""}  # 10-02: layers only, in their own tfvars
    assert json.loads(terraform.package_vars(legacy, root)[terraform.LAYERS_TFVARS]) == {"layer_packages": {"util": "../packages/layers/util.zip"}}


class _FakeAws:
    """Just enough of boto3 for Dev's image path: ECR login, a container-image function, an ECS service."""

    def __init__(self, live: dict[str, str]):
        self.live = live

    def client(self, name: str):
        import base64

        live = self.live

        class C:
            def get_authorization_token(self):
                return {"authorizationData": [{"authorizationToken": base64.b64encode(b"AWS:secret").decode()}]}

            def get_function(self, FunctionName):
                return {"Code": {"ImageUri": live.get(FunctionName, "")}}

            def describe_services(self, cluster, services):
                return {"services": [{"taskDefinition": "td:1"}]}

            def describe_task_definition(self, taskDefinition):
                return {"taskDefinition": {"containerDefinitions": [{"image": live.get("ecs", "")}]}}
        return C()


def test_dev_builds_and_pushes_images_that_terra_deploys(tmp_path, monkeypatch):
    from app.agents import de

    pid = f"prj_imgs{tmp_path.name[-6:]}"
    work = tmp_path / "code_work"
    (work / "src" / "api").mkdir(parents=True)
    (work / "src" / "api" / "app.py").write_text("print('hi')\n")
    repo = "144831534428.dkr.ecr.eu-west-1.amazonaws.com/orkestra-x-api"
    cd = {"images": {"api": {"repository_url": repo, "source_dir": "src/api/", "ecs_cluster": "orkestra-x", "ecs_service": "orkestra-x-api"}}}
    calls: list[list[str]] = []
    digest = "sha256:" + "a" * 64

    def docker(args, cwd=None, env=None, stdin=None, timeout=1500):
        calls.append(args)
        assert env and env["DOCKER_CONFIG"]  # Dev's ECR login never lands in the host's own docker config
        return 0, (f"latest: digest: {digest} size: 1570" if args[0] == "push" else "ok")
    monkeypatch.setattr(de, "_docker", docker)
    monkeypatch.setattr(aws_access, "session_for", lambda creds: _FakeAws({"ecs": f"{repo}@{digest}"}))
    with pytest.raises(AgentError, match="Dockerfile"):
        de.push_images(pid, {}, work, cd, "v1")
    (work / "src" / "api" / "Dockerfile").write_text("FROM public.ecr.aws/docker/library/python:3.12-slim\n")
    assert de.push_images(pid, {}, work, cd, "v1") == [{"key": "api", "uri": f"{repo}@{digest}", "built": True}]
    assert [c[0] for c in calls] == ["login", "build", "push", "rmi"] and "--provenance=false" in calls[1]
    root = aws_access.deploy_dir(pid) / "work" / terraform.PACKAGES_DIR
    assert terraform.handed_over(root)["images"] == {"api": f"{repo}@{digest}"}
    infra = {terraform.PACKAGES_TF_PATH: terraform.PACKAGES_TF, "infra/main.tf": ""}
    assert json.loads(terraform.package_vars(infra, root)[terraform.PACKAGES_TFVARS])["image_packages"] == {"api": f"{repo}@{digest}"}
    calls.clear()
    assert de.push_images(pid, {}, work, cd, "v1")[0]["built"] is False and calls == []  # same folder: no rebuild
    pending = de.hand_over(pid, work, [], cd, "v1", code=False)
    assert [(i["key"], i["from"], i["uri"]) for i in pending["images"]] == [("api", "placeholder", f"{repo}@{digest}")]
    live = de.record_live(pid, {}, cd, "v1")  # ECS runs Dev's exact digest
    assert live["images"]["api"]["where"] == "orkestra-x/orkestra-x-api" and "🐳" in live["changed"][0]
    rec = de.load_packages(pid)
    rec["images"]["api"].update(applied=rec["images"]["api"]["fingerprint"], applied_version="v1")
    aws_access.save(pid, rec, de.PACKAGES)
    assert de.hand_over(pid, work, [], cd, "v1", code=False)["images"] == []  # live: nothing left for Terra
    monkeypatch.setattr(aws_access, "session_for", lambda creds: _FakeAws({"ecs": f"{repo}@sha256:{'b' * 64}"}))
    with pytest.raises(de.NotDevsCode, match="doesn't run Dev's image"):
        de.record_live(pid, {}, cd, "v1")
    fn = {"images": {"api": {**cd["images"]["api"], "function_name": "orkestra-x-fn"}}}  # a container-image Lambda
    monkeypatch.setattr(aws_access, "session_for", lambda creds: _FakeAws({"orkestra-x-fn": "someone-else@sha256:" + "c" * 64}))
    with pytest.raises(de.NotDevsCode, match="orkestra-x-fn runs image"):
        de.record_live(pid, {}, fn, "v1")


def test_the_any_service_reference_flow_follows_every_rule():
    """live_any_service's VPC + ALB → Lambda + ECR + ECS + EC2 + container-image Lambda: lint-clean, nothing refused."""
    import re

    from tests.live_any_service import INFRA as ANY
    files = {k: v.replace("__ID__", "abc123").replace("__PID__", PID).replace("__MYIP__", "1.2.3.4") for k, v in ANY.items() if k.startswith("infra/")}
    assert terraform.lint(files, PID) == []
    types = set(re.findall(r'resource\s+"([a-z0-9_]+)"', "\n".join(files.values())))
    assert aws_access.services(types) == (["ec2", "ecr", "ecs", "elb", "iam", "lambda", "logs", "vpc"], [])
    assert {"src/img/Dockerfile", "src/web/handler.py"} <= set(ANY)


def test_lint_rules_for_any_service():
    base = _infra()

    def problems(extra: str) -> str:
        return " ".join(terraform.lint({**base, "infra/extra.tf": extra}, PID))
    assert "force_delete = true" in problems('resource "aws_ecr_repository" "r" {\n  name = "orkestra-x-api"\n}\n')
    assert "force_delete" not in problems('resource "aws_ecr_repository" "r" {\n  name = "orkestra-x-api"\n  force_delete = true\n}\n')
    assert "skip_final_snapshot" in problems('resource "aws_db_instance" "d" {\n  identifier = "orkestra-x-db"\n}\n')
    assert "deletion_protection" in problems('resource "aws_db_instance" "d" {\n  identifier = "orkestra-x-db"\n'
                                             '  skip_final_snapshot = true\n  deletion_protection = true\n}\n')
    assert "skip_destroy = true" in problems('resource "aws_ecs_task_definition" "t" {\n  family = "orkestra-x-task"\n}\n')
    # 10-05: logs on the log group only → the function runs and every log line is dropped
    pol = 'resource "aws_iam_role_policy" "p" {\n  name = "orkestra-x-logs"\n  role = "x"\n  policy = jsonencode({ Statement = [{ Effect = "Allow",\n' \
          '    Action = ["logs:CreateLogStream", "logs:PutLogEvents"],\n    Resource = __RES__\n  }] })\n}\n'
    own = {k: v.replace("service-role/AWSLambdaBasicExecutionRole", "service-role/AWSLambdaSQSQueueExecutionRole") for k, v in base.items()}
    for res, bad in (("aws_cloudwatch_log_group.l.arn", True), ('"arn:aws:logs:eu-west-1:1:log-group:/aws/lambda/orkestra-x"', True),
                     ('"${aws_cloudwatch_log_group.l.arn}:*"', False), ('"arn:aws:logs:eu-west-1:1:log-group:/aws/lambda/orkestra-x:*"', False)):
        assert ("log streams" in " ".join(terraform.lint({**own, "infra/extra.tf": pol.replace("__RES__", res)}, PID))) == bad, res
    server = 'resource "aws_instance" "w" {\n  ami = data.aws_ami.al2023.id\n__LC__}\n'
    assert "ignore_changes = [ami]" in problems(server.replace("__LC__", ""))  # a new weekly AMI must not rebuild the server
    assert "aws_instance" not in problems(server.replace("__LC__", "  lifecycle {\n    ignore_changes = [ami]\n  }\n"))
    assert "default resources" in problems('resource "aws_default_vpc" "v" {\n}\n')  # colleagues share the default VPC
    assert "Route 53" in problems('resource "aws_route53_zone" "z" {\n  name = "orkestra.example.com"\n}\n')
    cache = 'resource "aws_elasticache_replication_group" "c" {\n  replication_group_id = "orkestra-x-cache"\n' \
            '  parameter_group_name = "default.redis7"\n}\n'
    assert "default.redis7" not in problems(cache)  # AWS's own default groups are used, not created
    assert "AmazonSSMManagedInstanceCore" not in problems(  # an EC2 instance profile may use SSM's managed policy
        'resource "aws_iam_role_policy_attachment" "a" {\n  role = "x"\n  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"\n}\n')
    image_fn = 'resource "aws_lambda_function" "img" {\n  function_name = "orkestra-x-img"\n  package_type = "Image"\n  role = "x"\n__BODY__}\n'
    assert "is a container image" in problems(image_fn.replace("__BODY__", ""))
    good = image_fn.replace("__BODY__", '  count = contains(keys(var.image_packages), "img") ? 1 : 0\n  image_uri = var.image_packages["img"]\n')
    assert "img" not in problems(good)
    ignored = good.replace("}\n", "  lifecycle {\n    ignore_changes = [image_uri]\n  }\n}\n", 1)
    assert "don't ignore image_uri" in problems(ignored)


def test_dev_role_loses_deploy_rights_when_terra_deploys():
    pol = aws_access.dev_policy(PID, PREFIX, aws_access.services(FLOW)[0], terra_layers=True)
    acts = {a for s in pol["Statement"] for a in s["Action"]}
    assert "lambda:UpdateFunctionCode" in acts and not {"lambda:PublishLayerVersion", "lambda:UpdateFunctionConfiguration"} & acts
    pol = aws_access.dev_policy(PID, PREFIX, aws_access.services(FLOW)[0], terra_layers=True, terra_code=True)
    acts = {a for s in pol["Statement"] for a in s["Action"]}
    assert {"lambda:GetFunctionConfiguration", "lambda:InvokeFunction", "sqs:ReceiveMessage"} <= acts
    assert not {"lambda:UpdateFunctionCode", "lambda:PublishLayerVersion", "lambda:UpdateFunctionConfiguration"} & acts
    assert "Terra deploys his code, images and layers" in " ".join(aws_access._can("de", PREFIX, PID, ["lambda", "sqs"], True, True))
    ecr = next(s for s in pol["Statement"] if s["Sid"] == "PushImagesToTheseRepositories")  # his container images, this project's repos only
    assert ecr["Resource"] == [f"arn:aws:ecr:eu-west-1:144831534428:repository/{PREFIX}*"] and "ecr:PutImage" in ecr["Action"]


def test_dev_deploys_code_but_cant_touch_anything_else():
    pol = aws_access.dev_policy(PID, PREFIX, aws_access.services(FLOW)[0])
    acts = {a: s for s in pol["Statement"] for a in s["Action"]}
    assert {"lambda:UpdateFunctionCode", "lambda:PublishLayerVersion", "sqs:SendMessage", "logs:FilterLogEvents"} <= set(acts)
    assert all(PREFIX in r for r in acts["lambda:UpdateFunctionCode"]["Resource"])
    assert not any(a.startswith(("iam:", "lambda:Create", "lambda:Delete", "sqs:Create", "sqs:Delete")) and a != "sqs:DeleteMessage"
                   for a in acts if a != "lambda:DeleteLayerVersion")


def test_function_zip_is_reproducible(tmp_path):
    (tmp_path / "fn").mkdir()
    (tmp_path / "fn" / "handler.py").write_text("def handler(e, c):\n    return 1\n")
    (tmp_path / "fn" / "__pycache__").mkdir()
    (tmp_path / "fn" / "__pycache__" / "x.pyc").write_bytes(b"junk")
    a = terraform.zip_folder(tmp_path / "fn")
    (tmp_path / "fn" / "handler.py").touch()
    assert terraform.zip_folder(tmp_path / "fn") == a and b"x.pyc" not in a
    for junk in ("test_handler.py", "conftest.py", "README.md", "tests/test_x.py"):  # never shipped to AWS
        (tmp_path / "fn" / junk).parent.mkdir(exist_ok=True)
        (tmp_path / "fn" / junk).write_text("x")
    import io
    import zipfile
    assert zipfile.ZipFile(io.BytesIO(terraform.zip_folder(tmp_path / "fn"))).namelist() == ["handler.py"]


def test_terraform_workspace_sync_keeps_providers_and_drops_stale_files(tmp_path):
    work = tmp_path / "work"
    terraform.prepare(work, {"infra/main.tf": "a", "src/fn/handler.py": "x"})
    (work / "infra" / ".terraform").mkdir()
    (work / "infra" / ".terraform" / "p").write_text("provider")
    (work / "build").mkdir()
    (work / "build" / "fn.zip").write_text("zip")
    mtime = (work / "src/fn/handler.py").stat().st_mtime_ns
    terraform.prepare(work, {"infra/main.tf": "b", "src/fn/handler.py": "x"})
    assert (work / "infra/main.tf").read_text() == "b" and (work / "src/fn/handler.py").stat().st_mtime_ns == mtime
    terraform.prepare(work, {"infra/main.tf": "b"})
    assert not (work / "src/fn/handler.py").exists()
    assert (work / "infra/.terraform/p").exists() and (work / "build/fn.zip").exists()


def test_access_api_and_teardown_guards(client):
    c = TestClient(app)
    signup(c, "deployer")
    p = c.post("/api/projects", json={"name": "Deploy"}).json()
    a = c.get(f"/api/projects/{p['id']}/access").json()
    assert a["platform"]["role"] == "orkestra-host-role" and a["boundary"]["policy"]["Statement"] and a["plan"] is None
    assert c.post(f"/api/projects/{p['id']}/deploy/teardown", json={"confirm": "nope"}).status_code == 422
    assert c.post(f"/api/projects/{p['id']}/deploy/teardown", json={"confirm": "Deploy"}).status_code == 409  # nothing in AWS
    aws_access.save(p["id"], {"status": "deployed", "outputs": {}}, "deploy")
    r = c.delete(f"/api/projects/{p['id']}", params={"confirm": "Deploy"})
    assert r.status_code == 409 and "Tear it down" in r.json()["detail"]  # never orphan resources in the shared account
    aws_access.save(p["id"], {"status": "destroyed"}, "deploy")
    assert c.delete(f"/api/projects/{p['id']}", params={"confirm": "Deploy"}).status_code == 204
    assert not ProjectStore(p["id"]).root.exists()
