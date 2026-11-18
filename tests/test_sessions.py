"""Sessions: hashed at rest, listable, revocable; and the rate limits."""
import hashlib

from conftest import register, login
from wheelapp import accounts, storage


def test_tokens_are_stored_hashed(client):
    s = register(client, "Alice")
    db = storage.load_db()
    assert s.token not in db["sessions"]
    assert hashlib.sha256(s.token.encode()).hexdigest() in db["sessions"]


def test_legacy_raw_token_sessions_keep_working(client):
    s = register(client, "Alice")
    db = storage.load_db()
    key = accounts.token_key(s.token)
    db["sessions"] = {s.token: db["sessions"].pop(key)}  # as stored before hashing
    storage.save_db(db)
    assert s.get("/api/me").status_code == 200
    assert list(storage.load_db()["sessions"]) == [key]


def test_list_and_end_sessions(client):
    phone = register(client, "Alice")
    laptop = login(client, "Alice")
    sessions = laptop.get("/api/me/sessions").get_json()
    assert len(sessions) == 2
    assert sessions[0]["current"] is True
    other = next(x for x in sessions if not x["current"])
    r = laptop.delete(f"/api/me/sessions/{other['id']}")
    assert r.status_code == 200 and len(r.get_json()) == 1
    assert phone.get("/api/me").status_code == 401
    assert laptop.get("/api/me").status_code == 200


def test_end_other_sessions(client):
    a1 = register(client, "Alice")
    a2 = login(client, "Alice")
    a3 = login(client, "Alice")
    bob = register(client, "Bob")
    assert a3.post("/api/me/sessions/end-others").status_code == 200
    assert a1.get("/api/me").status_code == 401
    assert a2.get("/api/me").status_code == 401
    assert a3.get("/api/me").status_code == 200
    assert bob.get("/api/me").status_code == 200  # other people untouched


def test_password_change_logs_out_other_devices(client):
    a1 = register(client, "Alice")
    a2 = login(client, "Alice")
    r = a2.put("/api/me/password", json={"current": "secret12", "new": "brandnew1"})
    assert r.status_code == 200
    assert a1.get("/api/me").status_code == 401
    assert a2.get("/api/me").status_code == 200


def test_cannot_end_someone_elses_session(client):
    alice = register(client, "Alice")
    bob = register(client, "Bob")
    bob_id = bob.get("/api/me/sessions").get_json()[0]["id"]
    assert alice.delete(f"/api/me/sessions/{bob_id}").status_code == 404
    assert bob.get("/api/me").status_code == 200


def test_device_label():
    ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    assert accounts.device_label(ua) == "Safari · iPhone"
    assert accounts.device_label("Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36") \
        == "Chrome · Windows"
    assert accounts.device_label("") == "Unknown device"


def test_registration_is_rate_limited(client):
    register(client, "Admin")
    limit, _ = accounts.RATE_LIMITS["register"]
    for i in range(limit - 1):
        register(client, f"user{i}")
    r = client.post("/api/auth/register", json={"name": "onemore", "password": "secret12"})
    assert r.status_code == 429


def test_setup_restore_is_rate_limited(client):
    limit, _ = accounts.RATE_LIMITS["setup-restore"]
    for _ in range(limit):
        assert client.post("/api/setup/restore", json={"x": 1}).status_code == 400
    assert client.post("/api/setup/restore", json={"x": 1}).status_code == 429
