"""Live end-to-end check of Echo against the running service (real Opus call, ~$0.10). Not collected by pytest.
Run on the host:  /opt/orkestra/venv/bin/python -m tests.live_intake_flow
"""
import secrets
import time

import httpx

BASE = "http://localhost:80"
ANSWERS = {
    "purpose.goal": "Partners send orders by REST; validate, convert to our internal schema and queue for fulfilment.",
    "purpose.actors": "Partners send; internal fulfilment service consumes from a queue",
    "trigger.type": "REST API call",
    "trigger.sample_input": '{"orderId":"A-1","amount":49.99,"currency":"GBP","ts":"2026-09-30T10:00:00Z","country":"gb"}',
    "data.source_fields": "orderId string req; amount decimal req; currency ISO-4217; ts ISO-8601; country 2-letter",
    "data.target": "SQS queue",
    "data.target_fields": "order_id, amount_minor int, currency, created_at UTC, country_code",
    "data.rules": "amount_minor = amount*100; country_code = upper(country) default GB",
    "operations.environment": "Sandbox",
    "operations.volume": "__suggest__",
}

with httpx.Client(base_url=BASE, timeout=30) as c:
    user = f"live{secrets.token_hex(3)}"
    c.post("/api/auth/register", json={"username": user, "password": "live-test-pass-1", "display_name": "Live Test"}).raise_for_status()
    pid = c.post("/api/projects", json={"name": "Live intake check", "budget_usd": 5}).json()["id"]
    c.put(f"/api/projects/{pid}/intake/answers", json={"answers": ANSWERS}).raise_for_status()
    t0 = time.time()
    c.post(f"/api/projects/{pid}/intake/review", json={"mode": "review"}).raise_for_status()
    while time.time() - t0 < 300:
        s = c.get(f"/api/projects/{pid}/intake").json()
        if not s["busy"]:
            break
        time.sleep(3)
    s = c.get(f"/api/projects/{pid}/intake").json()
    if not s["rounds"]:
        print("NO ROUND. project:", c.get(f"/api/projects/{pid}").json()["agents"][1])
        raise SystemExit(1)
    r = s["rounds"][-1]
    print(f"round {r['round']} in {time.time() - t0:.0f}s · completeness {r['completeness']}% · {len(r['gaps'])} gaps · "
          f"{len(r['suggestions'])} suggestions · services: {[x['service'] for x in r['services']]}")
    print("headline:", r["headline"])
    print("first gap:", r["gaps"][0]["question"] if r["gaps"] else "-")
    print("first suggestion:", r["suggestions"][0]["title"] if r["suggestions"] else "-")
    u = c.get(f"/api/projects/{pid}/usage").json()
    print(f"usage: {u['totals']['calls']} call(s), {u['totals']['total_tokens']} tokens, ${u['totals']['cost_usd']:.4f}",
          "| by agent:", [(a['agent'], a['cost_usd']) for a in u["by_agent"]])
    c.delete(f"/api/projects/{pid}", params={"confirm": "Live intake check"})
