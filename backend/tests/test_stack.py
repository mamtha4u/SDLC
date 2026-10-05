"""The tech stack on Archie's Design page, read from the project's files (no AI call)."""
from app.services.stack import stack
from app.services.storage import ProjectStore
from tests.conftest import signup
from tests.live_deploy import INFRA


def test_tech_stack_comes_from_the_files(client):
    signup(client, "stacker")
    pid = client.post("/api/projects", json={"name": "Stack"}).json()["id"]
    s = ProjectStore(pid)
    for p, c in INFRA.items():
        s.write(p, c.replace("__ID__", "abc").replace("__PID__", pid))
    s.write("src/echo/handler.py", "import json\nimport boto3\nfrom lxml import etree\nfrom echo_util import tag\n")
    s.write("layers/lxml/requirements.txt", "lxml==5.3.0  # XML\n")
    s.write("tests/test_handler.py", "import pytest\nfrom moto import mock_aws\n")
    st = stack(pid)
    assert st["language"]["name"] == "Python" and st["language"]["version"] == "3.14"
    assert st["runtime"]["lambda"] == ["python3.14"] and st["iac"]["tool"] == "Terraform" and st["iac"]["version"] == ">= 1.10"
    assert {"name": "hashicorp/aws", "version": "~> 6.0"} in st["iac"]["providers"]
    assert st["packages"] == [{"name": "lxml", "version": "==5.3.0", "where": "layer lxml", "from": "layers/lxml/requirements.txt"}]
    assert st["imports"]["third_party"] == ["lxml"] and st["imports"]["aws_sdk"] == ["boto3"] and "echo_util" in st["imports"]["own"]
    assert st["imports"]["undeclared"] == [] and "json" in st["imports"]["stdlib"]
    assert [t["name"] for t in st["tests"]] == ["moto", "pytest", "pytest-cov"]
    assert "SQS queues" in st["services"] and st["gates"]["coverage"]
    assert client.get(f"/api/projects/{pid}/design/stack").json()["language"]["name"] == "Python"
