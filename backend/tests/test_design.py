import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import diagram
from tests.conftest import signup

ARCH = {
    "title": "Orders XML → JSON → SQS", "subtitle": "dev · eu-west-1",
    "sources": [{"id": "partner", "label": "Partner system", "sub": "HTTPS POST", "icon": "users", "details": []}],
    "path": [
        {"id": "apigw", "label": "API Gateway", "sub": "REST · POST /orders", "icon": "api_gateway", "details": []},
        {"id": "fn", "label": "orkestra-lmb-transform", "sub": "Python 3.14 · 256 MB", "icon": "lambda",
         "details": ["Transform: OrderId → order_id", "Validation: 400 on missing fields", "Logging: logger layer"]},
    ],
    "destinations": [{"id": "q", "label": "orders.fifo", "sub": "SQS FIFO", "icon": "sqs", "details": []},
                     {"id": "dlq", "label": "orders-dlq", "sub": "maxReceiveCount 3", "icon": "sqs", "details": []}],
    "support": [{"id": "role", "label": "Execution role", "sub": "least privilege", "icon": "iam_role", "under": "fn"},
                {"id": "logs", "label": "CloudWatch Logs", "sub": "30 days", "icon": "cloudwatch_logs", "under": "fn"}],
    "edges": [{"from": "partner", "to": "apigw", "label": "HTTPS", "kind": "data"},
              {"from": "apigw", "to": "fn", "label": "invoke", "kind": "data"},
              {"from": "fn", "to": "q", "label": "SendMessage", "kind": "data"},
              {"from": "q", "to": "dlq", "label": "redrive", "kind": "error"},
              {"from": "fn", "to": "role", "label": "assumes", "kind": "support"},
              {"from": "fn", "to": "logs", "label": "logs", "kind": "support"}],
}


def test_build_draws_a_valid_drawio_with_straight_data_path():
    assert diagram.validate(ARCH) == []
    xml = diagram.build(ARCH, "test")
    root = ET.fromstring(xml)
    cells = {c.get("id"): c for c in root.iter("mxCell")}
    for e in [c for c in cells.values() if c.get("edge") == "1"]:
        assert e.get("source") in cells and e.get("target") in cells
    assert "resIcon=mxgraph.aws4.lambda" in xml and "grIcon=mxgraph.aws4.group_region" in xml
    ys = [float(cells[i].find("mxGeometry").get("y")) + float(cells[i].find("mxGeometry").get("height")) / 2 for i in ("apigw", "fn", "q")]
    assert max(ys) - min(ys) < 1  # one centre line → straight arrows
    assert diagram.editor_url(xml).startswith("https://app.diagrams.net/")


def test_validate_reports_what_to_fix():
    bad = {**ARCH, "edges": [{"from": "fn", "to": "nowhere", "label": "", "kind": "data"}],
           "support": [{"id": "x", "label": "x", "sub": "", "icon": "teleporter", "under": "ghost"}]}
    problems = " ".join(diagram.validate(bad))
    assert "nowhere" in problems and "teleporter" in problems and "ghost" in problems


def test_user_edits_are_summarised():
    old = diagram.build(ARCH)
    new = old.replace("orders-dlq", "orders-dead-letters").replace('id="apigw_lbl"', 'id="apigw_lbl2"')
    new = new.replace("</root>", '<mxCell id="cache" value="ElastiCache" vertex="1" parent="1"><mxGeometry x="1" y="1" width="9" height="9" as="geometry"/></mxCell></root>')
    d = diagram.diff(old, new)
    assert any("orders-dead-letters" in r for r in d["relabelled"]) and "ElastiCache" in d["added"]
    assert "Relabelled" in diagram.diff_summary(d)


def test_uploads_with_entities_are_refused():
    with pytest.raises(ValueError):
        diagram.cells('<!DOCTYPE x [<!ENTITY e "x">]><mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>')


def test_design_api_before_archie_runs(client):
    c = TestClient(app)
    signup(c, "designer")
    pid = c.post("/api/projects", json={"name": "Design"}).json()["id"]
    d = c.get(f"/api/projects/{pid}/design").json()
    assert d["design"] is None and d["drawio"] is None
    assert c.post(f"/api/projects/{pid}/approvals/continue").status_code == 409
