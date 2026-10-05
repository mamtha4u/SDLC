import asyncio

import pytest

from app.agents.ba import render
from app.services import mapping_check
from app.tools import research, sandbox

ROW = {"target_moc": "M", "target_type": "string", "source_moc": "M", "source_type": "string", "pii": False, "comments": ""}
MAPPING = {
    "title": "t", "summary": "s", "direction": "Direct",
    "source": {"system": "a", "message_name": "Order", "format": "XML", "description": "d"},
    "target": {"system": "b", "message_name": "order", "format": "JSON", "description": "d"},
    "rows": [
        {**ROW, "target": "order_id", "target_sample": "ORD1", "logic": "1:1, trimmed", "rule_kind": "copy",
         "source_path": "/Order/OrderId", "source_sample": "ORD1"},
        {**ROW, "target": "customer.email", "target_sample": "a@b.c", "logic": "1:1", "rule_kind": "copy",
         "source_path": "/Order/Customer/Email", "source_sample": "a@b.c", "pii": True},
        {**ROW, "target": "channel", "target_sample": "API", "logic": "constant 'API'", "rule_kind": "constant",
         "source_path": "N/A", "source_sample": "", "source_moc": "N/A"},
        {**ROW, "target": "amount", "target_sample": "15.00", "logic": "format to 2 decimals", "rule_kind": "derived",
         "source_path": "/Order/Amount", "source_sample": "15", "target_moc": "O", "source_moc": "O"},
    ],
    "rules": [{"rule": "r", "condition": "c", "action": "400"}],
    "samples": [
        {"name": "ok", "description": "", "expect": "output",
         "input": "<Order><OrderId> ORD1 </OrderId><Customer><Email>a@b.c</Email></Customer><Amount>15</Amount></Order>",
         "expected": '{"order_id": "ORD1", "customer": {"email": "a@b.c"}, "channel": "API", "amount": "15.00"}'},
        {"name": "missing", "description": "", "input": "<Order><Customer><Email>a@b.c</Email></Customer></Order>",
         "expect": "reject", "expected": "OrderId is missing"},
        {"name": "xxe", "description": "", "expect": "reject", "expected": "Malformed XML",
         "input": '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><Order><OrderId>&e;</OrderId></Order>'},
        {"name": "wrong_root", "description": "", "input": "<Invoice><OrderId>1</OrderId></Invoice>",
         "expect": "reject", "expected": "Malformed XML"},
        {"name": "negative_amount", "description": "", "expect": "reject", "expected": "Amount must be positive",
         "input": "<Order><OrderId>ORD2</OrderId><Customer><Email>a@b.c</Email></Customer><Amount>-1</Amount></Order>"},
    ],
    "assumptions": [], "queries": [], "changes": "",
}


def test_examples_agree_with_mapping_table_without_running_code():
    proof = {p["name"]: p for p in mapping_check.check(MAPPING)}
    assert all(p["pass"] for p in proof.values())
    assert proof["ok"]["checked"] == 3  # 2 copies + 1 constant; the derived field is left to Dev's tests
    assert proof["missing"]["verified"] and "/Order/OrderId" in proof["missing"]["actual"]
    assert proof["xxe"]["verified"] and "refused" in proof["xxe"]["actual"]
    assert proof["wrong_root"]["verified"] and "<Invoice>" in proof["wrong_root"]["actual"]
    assert not proof["negative_amount"]["verified"]  # value rule: marked for Dev/Quinn, not guessed
    md = render({**MAPPING, "proof": list(proof.values())}, "v1")
    assert "5/5 examples agree" in md and "/Order/OrderId" in md and "transform(" not in md


def test_check_reports_a_wrong_example_precisely():
    bad = {**MAPPING, "samples": [{**MAPPING["samples"][0], "expected": '{"order_id": "ORD9", "customer": {"email": "a@b.c"}, "channel": "WEB"}'}]}
    p = mapping_check.check(bad)[0]
    assert not p["pass"] and "'ORD9'" in p["actual"] and "'ORD1'" in p["actual"] and "constant 'API'" in p["actual"]
    unexplained = {**MAPPING, "samples": [{**MAPPING["samples"][1], "expect": "output", "expected": '{"order_id": ""}'}]}
    assert not mapping_check.check(unexplained)[0]["pass"]


def test_json_paths():
    doc = {"order": {"items": [{"sku": "A1"}]}, "id": 7}
    assert mapping_check.get(doc, "order.items[0].sku") == "A1"
    assert mapping_check.get(doc, "$.id") == 7
    assert mapping_check.get(doc, "order.missing") is mapping_check.MISSING


@pytest.mark.skipif(not sandbox.available(), reason="Docker sandbox not installed")
def test_sandbox_has_no_network():
    # The sandbox is kept for Dev (DE) to run code and tests in. Atlas no longer uses it.
    net = asyncio.run(sandbox.run_transform(
        "import urllib.request\ndef transform(p):\n    return urllib.request.urlopen('https://pypi.org', timeout=3).read()[:5]",
        [{"name": "net", "input": ""}]))
    assert net["results"][0]["ok"] is False


def test_fetch_url_blocks_internal_addresses():
    for url in ["http://169.254.169.254/latest/meta-data/", "http://localhost:80/", "http://10.0.0.1/", "file:///etc/passwd"]:
        with pytest.raises(ValueError):
            asyncio.run(research.fetch_url(url))
