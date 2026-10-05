from fastapi.testclient import TestClient

from app.agents.intake import _chat_messages, chat_window
from app.db.models import Intake
from app.main import app
from tests.conftest import signup


def _chat(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "echo", "text": f"m{i}", "ts": ""} for i in range(n)]


def test_long_interview_window_is_anchored_so_the_prompt_cache_keeps_hitting():
    starts = [chat_window(Intake(chat=_chat(n), status="collecting"))[0]["text"] for n in range(1, 60)]
    assert set(starts) == {"m0"}  # same prefix for many turns (a sliding window changed it every turn)
    assert [chat_window(Intake(chat=_chat(n), status="collecting"))[0]["text"] for n in (60, 79, 80)] == ["m20", "m20", "m40"]
    assert len(chat_window(Intake(chat=_chat(60), status="collecting"))) == 40


def test_amendment_sends_only_its_own_conversation_and_labels_orion():
    chat = _chat(30) + [
        {"role": "orion", "cr": "cr_1", "label": "CR-001", "text": "use logger.py", "attachments": ["logger.py"], "ts": ""},
        {"role": "echo", "text": "Read it. Who fixes mask_pii?", "ts": ""},
        {"role": "note", "to": "cto", "text": "logger (1).py replaces logger.py", "ts": ""},
        {"role": "orion", "kind": "reply", "text": "Noted, Echo.", "ts": ""},
        {"role": "user", "text": "crew can fix it", "ts": ""},
    ]
    it = Intake(chat=chat, status="amending", active_cr="cr_1")
    assert chat_window(it)[0]["role"] == "orion"
    turns = _chat_messages(it, "(no new files)")
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[0]["content"].startswith("[Orion (CTO) → Echo] I'm forwarding CR-001") and "logger.py" in turns[0]["content"]
    assert "[I told Orion: logger (1).py replaces logger.py]" in turns[1]["content"][0]["text"]
    last = turns[2]["content"][0]["text"]
    assert last.startswith("[Orion (CTO) → Echo] Noted, Echo.") and last.endswith("crew can fix it")


def test_tool_calls_become_readable_crew_lines():
    from app.orchestrator.crewchat import describe

    assert describe("web_search", {"query": "lxml 3.14"}, [{"title": "lxml · PyPI"}]) == "🔎 Searched the web: “lxml 3.14” → 1 result(s). Top: lxml · PyPI"
    assert "→ lxml 6 publishes" in describe("pypi_package", {"package": "lxml", "python": "3.14"}, {"verdict": "lxml 6 publishes 36 wheels"})
    assert describe("capture", {"answers": []}) is None  # Echo's bookkeeping stays out of the room


def test_project_assistant_api(client, monkeypatch):
    from app.orchestrator import runner as runner_mod

    queued = []

    async def fake_enqueue(kind, project_id=None, **payload):  # no real model call from the test suite
        queued.append(kind)
        return "job_test"

    monkeypatch.setattr(runner_mod.runner, "enqueue", fake_enqueue)
    c = TestClient(app)
    signup(c, "asker")
    pid = c.post("/api/projects", json={"name": "Ask"}).json()["id"]
    msgs = c.post(f"/api/projects/{pid}/assistant", json={"question": "Are we using SQS?"}).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["status"] == "streaming" and queued == ["guide.answer"]
    assert c.post(f"/api/projects/{pid}/assistant", json={"question": "again?"}).status_code == 409  # one answer at a time
    assert c.delete(f"/api/projects/{pid}/assistant").status_code == 204
    assert c.get(f"/api/projects/{pid}/assistant").json() == []


def test_crew_room_api(client):
    c = TestClient(app)
    signup(c, "crewroom")
    pid = c.post("/api/projects", json={"name": "Crew room"}).json()["id"]
    assert c.get(f"/api/projects/{pid}/crew").json() == []
    assert c.get(f"/api/projects/{pid}/crew?after=5").status_code == 200
