"""E-mail addresses on accounts: setting them, logging in with them."""
from conftest import register, login


def test_register_with_email_and_login_with_it(client):
    resp = client.post("/api/auth/register", json={
        "name": "Alice", "password": "secret12", "email": " Alice@Example.COM "})
    assert resp.status_code == 201
    assert resp.get_json()["me"]["user"]["email"] == "alice@example.com"
    r = client.post("/api/auth/login", json={"login": "ALICE@example.com", "password": "secret12"})
    assert r.status_code == 200
    assert r.get_json()["me"]["user"]["name"] == "Alice"


def test_login_field_accepts_name_too(client):
    register(client, "Alice")
    r = client.post("/api/auth/login", json={"login": "alice", "password": "secret12"})
    assert r.status_code == 200


def test_email_is_optional_and_unique(client):
    register(client, "Alice")
    client.post("/api/auth/register", json={
        "name": "Bob", "password": "secret12", "email": "bob@example.com"})
    dup = client.post("/api/auth/register", json={
        "name": "Carol", "password": "secret12", "email": "BOB@example.com"})
    assert dup.status_code == 400


def test_bad_email_rejected(client):
    r = client.post("/api/auth/register", json={
        "name": "Alice", "password": "secret12", "email": "not-an-address"})
    assert r.status_code == 400


def test_name_cannot_contain_at(client):
    r = client.post("/api/auth/register", json={"name": "a@b.nl", "password": "secret12"})
    assert r.status_code == 400


def test_change_own_email_needs_password(client):
    s = register(client, "Alice")
    assert s.put("/api/me/email", json={"email": "a@example.com", "password": "nope"}).status_code == 403
    r = s.put("/api/me/email", json={"email": "a@example.com", "password": "secret12"})
    assert r.status_code == 200
    assert r.get_json()["user"]["email"] == "a@example.com"
    # clearing it works too
    r = s.put("/api/me/email", json={"email": "", "password": "secret12"})
    assert r.get_json()["user"]["email"] == ""


def test_change_email_to_taken_one_rejected(client):
    a = register(client, "Alice")
    b = register(client, "Bob")
    a.put("/api/me/email", json={"email": "a@example.com", "password": "secret12"})
    r = b.put("/api/me/email", json={"email": "a@example.com", "password": "secret12"})
    assert r.status_code == 400


def test_admin_sets_email(client):
    admin = register(client, "Alice")
    bob = register(client, "Bob")
    r = admin.put(f"/api/admin/users/{bob.user_id}/email", json={"email": "bob@example.com"})
    assert r.status_code == 200
    row = next(u for u in r.get_json() if u["id"] == bob.user_id)
    assert row["email"] == "bob@example.com"
    assert login(client, "bob@example.com")
    # non-admins can't
    assert bob.put(f"/api/admin/users/{admin.user_id}/email",
                   json={"email": "x@example.com"}).status_code == 403


def test_throttle_shared_between_name_and_email(client):
    import server
    register(client, "Alice", password="correcthorse")
    s = login(client, "Alice", password="correcthorse")
    s.put("/api/me/email", json={"email": "a@example.com", "password": "correcthorse"})
    for i in range(server.LOGIN_MAX_FAILS):
        ident = "Alice" if i % 2 else "a@example.com"
        client.post("/api/auth/login", json={"login": ident, "password": "nope"})
    r = client.post("/api/auth/login", json={"login": "a@example.com", "password": "correcthorse"})
    assert r.status_code == 429
