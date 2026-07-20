"""Self-serve password change and admin password reset."""
from conftest import register, login


def test_change_password_happy_path(client):
    s = register(client, "Alice", password="correcthorse")
    resp = s.put("/api/me/password", json={"current": "correcthorse", "new": "batterystaple"})
    assert resp.status_code == 200
    # old password no longer works, new one does
    assert client.post("/api/auth/login",
                       json={"name": "Alice", "password": "correcthorse"}).status_code == 401
    assert login(client, "Alice", password="batterystaple")


def test_change_password_needs_correct_current(client):
    s = register(client, "Alice", password="correcthorse")
    resp = s.put("/api/me/password", json={"current": "wrongwrong", "new": "batterystaple"})
    assert resp.status_code == 403
    # unchanged: original still works
    assert login(client, "Alice", password="correcthorse")


def test_change_password_enforces_floor(client):
    s = register(client, "Alice", password="correcthorse")
    resp = s.put("/api/me/password", json={"current": "correcthorse", "new": "short7!"})
    assert resp.status_code == 400


def test_change_password_keeps_current_session(client):
    s = register(client, "Alice", password="correcthorse")
    s.put("/api/me/password", json={"current": "correcthorse", "new": "batterystaple"})
    # the token that made the change is still valid
    assert s.get("/api/me").status_code == 200


def test_change_password_requires_auth(client):
    assert client.put("/api/me/password",
                      json={"current": "x", "new": "batterystaple"}).status_code == 401


def test_admin_reset_lets_user_back_in(client):
    admin = register(client, "Alice")
    bob = register(client, "Bob", password="bobspassword")
    resp = admin.put(f"/api/admin/users/{bob.user_id}/password", json={"new": "temppass123"})
    assert resp.status_code == 200
    # Bob's old sessions are gone...
    assert bob.get("/api/me").status_code == 401
    # ...old password is dead, the temp one works
    assert client.post("/api/auth/login",
                       json={"name": "Bob", "password": "bobspassword"}).status_code == 401
    assert login(client, "Bob", password="temppass123")


def test_admin_reset_requires_admin(client):
    register(client, "Alice")
    bob = register(client, "Bob")
    carol = register(client, "Carol")
    # a non-admin can't reset anyone
    assert bob.put(f"/api/admin/users/{carol.user_id}/password",
                   json={"new": "temppass123"}).status_code == 403


def test_admin_reset_enforces_floor(client):
    admin = register(client, "Alice")
    bob = register(client, "Bob")
    assert admin.put(f"/api/admin/users/{bob.user_id}/password",
                     json={"new": "short7!"}).status_code == 400


def test_admin_reset_unknown_user_404s(client):
    admin = register(client, "Alice")
    assert admin.put("/api/admin/users/u-nope/password",
                     json={"new": "temppass123"}).status_code == 404
