import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agents.base import AgentError
from app.agents.buildkit import checked_files
from app.main import app
from app.orchestrator.flow import STAGES
from app.orchestrator.runner import HANDLERS
from app.services.storage import ProjectStore
from app.tools import pytest_runner, sandbox, terraform
from tests.conftest import signup

GOOD_TF = {
    "infra/providers.tf": '''provider "aws" {
  region = "eu-west-1"
  default_tags {
    tags = { created_by = "orkestra", project_id = "prj_x" }
  }
}
''',
    "infra/variables.tf": 'variable "name_prefix" {\n  default = "orkestra-ac-cd-dl-dev"\n}\n',
    "infra/sqs.tf": 'resource "aws_sqs_queue" "q" {\n  name = "${var.name_prefix}-orders.fifo"\n  fifo_queue = true\n  tags = { name = "orders" }\n}\n',
    "infra/backend.tf": 'terraform {\n  backend "s3" {}\n}\n',
    "infra/names.tf": 'variable "names" {\n  type    = map(string)\n  default = { orders = "orders.fifo" }\n}\n',
}


def test_terraform_sandbox_rules_pass_for_compliant_code():
    assert terraform.lint(GOOD_TF, "prj_x") == []
    logs = {**GOOD_TF, "infra/logs.tf": 'resource "aws_cloudwatch_log_group" "fn" {\n  name = "/aws/lambda/${var.name_prefix}-transform"\n}\n'
                                         'resource "aws_cloudwatch_log_group" "x" {\n  name = "/aws/lambda/orkestra-x"\n}\n'}
    assert terraform.lint(logs, "prj_x") == []  # AWS's own /aws/lambda/<function> naming is fine
    bad = {**GOOD_TF, "infra/logs.tf": 'resource "aws_cloudwatch_log_group" "fn" {\n  name = "/aws/lambda/colleague-fn"\n}\n'}
    assert terraform.lint(bad, "prj_x")


def test_terraform_sandbox_rules_catch_violations():
    bad = {**GOOD_TF,
           "infra/providers.tf": 'provider "aws" {\n  region = "us-east-1"\n}\n',
           "infra/lambda.tf": 'resource "aws_lambda_function" "f" {\n  function_name = "transform"\n  provisioner "local-exec" {}\n}\n',
           "infra/versions.tf": 'terraform {\n  required_providers {\n    evil = { source = "someone/evil" }\n  }\n}\n'}
    problems = " ".join(terraform.lint(bad, "prj_x"))
    assert "eu-west-1" in problems and "default_tags" in problems and "transform" in problems
    assert "someone/evil" in problems and "provisioners" in problems


def test_agents_can_only_write_their_own_folders():
    assert checked_files([{"path": "src/fn/handler.py", "content": "x"}], ("src/", "tests/"), "Dev") == {"src/fn/handler.py": "x"}
    for path in ("infra/main.tf", "../etc/passwd", "src/../infra/x.tf", "src/fn/app.exe"):
        with pytest.raises(AgentError):
            checked_files([{"path": path, "content": "x"}], ("src/", "tests/"), "Dev")


def test_replace_files_drops_an_agents_stale_files_only(client):
    s = ProjectStore("prj_replacecheck")
    s.create("x")
    s.write("src/old.py", "old")
    s.write("01_data_mapping.md", "keep")
    removed = s.replace_files(("src/",), {"src/new.py": "new"})
    paths = {f["path"] for f in s.tree()}
    assert removed == ["src/old.py"] and paths == {"src/new.py", "01_data_mapping.md"}
    s.delete()


def test_every_stage_hands_over_to_a_real_job():
    for name, st in STAGES.items():
        assert st["redo"] in HANDLERS, name
        assert st["next"] is None or st["next"] in HANDLERS, name


def test_build_api_shape(client):
    c = TestClient(app)
    signup(c, "builder")
    pid = c.post("/api/projects", json={"name": "Build"}).json()["id"]
    b = c.get(f"/api/projects/{pid}/build").json()
    assert set(b) == {"infra", "code", "qa", "deploy", "tickets", "handover"} and b["code"]["gates"]["min_coverage_percent"] == 70
    assert b["deploy"]["access"] is None and b["deploy"]["state"] is None and b["tickets"]["total"] == 0
    assert b["handover"] == {"mode": "dev", "code": [], "layers": [], "images": [], "pending": False, "planned": False,
                             "plan": None}  # no Terraform yet


@pytest.mark.skipif(not sandbox.available(), reason="Docker sandbox not installed")
def test_sandbox_pytest_with_coverage_and_moto():
    files = {
        "src/fn/handler.py": "import boto3\n\ndef send(url, body):\n    boto3.client('sqs').send_message(QueueUrl=url, MessageBody=body)\n    return 'ok'\n\n"
                             "def unused():\n    return 1\n",
        "tests/test_fn.py": "import boto3\nfrom moto import mock_aws\nfrom fn.handler import send\n\n@mock_aws\ndef test_send():\n"
                            "    url = boto3.client('sqs').create_queue(QueueName='orkestra-q')['QueueUrl']\n    assert send(url, 'x') == 'ok'\n",
    }
    r = asyncio.run(pytest_runner.run(files, ["tests"], coverage=True))
    assert r["ok"] and r["passed"] == 1, r["output"]
    assert 0 < r["coverage"]["percent"] < 100 and r["coverage"]["files"]["src/fn/handler.py"]["missing"]
