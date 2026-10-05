import os
import tempfile

# Isolated data dir per test session — must be set before the app is imported.
os.environ["ORKESTRA_DATA_DIR"] = tempfile.mkdtemp(prefix="orkestra-test-")
os.environ["ORKESTRA_JOB_WORKERS"] = "2"
os.environ["ORKESTRA_DRIFT_CHECK_MINUTES"] = "0"  # no background drift watch in tests
os.environ["ORKESTRA_CREW_WATCH_SECONDS"] = "0"  # nor the crew watch (tests call watch.sweep directly)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """Each test starts with an empty login/sign-up window (the limiter is in-memory and shared by the session)."""
    from app.core.security import login_limiter

    login_limiter.hits.clear()
    yield


def signup(client: TestClient, username: str) -> TestClient:
    r = client.post("/api/auth/register", json={"username": username, "password": "correct-horse-9", "display_name": username})
    assert r.status_code == 201, r.text
    return client
