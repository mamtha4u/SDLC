"""Fixes from the user's 2026-10-02 review: names owned by the user, change requests that skip unaffected agents,
the coverage gate kept in sync with the documents, a stable diagram layout, a diagram that stays inside its boxes."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.agents.ta import stable_order, sync_coverage
from app.main import app
from app.orchestrator.flow import plan_route
from app.services import diagram, naming
from app.services.storage import ProjectStore
from tests.conftest import signup

NAMES_TF = '''variable "name_prefix" {
  type    = string
  default = "orkestra-ac-cd-dl-551-dev"
}
variable "names" {
  type = map(string)
  default = {
    transform      = "transform"
    transform_role = "transform-role"
    orders_queue   = "orders.fifo"
  }
}
'''


def _project(c: TestClient, name: str) -> str:
    return c.post("/api/projects", json={"name": name}).json()["id"]


def test_names_are_the_users_and_follow_into_the_documents(client):
    c = TestClient(app)
    signup(c, "namer")
    pid = _project(c, "Names")
    s = ProjectStore(pid)
    s.write("infra/names.tf", NAMES_TF)
    s.write("03_lld.md", "Lambda `orkestra-ac-cd-dl-551-dev-transform` with role orkestra-ac-cd-dl-551-dev-transform-role. "
                         "Queue orkestra-ac-cd-dl-551-dev-orders.fifo.")
    st = c.get(f"/api/projects/{pid}/naming").json()
    assert st["editable"] and st["prefix"] == "orkestra-ac-cd-dl-551-dev"
    assert {i["key"]: i["full"] for i in st["items"]}["orders_queue"] == "orkestra-ac-cd-dl-551-dev-orders.fifo"
    r = c.put(f"/api/projects/{pid}/naming", json={"prefix": "orkestra-ac-cd-dl-551-dev", "names": {"transform": "xform"}})
    assert r.status_code == 200, r.text
    assert r.json()["renamed"] == [["orkestra-ac-cd-dl-551-dev-transform", "orkestra-ac-cd-dl-551-dev-xform"]]
    lld = s.read("03_lld.md").decode()
    # only the whole name changed: the role that starts with the same text kept its name
    assert "`orkestra-ac-cd-dl-551-dev-xform`" in lld and "orkestra-ac-cd-dl-551-dev-transform-role" in lld
    saved = json.loads(s.read(naming.USER_NAMES))
    assert saved["names"]["transform"] == "xform" and saved["name_prefix"] == "orkestra-ac-cd-dl-551-dev"
    # the access plan scopes by the user's prefix
    from app.services import aws_access
    assert aws_access.name_prefix({naming.USER_NAMES: json.dumps({"name_prefix": "orkestra-orders-x"}), "infra/names.tf": NAMES_TF}) == "orkestra-orders-x"


@pytest.mark.parametrize("names,prefix,msg", [
    ({"orders_queue": "orders"}, "orkestra-ac-cd-dl-551-dev", ".fifo"),
    ({"transform": "bad name!"}, "orkestra-ac-cd-dl-551-dev", "letters"),
    ({"transform": "orders.fifo"}, "orkestra-ac-cd-dl-551-dev", "same name"),
    ({}, "colleague-app", "orkestra-"),
])
def test_names_are_checked(client, names, prefix, msg):
    c = TestClient(app)
    signup(c, f"namecheck{abs(hash(msg)) % 9999}")
    pid = _project(c, "Names check")
    ProjectStore(pid).write("infra/names.tf", NAMES_TF)
    r = c.put(f"/api/projects/{pid}/naming", json={"prefix": prefix, "names": names})
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_names_before_terra_are_read_only(client):
    c = TestClient(app)
    signup(c, "namesearly")
    pid = _project(c, "Early")
    st = c.get(f"/api/projects/{pid}/naming").json()
    assert not st["editable"] and st["reason"]


def test_a_change_request_skips_agents_it_does_not_touch(client):
    s = ProjectStore("prj_skipcheck01")
    s.create("x")
    s.write("mapping/01_data_mapping.json", "{}")
    s.write("diagrams/design.json", "{}")
    route, skipped = plan_route("prj_skipcheck01", {"rerun": ["ta", "tp"], "cr_id": "cr_1"})
    # *.talk: a finished kickoff (or an older project) goes straight to the work (agents/talk.py)
    assert route["next"] == "ta.talk" and skipped == ["ba"]
    route, skipped = plan_route("prj_skipcheck01", {"rerun": ["tp"], "cr_id": "cr_1"})
    assert route["next"] == "tp.talk" and skipped == ["ba", "ta"]
    route, skipped = plan_route("prj_skipcheck01", {"rerun": ["de"]})  # no infra yet: Terra can't be skipped
    assert route["next"] == "tp.talk" and skipped == ["ba", "ta"]
    route, _ = plan_route("prj_skipcheck01", {"rerun": ["ba", "ta", "tp", "de", "qa"]})
    assert route["next"] == "ba.talk"
    s.delete()


def test_coverage_number_follows_the_gate():
    lld = "## 11.3 Quality gates\nMinimum line coverage is 70%. The rules are in quality_gates.\nCoverage gate ≥ 70 % on src/."
    out = sync_coverage(lld, 80)
    assert "Minimum line coverage is 80%" in out and "≥ 80%" in out and "70" not in out
    assert sync_coverage("Retry 3 times, 70% of the time", 80) == "Retry 3 times, 70% of the time"  # not about coverage
    assert sync_coverage("- Coverage ≥ 70% of lines (`--cov-fail-under=70`).", 80) == "- Coverage ≥ 80% of lines (`--cov-fail-under=80`)."


def test_a_revision_keeps_the_diagram_order():
    old = {"path": [{"id": "api"}, {"id": "fn"}], "destinations": [{"id": "q"}], "support": [{"id": "role"}, {"id": "logs"}]}
    new = {"path": [{"id": "fn"}, {"id": "api"}], "destinations": [{"id": "dlq"}, {"id": "q"}], "support": [{"id": "logs"}, {"id": "role"}]}
    out = stable_order(old, new)
    assert [n["id"] for n in out["path"]] == ["api", "fn"]
    assert [n["id"] for n in out["destinations"]] == ["q", "dlq"]  # new nodes go last
    assert [n["id"] for n in out["support"]] == ["role", "logs"]


ARCH = {
    "title": "t", "subtitle": "s", "network": "No VPC: the Lambda isn't attached to one",
    "sources": [{"id": "sender", "label": "Order sender", "sub": "", "icon": "users", "details": []}],
    "path": [{"id": "api", "label": "API", "sub": "", "icon": "api_gateway", "details": []},
             {"id": "fn", "label": "Lambda", "sub": "", "icon": "lambda", "details": ["Parse: x", "Validate: y", "Map: z", "Send: w"]}],
    "destinations": [{"id": "q", "label": "orders.fifo", "sub": "", "icon": "sqs_queue", "details": []},
                     {"id": "dlq", "label": "orders-dlq.fifo", "sub": "", "icon": "sqs_queue", "details": []}],
    "support": [{"id": s, "label": s, "sub": "", "icon": "lambda_layer", "under": "fn"} for s in ("lxml", "logger", "role", "logs", "alarm")],
    "edges": [{"from": "sender", "to": "api", "label": "1. HTTPS POST /orders (XML)", "kind": "data"},
              {"from": "api", "to": "sender", "label": "8. 200 / 400 / 500", "kind": "error"},
              {"from": "api", "to": "fn", "label": "Lambda proxy", "kind": "data"},
              {"from": "fn", "to": "q", "label": "SendMessage", "kind": "data"},
              {"from": "q", "to": "dlq", "label": "redrive after 3", "kind": "error"},
              {"from": "lxml", "to": "fn", "label": "layer", "kind": "support"}],
}


def _geo(xml: str) -> dict[str, tuple[float, float, float, float]]:
    out = {}
    for cid, x, y, w, h in re.findall(r'<mxCell id="([^"]+)"[^>]*vertex="1"[^>]*><mxGeometry x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"', xml):
        out[cid] = (float(x), float(y), float(w), float(h))
    return out


def test_the_diagram_stays_inside_its_boxes_and_captions_sit_beside_arrows():
    xml = diagram.build(ARCH, "f")
    g = _geo(xml)
    rx, ry, rw, rh = g["region"]
    for cid in ("lxml", "logger", "role", "logs", "alarm", "api", "fn", "q", "dlq"):
        x, y, w, h = g[cid]
        assert rx <= x and x + w <= rx + rw and ry <= y and y + h <= ry + rh, f"{cid} spills out of the region"
    # edges carry no label of their own (no text drawn through the line); captions are separate cells
    assert not re.search(r'edge="1"[^>]*value="[^"]+"', xml) and not re.search(r'value="[^"]+"[^>]*edge="1"', xml)
    assert "e0_cap" in g and "e1_cap" in g
    assert g["e0_cap"][1] < g["e1_cap"][1]  # the request's caption above, the reply's below
    assert "No VPC" in xml


def test_the_state_bucket_is_not_part_of_the_diagram():
    bad = {**ARCH, "support": [*ARCH["support"], {"id": "st", "label": "orkestra-x-tfstate", "sub": "Terraform state", "icon": "s3_bucket", "under": "fn"}]}
    assert any("Terraform state" in p for p in diagram.validate(bad))
