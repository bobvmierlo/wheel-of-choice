"""Admin: bootstrap, promotion, deletion, and the guardrails around them."""
from conftest import register, make_restaurant_wheel


def test_first_account_is_admin_others_are_not(client):
    admin = register(client, "Alice")
    user = register(client, "Bob")
    assert admin.get("/api/admin/users").status_code == 200
    assert user.get("/api/admin/users").status_code == 403


def test_admin_can_promote_a_user(client):
    admin = register(client, "Alice")
    bob = register(client, "Bob")
    resp = admin.put(f"/api/admin/users/{bob.user_id}", json={"admin": True})
    assert resp.status_code == 200
    assert bob.get("/api/admin/users").status_code == 200


def test_admin_cannot_demote_self(client):
    admin = register(client, "Alice")
    resp = admin.put(f"/api/admin/users/{admin.user_id}", json={"admin": False})
    assert resp.status_code == 400


def test_admin_cannot_delete_self(client):
    admin = register(client, "Alice")
    resp = admin.delete(f"/api/admin/users/{admin.user_id}")
    assert resp.status_code == 400


def test_admin_can_delete_another_user(client):
    admin = register(client, "Alice")
    bob = register(client, "Bob")
    resp = admin.delete(f"/api/admin/users/{bob.user_id}")
    assert resp.status_code == 200
    # Bob's session is gone
    assert bob.get("/api/me").status_code == 401


def test_deleting_a_user_drops_their_solo_wheels(client):
    import server
    admin = register(client, "Alice")
    bob = register(client, "Bob")
    wid, _ = make_restaurant_wheel(bob)
    admin.delete(f"/api/admin/users/{bob.user_id}")
    db = server.load_db()
    assert wid not in db["wheels"]
