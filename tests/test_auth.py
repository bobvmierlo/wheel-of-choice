"""Accounts: register, login, sessions."""
from conftest import Session, register, login


def test_register_returns_token_and_me(client):
    s = register(client, "Alice")
    assert s.token
    assert s.me["user"]["name"] == "Alice"
    # the very first account runs the place
    assert s.me["user"]["admin"] is True


def test_second_account_is_not_admin(client):
    register(client, "Alice")
    s2 = register(client, "Bob")
    assert s2.me["user"]["admin"] is False


def test_duplicate_name_rejected_case_insensitively(client):
    register(client, "Alice")
    resp = client.post("/api/auth/register", json={"name": "alice", "password": "secret12"})
    assert resp.status_code == 400


def test_short_name_rejected(client):
    resp = client.post("/api/auth/register", json={"name": "A", "password": "secret12"})
    assert resp.status_code == 400


def test_login_wrong_password_rejected(client):
    register(client, "Alice", password="correcthorse")
    resp = client.post("/api/auth/login", json={"name": "Alice", "password": "nope"})
    assert resp.status_code == 401


def test_login_is_case_insensitive_on_name(client):
    register(client, "Alice", password="correcthorse")
    s = login(client, "ALICE", password="correcthorse")
    assert s.me["user"]["name"] == "Alice"


def test_me_requires_auth(client):
    assert client.get("/api/me").status_code == 401


def test_logout_invalidates_token(client):
    s = register(client, "Alice")
    assert s.get("/api/me").status_code == 200
    assert s.post("/api/auth/logout").status_code == 204
    assert s.get("/api/me").status_code == 401


def test_expired_session_is_rejected(client):
    import server
    s = register(client, "Alice")
    # age the session past the TTL by rewriting its created stamp
    db = server.load_db()
    (tok,) = [t for t, sess in db["sessions"].items() if sess["user"] == s.user_id]
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=server.SESSION_TTL_DAYS + 1)).isoformat()
    db["sessions"][tok]["created"] = old
    server.save_db(db)
    assert s.get("/api/me").status_code == 401
