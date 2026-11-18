"""Single sign-on (OpenID Connect) against a fake identity provider."""
import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest

import server
from conftest import register, login

ISSUER = "https://auth.example.com/application/o/wheel/"


def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


class FakeIdP:
    """Stands in for Authentik: answers discovery, token and userinfo."""

    def __init__(self):
        self.identity = {}
        self.nonce = None
        self.token_calls = []

    def __call__(self, url, data=None, headers=None):
        if url.endswith("/.well-known/openid-configuration"):
            return {
                "issuer": ISSUER,
                "authorization_endpoint": "https://auth.example.com/application/o/authorize/",
                "token_endpoint": "https://auth.example.com/application/o/token/",
                "userinfo_endpoint": "https://auth.example.com/application/o/userinfo/",
            }
        if url.endswith("/token/"):
            self.token_calls.append(data)
            claims = {"iss": ISSUER, "aud": "wheel-client", "exp": time.time() + 300,
                      "nonce": self.nonce, "sub": self.identity["sub"]}
            return {"id_token": _jwt(claims), "access_token": "at"}
        if url.endswith("/userinfo/"):
            return dict(self.identity)
        raise AssertionError(url)


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdP()
    monkeypatch.setattr(server, "_oidc_http", fake)
    server._oidc_discovery.clear()
    server._oidc_pending.clear()
    server._oidc_handoffs.clear()
    return fake


def configure(admin, **extra):
    r = admin.put("/api/admin/sso", json={
        "issuer": ISSUER, "client_id": "wheel-client", "client_secret": "s3cret", **extra})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def sso(client, idp, identity, token=None, mode="login", code=None):
    """Run the whole browser round-trip; returns the bounce query params."""
    idp.identity = identity
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"mode": mode, **({"code": code} if code else {})}
    r = client.post("/api/auth/oidc/start", json=body, headers=headers)
    assert r.status_code == 200, r.get_json()
    q = parse_qs(urlparse(r.get_json()["url"]).query)
    assert q["code_challenge_method"] == ["S256"]
    idp.nonce = q["nonce"][0]
    cb = client.get(f"/api/auth/oidc/callback?code=abc&state={q['state'][0]}")
    assert cb.status_code == 302
    return {k: v[0] for k, v in parse_qs(urlparse(cb.headers["Location"]).query).items()}


def finish(client, handoff):
    r = client.post("/api/auth/oidc/finish", json={"code": handoff})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_config_reports_sso(client, idp):
    admin = register(client, "Alice")
    assert client.get("/api/config").get_json()["sso"]["enabled"] is False
    view = configure(admin, button_label="Log in with Authentik")
    assert "client_secret" not in view and view["client_secret_set"] is True
    assert view["redirect_uri"].endswith("/api/auth/oidc/callback")
    cfg = client.get("/api/config").get_json()["sso"]
    assert cfg == {"enabled": True, "label": "Log in with Authentik", "password_login": True}


def test_existing_user_matched_by_verified_email(client, idp):
    admin = register(client, "Alice")
    configure(admin)
    bob = register(client, "Bob")
    admin.put(f"/api/admin/users/{bob.user_id}/email", json={"email": "bob@example.com"})
    out = sso(client, idp, {"sub": "ak-1", "email": "Bob@Example.com", "email_verified": True})
    data = finish(client, out["sso"])
    assert data["me"]["user"]["id"] == bob.user_id
    assert data["me"]["user"]["sso_linked"] is True
    # a later login finds them by `sub`, even if the e-mail moved on
    out = sso(client, idp, {"sub": "ak-1", "email": "new@example.com", "email_verified": True})
    assert finish(client, out["sso"])["me"]["user"]["id"] == bob.user_id
    # the handoff code is single-use
    assert client.post("/api/auth/oidc/finish", json={"code": out["sso"]}).status_code == 400


def test_unverified_email_does_not_match(client, idp):
    admin = register(client, "Alice")
    configure(admin)
    bob = register(client, "Bob")
    admin.put(f"/api/admin/users/{bob.user_id}/email", json={"email": "bob@example.com"})
    out = sso(client, idp, {"sub": "ak-1", "email": "bob@example.com", "email_verified": False})
    assert "verified" in out["sso_error"]
    # …unless the admin says the provider's addresses can be trusted
    configure(admin, require_verified_email=False)
    out = sso(client, idp, {"sub": "ak-1", "email": "bob@example.com", "email_verified": False})
    assert finish(client, out["sso"])["me"]["user"]["id"] == bob.user_id


def test_unknown_identity_refused_when_registration_closed(client, idp):
    admin = register(client, "Alice")
    configure(admin)
    admin.put("/api/admin/settings", json={"registration_open": False})
    out = sso(client, idp, {"sub": "ak-9", "email": "zed@example.com", "email_verified": True})
    assert "no account" in out["sso_error"]
    # the admin can let SSO users create accounts
    configure(admin, allow_signup=True)
    out = sso(client, idp, {"sub": "ak-9", "email": "zed@example.com", "email_verified": True,
                            "preferred_username": "zed"})
    me = finish(client, out["sso"])["me"]
    assert me["user"]["name"] == "zed"
    assert me["user"]["email"] == "zed@example.com"
    assert me["user"]["has_password"] is False


def test_sso_signup_dedupes_name_and_uses_wheel_invite(client, idp):
    from conftest import make_restaurant_wheel
    admin = register(client, "Alice")
    configure(admin)
    admin.put("/api/admin/settings", json={"registration_open": False})
    wheel_id, me = make_restaurant_wheel(admin)
    code = next(w for w in me["wheels"] if w["id"] == wheel_id)["code"]
    out = sso(client, idp, {"sub": "ak-2", "preferred_username": "alice"}, code=code)
    data = finish(client, out["sso"])
    assert data["me"]["user"]["name"] == "alice 2"
    assert data["me"]["joined"] == wheel_id


def test_password_login_fails_for_sso_only_account(client, idp):
    admin = register(client, "Alice")
    configure(admin, allow_signup=True)
    sso(client, idp, {"sub": "ak-3", "preferred_username": "zed"})
    r = client.post("/api/auth/login", json={"login": "zed", "password": ""})
    assert r.status_code == 401


def test_sso_only_account_can_set_first_password(client, idp):
    admin = register(client, "Alice")
    configure(admin, allow_signup=True)
    out = sso(client, idp, {"sub": "ak-3", "preferred_username": "zed"})
    tok = finish(client, out["sso"])["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/me/sso/unlink", headers=h).status_code == 400  # would lock out
    assert client.put("/api/me/password", json={"new": "brandnew1"}, headers=h).status_code == 200
    assert login(client, "zed", password="brandnew1")
    assert client.post("/api/me/sso/unlink", headers=h).status_code == 200


def test_link_from_account_settings(client, idp):
    admin = register(client, "Alice")
    configure(admin)
    bob = register(client, "Bob")  # no e-mail on file
    out = sso(client, idp, {"sub": "ak-5", "email": "bob@corp.example", "email_verified": True},
              token=bob.token, mode="link")
    assert out == {"sso": "linked"}
    me = bob.get("/api/me").get_json()
    assert me["user"]["sso_linked"] is True
    assert me["user"]["email"] == "bob@corp.example"  # filled in since it was empty
    # that identity can't be linked to someone else as well
    out = sso(client, idp, {"sub": "ak-5"}, token=admin.token, mode="link")
    assert "already linked" in out["sso_error"]


def test_already_linked_email_account_not_hijacked(client, idp):
    admin = register(client, "Alice")
    configure(admin)
    bob = register(client, "Bob")
    admin.put(f"/api/admin/users/{bob.user_id}/email", json={"email": "bob@example.com"})
    sso(client, idp, {"sub": "ak-1", "email": "bob@example.com", "email_verified": True})
    out = sso(client, idp, {"sub": "ak-OTHER", "email": "bob@example.com", "email_verified": True})
    assert "different sign-on identity" in out["sso_error"]
    # the admin unlinks; the next login re-matches
    admin.post(f"/api/admin/users/{bob.user_id}/unlink-sso")
    out = sso(client, idp, {"sub": "ak-OTHER", "email": "bob@example.com", "email_verified": True})
    assert finish(client, out["sso"])["me"]["user"]["id"] == bob.user_id


def test_callback_needs_the_starting_browser(client, app, idp):
    admin = register(client, "Alice")
    configure(admin)
    idp.identity = {"sub": "ak-1"}
    r = client.post("/api/auth/oidc/start", json={})
    q = parse_qs(urlparse(r.get_json()["url"]).query)
    idp.nonce = q["nonce"][0]
    other = app.test_client()  # a different browser: no binding cookie
    cb = other.get(f"/api/auth/oidc/callback?code=abc&state={q['state'][0]}")
    assert "another browser" in parse_qs(urlparse(cb.headers["Location"]).query)["sso_error"][0]


def test_bad_nonce_rejected(client, idp):
    admin = register(client, "Alice")
    configure(admin)
    idp.identity = {"sub": "ak-1"}
    r = client.post("/api/auth/oidc/start", json={})
    q = parse_qs(urlparse(r.get_json()["url"]).query)
    idp.nonce = "wrong"
    cb = client.get(f"/api/auth/oidc/callback?code=abc&state={q['state'][0]}")
    assert "login attempt" in parse_qs(urlparse(cb.headers["Location"]).query)["sso_error"][0]


def test_unknown_state_rejected(client, idp):
    cb = client.get("/api/auth/oidc/callback?code=abc&state=nope")
    assert "expired" in parse_qs(urlparse(cb.headers["Location"]).query)["sso_error"][0]


def test_start_without_config_is_404(client, idp):
    register(client, "Alice")
    assert client.post("/api/auth/oidc/start", json={}).status_code == 404


def test_secret_kept_when_omitted(client, idp):
    admin = register(client, "Alice")
    configure(admin)
    view = admin.put("/api/admin/sso", json={"button_label": "Authentik"}).get_json()
    assert view["client_secret_set"] is True and view["enabled"] is True
    view = admin.put("/api/admin/sso", json={"clear_secret": True}).get_json()
    assert view["enabled"] is False


def test_admin_test_endpoint(client, idp):
    admin = register(client, "Alice")
    configure(admin)
    r = admin.post("/api/admin/sso/test")
    assert r.status_code == 200 and r.get_json()["issuer"] == ISSUER


# ── Switching password log-in off ────────────────────────────────────
def _linked_admin(client, idp):
    """Admin account linked to SSO, plus a password-only second user."""
    admin = register(client, "Alice")
    configure(admin)
    admin.put("/api/me/email", json={"email": "alice@example.com", "password": "secret12"})
    out = sso(client, idp, {"sub": "ak-alice", "email": "alice@example.com", "email_verified": True})
    finish(client, out["sso"])
    return admin


def test_disable_needs_sso_and_linked_admin(client, idp):
    admin = register(client, "Alice")
    # no SSO configured yet
    r = admin.put("/api/admin/sso", json={"disable_password_login": True})
    assert "single sign-on first" in r.get_json()["warning"]
    assert r.get_json()["disable_password_login"] is False
    # admin not linked → would lock themselves out; the other fields still save
    r = admin.put("/api/admin/sso", json={
        "issuer": ISSUER, "client_id": "wheel-client", "client_secret": "s3cret",
        "disable_password_login": True})
    view = r.get_json()
    assert "link your own account" in view["warning"]
    assert view["enabled"] is True and view["disable_password_login"] is False


def test_password_login_off_blocks_password_routes(client, idp):
    admin = _linked_admin(client, idp)
    bob = register(client, "Bob")
    view = admin.put("/api/admin/sso", json={"disable_password_login": True}).get_json()
    assert view["password_login"] is False and view["unlinked_users"] == ["Bob"]
    cfg = client.get("/api/config").get_json()
    assert cfg["password_login"] is False and cfg["sso"]["password_login"] is False
    assert client.post("/api/auth/login",
                       json={"login": "Bob", "password": "secret12"}).status_code == 403
    assert client.post("/api/auth/register",
                       json={"name": "Carol", "password": "secret12"}).status_code == 403
    assert bob.put("/api/me/password",
                   json={"current": "secret12", "new": "another12"}).status_code == 403
    assert admin.put(f"/api/admin/users/{bob.user_id}/password",
                     json={"new": "another12"}).status_code == 403
    assert admin.post("/api/me/sso/unlink").status_code == 400
    # existing sessions keep working
    assert bob.get("/api/me").status_code == 200
    # SSO still logs people in
    out = sso(client, idp, {"sub": "ak-alice"})
    assert finish(client, out["sso"])["me"]["user"]["name"] == "Alice"


def test_password_login_fails_open_without_sso(client, idp):
    admin = _linked_admin(client, idp)
    admin.put("/api/admin/sso", json={"disable_password_login": True})
    assert client.post("/api/auth/login",
                       json={"login": "Alice", "password": "secret12"}).status_code == 403
    admin.put("/api/admin/sso", json={"clear_secret": True})  # SSO no longer configured
    assert login(client, "Alice")


def test_force_env_var_restores_password_login(client, idp, monkeypatch):
    admin = _linked_admin(client, idp)
    admin.put("/api/admin/sso", json={"disable_password_login": True})
    monkeypatch.setenv("WHEEL_FORCE_PASSWORD_LOGIN", "1")
    assert login(client, "Alice")
    view = admin.get("/api/admin/sso").get_json()
    assert view["password_login_forced"] is True and view["password_login"] is True


def test_sso_signup_still_works_with_passwords_off(client, idp):
    admin = _linked_admin(client, idp)
    admin.put("/api/admin/sso", json={"disable_password_login": True, "allow_signup": True})
    out = sso(client, idp, {"sub": "ak-new", "preferred_username": "newbie"})
    assert finish(client, out["sso"])["me"]["user"]["name"] == "newbie"
