"""Live check of Echo's interview turns (real Opus calls, ~$0.06). Not collected by pytest.
Measures time-to-first-words (typewriter draft) and total time for two turns, plus cache reads.
Run on the host:  /opt/orkestra/venv/bin/python -m tests.live_chat_flow
"""
import secrets
import time

import httpx

MSG1 = """from 3rd party we get an API call to API Gateway, which triggers a Lambda. The Lambda converts the XML payload
to JSON as per the data mapping and sends it to SQS.
<Order><OrderId>ORD1001</OrderId><Customer><Name>Ravi Kumar</Name><Email>ravi@example.com</Email></Customer>
<Amount>1500</Amount><Currency>INR</Currency></Order>
mapping: Order/OrderId -> order_id, Order/Customer/Name -> customer_name, Order/Amount -> amount"""
MSG2 = "about 5 per hour, and yes order matters"


def turn(c, pid, msg):
    t0, first = time.time(), None
    c.post(f"/api/projects/{pid}/intake/chat", json={"message": msg}).raise_for_status()
    while time.time() - t0 < 180:
        d = c.get(f"/api/projects/{pid}/intake/draft").json()
        if d["draft"] and first is None:
            first = time.time() - t0
        if not d["busy"]:
            break
        time.sleep(0.4)
    s = c.get(f"/api/projects/{pid}/intake").json()
    echo = s["chat"][-1]
    print(f"--- first words after {first if first is None else round(first, 1)}s · done in {time.time() - t0:.1f}s")
    print("captured:", echo.get("filled"), "| quick replies:", echo.get("quick_replies"))
    print(echo["text"][:500])


with httpx.Client(base_url="http://localhost:80", timeout=30) as c:
    c.post("/api/auth/register", json={"username": f"chat{secrets.token_hex(3)}", "password": "live-test-pass-1",
                                       "display_name": "Chat Test"}).raise_for_status()
    pid = c.post("/api/projects", json={"name": "Live chat check", "budget_usd": 5}).json()["id"]
    turn(c, pid, MSG1)
    turn(c, pid, MSG2)
    u = c.get(f"/api/projects/{pid}/usage").json()
    for call in reversed(u["calls"]):
        print(f"call: in={call['input_tokens']} cache_read={call['cache_read_tokens']} cache_write={call['cache_write_tokens']} "
              f"out={call['output_tokens']} ${call['cost_usd']:.4f} {call['duration_ms'] / 1000:.1f}s")
    c.delete(f"/api/projects/{pid}", params={"confirm": "Live chat check"})
