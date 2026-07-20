"""Admin-controlled registration gate and account invites."""
import server
from conftest import register, make_restaurant_wheel


def close_registration(admin):
    resp = admin.put("/api/admin/settings", json={"registration_open": False})
    assert resp.status_code == 200
    assert resp.get_json()["registration_open"] is False


# ── The setting itself ───────────────────────────────────────────────
def test_registration_open_by_default(client):
    admin = register(client, "Alice")
    assert admin.get("/api/admin/settings").get_json()["registration_open"] is True


def test_public_config_reports_openness(client):
    # before any account exists, config reports open (bootstrap)
    assert client.get("/api/config").get_json()["registration_open"] is True
    admin = register(client, "Alice")
    assert client.get("/api/config").get_json()["registration_open"] is True
    close_registration(admin)
    assert client.get("/api/config").get_json()["registration_open"] is False


def test_only_admin_can_toggle_registration(client):
    register(client, "Alice")
    bob = register(client, "Bob")
    assert bob.put("/api/admin/settings", json={"registration_open": False}).status_code == 403


# ── The gate ─────────────────────────────────────────────────────────
def test_open_registration_lets_anyone_in(client):
    admin = register(client, "Alice")
    # default open — a stranger can still register
    assert register(client, "Bob")


def test_closed_registration_blocks_open_signup(client):
    admin = register(client, "Alice")
    close_registration(admin)
    resp = client.post("/api/auth/register", json={"name": "Bob", "password": "secret12"})
    assert resp.status_code == 403


def test_first_account_always_allowed_even_if_closed(client):
    # a fresh server with the flag pre-set to closed must still bootstrap
    server.save_db({"version": 3, "users": {}, "sessions": {}, "wheels": {},
                    "settings": {"registration_open": False}})
    resp = client.post("/api/auth/register", json={"name": "Alice", "password": "secret12"})
    assert resp.status_code == 201
    assert resp.get_json()["me"]["user"]["admin"] is True


def test_wheel_invite_code_still_works_when_closed(client):
    admin = register(client, "Alice")
    wid, me = make_restaurant_wheel(admin)
    code = next(w["code"] for w in me["wheels"] if w["id"] == wid)
    close_registration(admin)
    # registering with a valid wheel code joins that wheel despite the gate
    resp = client.post("/api/auth/register",
                       json={"name": "Bob", "password": "secret12", "code": code})
    assert resp.status_code == 201
    assert wid in [w["id"] for w in resp.get_json()["me"]["wheels"]]


# ── Admin account invites ────────────────────────────────────────────
def test_admin_creates_invite_that_opens_the_gate(client):
    admin = register(client, "Alice")
    close_registration(admin)
    made = admin.post("/api/admin/invites")
    assert made.status_code == 201
    code = made.get_json()["code"]

    # a newcomer with the invite gets in — and joins no wheel
    resp = client.post("/api/auth/register",
                       json={"name": "Bob", "password": "secret12", "code": code})
    assert resp.status_code == 201
    assert resp.get_json()["me"]["wheels"] == []


def test_invite_is_single_use(client):
    admin = register(client, "Alice")
    close_registration(admin)
    code = admin.post("/api/admin/invites").get_json()["code"]
    assert client.post("/api/auth/register",
                       json={"name": "Bob", "password": "secret12", "code": code}).status_code == 201
    # the same code can't mint a second account — it's now a dead code (400)
    again = client.post("/api/auth/register",
                        json={"name": "Carol", "password": "secret12", "code": code})
    assert again.status_code == 400


def test_admin_can_list_and_revoke_invites(client):
    admin = register(client, "Alice")
    code = admin.post("/api/admin/invites").get_json()["code"]
    listed = admin.get("/api/admin/invites").get_json()
    assert any(i["code"] == code for i in listed)
    remaining = admin.delete(f"/api/admin/invites/{code}").get_json()
    assert all(i["code"] != code for i in remaining)
    # a revoked invite is now a dead code (400), whether or not the gate is closed
    close_registration(admin)
    assert client.post("/api/auth/register",
                       json={"name": "Bob", "password": "secret12", "code": code}).status_code == 400


def test_invite_endpoints_are_admin_only(client):
    register(client, "Alice")
    bob = register(client, "Bob")
    assert bob.get("/api/admin/invites").status_code == 403
    assert bob.post("/api/admin/invites").status_code == 403


def test_invite_codes_never_collide_with_wheel_codes(client):
    admin = register(client, "Alice")
    wid, me = make_restaurant_wheel(admin)
    wheel_code = next(w["code"] for w in me["wheels"] if w["id"] == wid)
    # mint a batch of invites; none may equal an existing wheel code
    for _ in range(20):
        code = admin.post("/api/admin/invites").get_json()["code"]
        assert code != wheel_code
