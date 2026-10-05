"""What Dev hands over after his deploy (the coverage report, the deploy report with sent → received evidence), the live
tools' queue reading, and the agent loop's "two turns left" warning. No AWS, no AI calls."""
import asyncio
from types import SimpleNamespace

from app.agents import base, de, livekit
from app.tools import pytest_runner


PUSHED = {
    "functions": {"orkestra-x-dev-transform": {"key": "transform", "source_dir": "src/transform", "kb": 12, "handler": "handler.lambda_handler",
                                               "layers": ["arn:aws:lambda:eu-west-1:1:layer:orkestra-x-dev-logger:3"], "deployed_at": "2026-10-02T12:00:00Z"}},
    "layers": {"orkestra-x-dev-logger": {"key": "abc", "folder": "logger", "arn": "arn:aws:lambda:eu-west-1:1:layer:orkestra-x-dev-logger:3",
                                         "version": 3, "kb": 4}},
    "changed": [], "unchanged": [],
}


def test_coverage_report_marks_tested_and_untested_lines():
    files = {"src/fn/handler.py": "def ok():\n    return 1\n\ndef never():\n    return '<b>'\n"}
    r = {"total": 2, "passed": 1, "failed": 1, "error": 0, "tests": [
        {"id": "tests::test_ok", "outcome": "passed", "message": ""},
        {"id": "tests::test_bad", "outcome": "failed", "message": "assert 1 == 2"}],
         "coverage": {"percent": 50.0, "files": {"src/fn/handler.py": {"percent": 50.0, "missing": [4, 5], "executed": [1, 2]}}}}
    html = pytest_runner.coverage_html("Coverage report · v1.2", files, r, 80)
    assert html.startswith("<!doctype html>") and "<script" not in html
    assert '<tr class="hit"><td class="ln">2</td>' in html and '<tr class="miss"><td class="ln">5</td>' in html
    assert "&#x27;&lt;b&gt;&#x27;" in html  # source is escaped, never rendered
    assert "gate 80% ✗ not met" in html and "1/2" in html and "assert 1 == 2" in html


def test_deploy_report_says_what_is_live_who_did_what_and_the_evidence():
    sanity = {"passed": True, "summary": "ok", "steps": [{"what": "API Gateway → Lambda", "how": "POST https://x/dev/orders", "observed": "200", "ok": True}],
              "try_it": [{"title": "1. Call the API", "link": "https://eu-west-1.console.aws.amazon.com/apigateway/main/apis/abc/resources",
                          "steps": "Resources → POST /orders → Test", "input": "Content-Type:application/xml", "expect": "200 accepted"}],
              "sent": "POST https://x/dev/orders <Order>…", "received": '{"order_id": "ORD1001-S2"} in orders.fifo',
              "left_for_user": "ORD1001-S3 in orders.fifo", "test_event": '{"httpMethod": "POST", "body": "<Order/>"}'}
    md = de.deploy_report("v1.2", "orkestra-x-dev-de", PUSHED, sanity)
    assert "live in AWS now" in md and "Dev built each layer zip" in md
    assert "orkestra-x-dev-logger v3" in md and "layers/logger/" in md
    assert "#/functions/orkestra-x-dev-transform?tab=code" in md and "#/layers/orkestra-x-dev-logger/versions/3" in md
    assert "**API Gateway → Lambda**: 200" in md and "  - how: POST https://x/dev/orders" in md
    assert "## Test it yourself in the AWS console" in md and "[Open in AWS](https://eu-west-1.console.aws.amazon.com/apigateway/" in md
    assert "**You should see:** 200 accepted" in md
    assert "### Sent" in md and "ORD1001-S2" in md and "Left for you to see:** ORD1001-S3" in md and '"httpMethod": "POST"' in md
    line = de.live_summary("v1.2", PUSHED)
    assert "Live in AWS now (v1.2)" in line and "layers orkestra-x-dev-logger v3" in line and "only its src/ folder" in line


def test_reading_a_queue_always_consumes():
    assert "keep" not in livekit.SQS_RECEIVE.schema["properties"]
    assert "no peek" in livekit.SQS_RECEIVE.description


def test_agents_are_warned_two_turns_before_the_limit(client, monkeypatch):
    seen: list[list] = []

    async def fake_call(*, messages, **_):
        seen.append([b for b in messages[-1]["content"] if isinstance(b, dict) and b.get("type") == "text"] if len(messages) > 1 else [])
        use = SimpleNamespace(type="tool_use", id=f"t{len(seen)}", name="look", input={})
        return SimpleNamespace(content=[use], stop_reason="tool_use"), {}

    monkeypatch.setattr(base.llm, "call", fake_call)
    tools = [base.Tool(name="look", description="look", schema=base.obj({}), handler=lambda a: asyncio.sleep(0, "seen")),
             base.Tool(name="submit", description="submit", schema=base.obj({}), terminal=True)]
    try:
        asyncio.run(base.run_loop(project_id="prj_nope", agent="qa", system="s", messages=[{"role": "user", "content": "go"}],
                                  tools=tools, max_turns=4, narrate=False))
    except base.AgentError as exc:
        assert "Stopped after 4 turns" in str(exc)
    warned = [i for i, texts in enumerate(seen) if any("Two turns left" in t["text"] and "submit" in t["text"] for t in texts)]
    assert warned == [2]  # the 3rd call (turn 3 of 4) carries the warning, once
