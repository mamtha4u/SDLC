import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.storage import ProjectStore, StorageError
from tests.conftest import signup


def test_register_login_logout_me(client):
    signup(client, "alice")
    assert client.get("/api/auth/me").json()["username"] == "alice"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-9"}).status_code == 200


def test_duplicate_username_rejected(client):
    assert client.post("/api/auth/register", json={"username": "alice", "password": "x" * 10,
                                                   "display_name": "A"}).status_code == 409


def test_projects_are_private_per_user(client):
    # Extra clients share the app started by the session fixture (no second lifespan/runner), own cookie jars.
    a, b = TestClient(app), TestClient(app)
    signup(a, "owner1")
    signup(b, "intruder1")
    pid = a.post("/api/projects", json={"name": "Orders flow"}).json()["id"]
    assert b.get(f"/api/projects/{pid}").status_code == 404
    assert all(p["id"] != pid for p in b.get("/api/projects").json())


def test_project_crud_and_storage(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-9"})
    r = client.post("/api/projects", json={"name": "Payments", "description": "demo", "budget_usd": 15})
    assert r.status_code == 201
    p = r.json()
    assert len(p["agents"]) == 7 and p["versions"] == ["v1"]
    assert client.patch(f"/api/projects/{p['id']}", json={"name": "Payments v2"}).json()["name"] == "Payments v2"
    assert client.get("/api/projects", params={"q": "payments"}).json()[0]["id"] == p["id"]
    assert client.delete(f"/api/projects/{p['id']}", params={"confirm": "wrong"}).status_code == 422
    assert client.delete(f"/api/projects/{p['id']}", params={"confirm": "Payments v2"}).status_code == 204
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_released_versions_are_read_only(tmp_path, monkeypatch):
    store = ProjectStore("prj_teststore")
    store.create("t")
    store.write("00_requirement.md", "# v1")
    v2 = store.new_version("change request", minor=False)
    assert v2 == "v2" and store.read("00_requirement.md") == b"# v1"
    with pytest.raises(StorageError):
        store.write("00_requirement.md", "tamper", version="v1")
    with pytest.raises(StorageError):
        store.write("../escape.txt", "x")
    assert store.new_version("bug fix", minor=True) == "v2.1"


def test_simulation_runs_and_kill_switch(client):
    client.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-9"})
    pid = client.post("/api/projects", json={"name": "Sim"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/simulate", params={"speed": 4}).status_code == 202
    time.sleep(2)
    assert client.post(f"/api/projects/{pid}/pause").json()["paused"] is True
    time.sleep(1.5)
    frozen = client.get(f"/api/projects/{pid}").json()["progress"]
    time.sleep(2)
    assert client.get(f"/api/projects/{pid}").json()["progress"] == frozen  # nothing moves while paused
    client.post(f"/api/projects/{pid}/resume")
    deadline = time.time() + 40
    while time.time() < deadline and client.get(f"/api/projects/{pid}").json()["status"] != "completed":
        time.sleep(1)
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["status"] == "completed"
    assert all(a["status"] == "done" for a in detail["agents"])
    assert any(e["type"] == "project.paused" for e in client.get(f"/api/projects/{pid}/events").json())
