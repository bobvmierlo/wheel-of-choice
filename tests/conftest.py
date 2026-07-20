"""Shared pytest fixtures.

Every test runs against a throwaway data directory so the real db.json is
never touched. server.py reads its DB path from WHEEL_DATA_DIR at import
time, so we set that *before* importing the module, then wipe the db file
(and the in-memory caches) between tests for a clean slate each time.
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Point the server at a temp data dir before it's imported. A module-level
# temp dir keeps the path stable for the whole session; individual tests
# reset the db file inside it.
_TMP = tempfile.mkdtemp(prefix="wheel-tests-")
os.environ["WHEEL_DATA_DIR"] = _TMP
# Deterministic starting defaults, independent of the runner's env.
os.environ.pop("VAPID_SUBJECT", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


@pytest.fixture
def app():
    """A fresh, empty database for each test."""
    if server.DB_FILE.exists():
        server.DB_FILE.unlink()
    # Reset in-memory caches that outlive a single db.json.
    server._cal_cache.clear()
    with server._update_check_lock:
        server._update_check_cache.update({"at": 0.0, "data": None})
    yield server.app


@pytest.fixture
def client(app):
    return app.test_client()


class Session:
    """A logged-in test client: carries the bearer token and exposes the
    JSON verbs the way the real frontend calls them."""

    def __init__(self, client, token, me):
        self.client = client
        self.token = token
        self.me = me
        self.user_id = me["user"]["id"]

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def post(self, path, json=None):
        return self.client.post(path, json=json or {}, headers=self.headers)

    def put(self, path, json=None):
        return self.client.put(path, json=json or {}, headers=self.headers)

    def delete(self, path, json=None):
        return self.client.delete(path, json=json or {}, headers=self.headers)


def register(client, name, password="secret12", code=None):
    body = {"name": name, "password": password}
    if code:
        body["code"] = code
    resp = client.post("/api/auth/register", json=body)
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    return Session(client, data["token"], data["me"])


def login(client, name, password="secret12"):
    resp = client.post("/api/auth/login", json={"name": name, "password": password})
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    return Session(client, data["token"], data["me"])


def make_travel_wheel(session, wheel_type="holidays", home="northwest",
                      roam="europe", budget="mix", vibes=None, favorites=None):
    resp = session.post("/api/wheels", json={
        "type": wheel_type, "home": home, "roam": roam, "budget": budget,
        "vibes": vibes or [], "favorites": favorites or [],
    })
    assert resp.status_code == 201, resp.get_json()
    me = resp.get_json()
    return me["created"], me


def make_restaurant_wheel(session, name="Dinner"):
    resp = session.post("/api/wheels", json={"type": "restaurants", "name": name})
    assert resp.status_code == 201, resp.get_json()
    me = resp.get_json()
    return me["created"], me


def add_entry(session, wheel_id, name="Test place", travel=False):
    payload = {"name": name}
    resp = session.post(f"/api/wheels/{wheel_id}/destinations", json=payload)
    assert resp.status_code in (200, 201), resp.get_json()
    return resp.get_json()


def write_db(raw):
    """Drop a raw db dict straight onto disk — for migration tests."""
    server.DATA_DIR.mkdir(parents=True, exist_ok=True)
    server.DB_FILE.write_text(json.dumps(raw), encoding="utf-8")
