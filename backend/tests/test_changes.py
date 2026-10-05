from fastapi.testclient import TestClient

from app.main import app
from app.orchestrator.changes import unified_diff
from app.services.storage import ProjectStore
from tests.conftest import signup


def test_change_request_needs_signed_off_requirement(client):
    c = TestClient(app)
    signup(c, "crowner")
    pid = c.post("/api/projects", json={"name": "CR test"}).json()["id"]
    r = c.post(f"/api/projects/{pid}/changes", data={"text": "add a logging rule"})
    assert r.status_code == 409 and "Echo" in r.json()["detail"]
    assert c.get(f"/api/projects/{pid}/changes").json() == []


def test_agent_detail_shapes(client):
    c = TestClient(app)
    signup(c, "agentview")
    pid = c.post("/api/projects", json={"name": "Agent view"}).json()["id"]
    d = c.get(f"/api/projects/{pid}/agents/cto").json()
    assert d["agent"] == "cto" and d["state"]["status"] == "waiting"
    assert {"approvals", "changes", "files", "events", "conversation"} <= set(d)
    assert c.get(f"/api/projects/{pid}/agents/nope").status_code == 404


def test_amendment_versions_are_minor_bumps(client):
    store = ProjectStore("prj_versioncheck")
    store.create("version check")
    assert store.next_version(minor=True) == "v1.1" and store.next_version() == "v2"
    assert store.new_version("CR-001", minor=True) == "v1.1"
    assert store.next_version(minor=True) == "v1.2" and store.versions() == ["v1", "v1.1"]
    store.delete()


def test_unified_diff_marks_only_changed_lines():
    d = unified_diff("a\nb\nc\n", "a\nB\nc\n", "v1", "v1.1")
    assert "-b" in d and "+B" in d and "(v1.1)" in d
