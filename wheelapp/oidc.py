"""oidc — part of the Wheel of Choice server (see server.py)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from flask import abort, jsonify, redirect, request

from .core import (
    _lock,
    app,
    settings_view,
)
from . import accounts, storage


# ── Single sign-on (OpenID Connect, e.g. Authentik) ──────────────────
# Optional: once an admin fills in an identity provider (Admin → Single
# sign-on), the login screen grows a "Log in with …" button. It's the
# standard authorization-code flow with PKCE, for a *confidential*
# client: the browser is sent to the provider, comes back to
# /api/auth/oidc/callback with a one-time code, and the server swaps that
# code for tokens directly with the provider over TLS. Because the ID
# token arrives straight from the provider's token endpoint (never via
# the browser), OIDC Core §3.1.3.7 lets us rely on TLS instead of
# verifying its signature — so this needs no JWT/crypto dependency. We
# still check issuer, audience, expiry and nonce.
#
# Matching to local accounts, in order:
#   1. an account already linked to this identity (by the provider's
#      stable `sub` claim — not the e-mail, which can change);
#   2. if "match by e-mail" is on: the account whose e-mail address equals
#      the provider's — linked for good on that first login. By default
#      the provider must vouch that the address is verified;
#   3. otherwise a new account is created — but only where a password
#      sign-up would be allowed too (open registration, an invite, or the
#      admin's "SSO users may create accounts" switch).
# A logged-in user can also link their account by hand (Account → Link),
# which covers accounts whose e-mail differs from the provider's.
OIDC_STATE_TTL = 10 * 60   # seconds a started login may take at the provider
OIDC_HANDOFF_TTL = 2 * 60  # seconds the browser has to collect its session
OIDC_DISCOVERY_TTL = 3600  # seconds the provider's metadata is cached
OIDC_TIMEOUT = 10
OIDC_MAX_BYTES = 1_000_000
OIDC_MAX_PENDING = 500     # cap on in-flight logins, so they can't pile up
OIDC_CALLBACK_PATH = "/api/auth/oidc/callback"
OIDC_DEFAULT_LABEL = "Log in with SSO"
# Binds a started login to the browser that started it. Without it,
# someone could start a flow and talk somebody else into finishing it —
# logging the victim into the attacker's account, or (worse) linking the
# victim's identity to the attacker's account. SameSite=Lax still rides
# along on the provider's top-level redirect back to us.
OIDC_COOKIE = "wheel_oidc"

_oidc_lock = threading.Lock()
_oidc_pending = {}    # state -> {verifier, nonce, redirect_uri, mode, user_id, code, at}
_oidc_handoffs = {}   # one-time code -> {token, joined, at}
_oidc_discovery = {}  # issuer -> {"at": monotonic, "data": metadata}


def oidc_settings(db):
    """The single-sign-on configuration in force: what an admin stored,
    falling back to the WHEEL_OIDC_* / WHEEL_PUBLIC_URL env vars (handy for
    a docker-compose file). The client secret is in here — never send this
    dict to a browser; see oidc_settings_view."""
    stored = (db.get("settings") or {}).get("oidc") or {}

    def pick(key, env):
        return str(stored.get(key) or os.environ.get(env) or "").strip()

    cfg = {
        "issuer": pick("issuer", "WHEEL_OIDC_ISSUER"),
        "client_id": pick("client_id", "WHEEL_OIDC_CLIENT_ID"),
        "client_secret": pick("client_secret", "WHEEL_OIDC_CLIENT_SECRET"),
        "public_url": pick("public_url", "WHEEL_PUBLIC_URL").rstrip("/"),
        "button_label": str(stored.get("button_label") or "").strip() or OIDC_DEFAULT_LABEL,
        "match_email": bool(stored.get("match_email", True)),
        "require_verified_email": bool(stored.get("require_verified_email", True)),
        "allow_signup": bool(stored.get("allow_signup", False)),
        "disable_password_login": bool(stored.get("disable_password_login", False)),
    }
    cfg["enabled"] = bool(cfg["issuer"] and cfg["client_id"] and cfg["client_secret"])
    return cfg


def password_login_forced():
    """Break-glass switch: WHEEL_FORCE_PASSWORD_LOGIN=1 turns password
    log-in back on regardless of the admin setting — for when the identity
    provider is down or misconfigured and nobody can get in anymore."""
    return os.environ.get("WHEEL_FORCE_PASSWORD_LOGIN", "").strip().lower() in ("1", "true", "yes", "on")


def password_login_allowed(db):
    """Whether names/e-mails + passwords work on this server right now.
    An admin can switch it off once single sign-on is set up; it comes back
    on by itself if SSO stops being configured (fail open — never lock the
    whole server out), and the env var above forces it on."""
    if password_login_forced():
        return True
    cfg = oidc_settings(db)
    return not (cfg["enabled"] and cfg["disable_password_login"])


def require_password_login(db):
    if not password_login_allowed(db):
        abort(403, description="passwords are switched off on this server — "
                               "use the sign-on button instead")


def oidc_redirect_uri(cfg):
    """Where the provider sends the browser back to. Behind a reverse
    proxy the request's own URL may say http:// or an internal host, so an
    admin can pin the public address (Admin → Single sign-on)."""
    base = cfg["public_url"] or request.url_root.rstrip("/")
    return base + OIDC_CALLBACK_PATH


def oidc_settings_view(db):
    """The SSO settings for the admin panel — the secret only as a flag."""
    cfg = oidc_settings(db)
    view = {k: v for k, v in cfg.items() if k != "client_secret"}
    view["client_secret_set"] = bool(cfg["client_secret"])
    view["redirect_uri"] = oidc_redirect_uri(cfg)
    view["linked_users"] = sum(1 for u in db["users"].values() if u.get("oidc_sub"))
    view["unlinked_users"] = sorted(u["name"] for u in db["users"].values() if not u.get("oidc_sub"))
    view["password_login"] = password_login_allowed(db)
    view["password_login_forced"] = password_login_forced()
    return view


def _oidc_http(url, data=None, headers=None):
    """GET (or form-POST, when data is given) a JSON document from the
    identity provider. Tests replace this. Raises ValueError with a
    human-readable reason on any failure."""
    body = urlencode(data).encode("ascii") if data is not None else None
    req = urllib.request.Request(url, data=body, headers={
        "Accept": "application/json",
        "User-Agent": "wheel-of-choice",
        **({"Content-Type": "application/x-www-form-urlencoded"} if body else {}),
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=OIDC_TIMEOUT) as resp:
            raw = resp.read(OIDC_MAX_BYTES + 1)
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = json.loads(err.read(4096)).get("error_description") or ""
        except Exception:
            pass
        raise ValueError(f"the identity provider answered {err.code}"
                         + (f": {detail}" if detail else "")) from None
    except (urllib.error.URLError, OSError) as err:
        raise ValueError(f"can't reach the identity provider ({getattr(err, 'reason', err)})") from None
    if len(raw) > OIDC_MAX_BYTES:
        raise ValueError("the identity provider sent an oversized response")
    try:
        doc = json.loads(raw)
    except ValueError:
        raise ValueError("the identity provider didn't answer with JSON") from None
    if not isinstance(doc, dict):
        raise ValueError("the identity provider sent something unexpected")
    return doc


def oidc_discover(issuer, fresh=False):
    """The provider's metadata (/.well-known/openid-configuration), cached.
    For Authentik the issuer looks like
    https://auth.example.com/application/o/<app-slug>/"""
    now = time.monotonic()
    with _oidc_lock:
        hit = _oidc_discovery.get(issuer)
        if hit and not fresh and now - hit["at"] < OIDC_DISCOVERY_TTL:
            return hit["data"]
    meta = _oidc_http(issuer.rstrip("/") + "/.well-known/openid-configuration")
    for key in ("issuer", "authorization_endpoint", "token_endpoint"):
        if not isinstance(meta.get(key), str) or not meta[key]:
            raise ValueError(f"the provider's metadata has no {key}")
    if meta["issuer"].rstrip("/") != issuer.rstrip("/"):
        raise ValueError(f"the provider calls itself {meta['issuer']} — "
                         "put exactly that in the issuer field")
    with _oidc_lock:
        _oidc_discovery[issuer] = {"at": now, "data": meta}
    return meta


def _b64url_json(segment):
    pad = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + pad))


def oidc_exchange(cfg, pending, code):
    """Swap the authorization code for tokens and return the identity's
    claims (ID token, topped up from the userinfo endpoint)."""
    meta = oidc_discover(cfg["issuer"])
    basic = base64.b64encode(
        f"{quote(cfg['client_id'], safe='')}:{quote(cfg['client_secret'], safe='')}".encode()
    ).decode()
    tokens = _oidc_http(meta["token_endpoint"], data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending["redirect_uri"],
        "code_verifier": pending["verifier"],
    }, headers={"Authorization": f"Basic {basic}"})
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        raise ValueError("the identity provider sent no ID token — is the 'openid' scope allowed?")
    try:
        claims = _b64url_json(id_token.split(".")[1])
    except (ValueError, TypeError):
        raise ValueError("the identity provider sent an unreadable ID token") from None
    if not isinstance(claims, dict):
        raise ValueError("the identity provider sent an unreadable ID token")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if str(claims.get("iss", "")).rstrip("/") != meta["issuer"].rstrip("/"):
        raise ValueError("the ID token was issued by someone else")
    if cfg["client_id"] not in auds:
        raise ValueError("the ID token is meant for another application")
    try:
        expired = float(claims.get("exp", 0)) < time.time() - 60
    except (TypeError, ValueError):
        expired = True
    if expired:
        raise ValueError("the ID token has expired — is the server's clock right?")
    if not hmac.compare_digest(str(claims.get("nonce", "")), pending["nonce"]):
        raise ValueError("the ID token doesn't belong to this login attempt")
    if not claims.get("sub"):
        raise ValueError("the ID token names no user")
    # Authentik can leave profile/e-mail claims out of the ID token; the
    # userinfo endpoint always has them.
    if meta.get("userinfo_endpoint") and tokens.get("access_token"):
        try:
            info = _oidc_http(meta["userinfo_endpoint"],
                              headers={"Authorization": f"Bearer {tokens['access_token']}"})
        except ValueError:
            info = {}
        if str(info.get("sub", "")) == str(claims["sub"]):
            claims = {**claims, **info}
    claims["sub"] = str(claims["sub"])
    return claims


def user_by_oidc_sub(db, sub):
    return next((u for u in db["users"].values() if u.get("oidc_sub") == sub), None)


def email_is_verified(claims):
    v = claims.get("email_verified")
    return v is True or (isinstance(v, str) and v.lower() == "true")


def sso_display_name(db, claims):
    """A free, valid display name for an account created through SSO:
    the provider's username, else its full name, else the e-mail's local
    part — de-duplicated with a number."""
    raw = ""
    for key in ("preferred_username", "nickname", "name", "given_name"):
        if isinstance(claims.get(key), str) and claims[key].strip():
            raw = claims[key]
            break
    if not raw and isinstance(claims.get("email"), str):
        raw = claims["email"].split("@")[0]
    base = " ".join(raw.replace("@", " ").split())[:26] or "user"
    if len(base) < 2:
        base = (base + "-user")[:26]
    name, n = base, 2
    while accounts.user_by_name(db, name):
        name, n = f"{base} {n}", n + 1
    return name


def _prune_oidc(now):
    for store, ttl in ((_oidc_pending, OIDC_STATE_TTL), (_oidc_handoffs, OIDC_HANDOFF_TTL)):
        for key in [k for k, v in store.items() if now - v["at"] > ttl]:
            store.pop(key, None)


def _sso_bounce(**params):
    """Back to the app, with the outcome in the query string."""
    resp = redirect("/?" + urlencode(params), code=302)
    resp.delete_cookie(OIDC_COOKIE, path="/api/auth/oidc")
    return resp


@app.post("/api/auth/oidc/start")
def oidc_start():
    """Begin an SSO login (or, with a valid Bearer token and mode=link,
    linking the logged-in account). Returns the provider URL for the
    browser to go to; a JSON call rather than a plain link so that linking
    can carry the session token."""
    accounts.rate_limit("sso-start")
    payload = request.get_json(force=True, silent=True) or {}
    mode = "link" if payload.get("mode") == "link" else "login"
    code = str(payload.get("code", "")).strip().upper().replace(" ", "")[:20]
    with _lock:
        db = storage.load_db()
        cfg = oidc_settings(db)
        if not cfg["enabled"]:
            abort(404, description="single sign-on isn't set up on this server")
        user_id = accounts.current_user(db)["id"] if mode == "link" else None
    try:
        meta = oidc_discover(cfg["issuer"])
    except ValueError as err:
        abort(502, description=str(err))
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    redirect_uri = oidc_redirect_uri(cfg)
    browser = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _oidc_lock:
        _prune_oidc(now)
        if len(_oidc_pending) >= OIDC_MAX_PENDING:
            abort(429, description="too many logins in progress — try again in a few minutes")
        _oidc_pending[state] = {
            "verifier": verifier, "nonce": nonce, "redirect_uri": redirect_uri,
            "mode": mode, "user_id": user_id, "code": code, "at": now,
            "browser": hashlib.sha256(browser.encode()).hexdigest(),
        }
    sep = "&" if "?" in meta["authorization_endpoint"] else "?"
    url = meta["authorization_endpoint"] + sep + urlencode({
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    resp = jsonify({"url": url})
    resp.set_cookie(OIDC_COOKIE, browser, max_age=OIDC_STATE_TTL, path="/api/auth/oidc",
                    httponly=True, samesite="Lax",
                    secure=redirect_uri.startswith("https://"))
    return resp


@app.get(OIDC_CALLBACK_PATH)
def oidc_callback():
    """The provider sends the browser back here. Works out which local
    account this identity is (see the section comment above), starts a
    session for it and bounces back to the app with a short-lived
    one-time code; the app trades that for its token via /finish, so the
    session token itself never appears in a URL."""
    state = request.args.get("state", "")
    with _oidc_lock:
        _prune_oidc(time.monotonic())
        pending = _oidc_pending.pop(state, None)
    if pending is None:
        return _sso_bounce(sso_error="that sign-in link expired or was already used — try again")
    browser = request.cookies.get(OIDC_COOKIE, "")
    if not hmac.compare_digest(hashlib.sha256(browser.encode()).hexdigest(), pending["browser"]):
        return _sso_bounce(sso_error="this sign-in was started in another browser — start it again here")
    if request.args.get("error"):
        reason = request.args.get("error_description") or request.args.get("error")
        return _sso_bounce(sso_error=f"the identity provider said no: {reason[:200]}")
    with _lock:
        cfg = oidc_settings(storage.load_db())
    if not cfg["enabled"]:
        return _sso_bounce(sso_error="single sign-on isn't set up on this server")
    try:
        claims = oidc_exchange(cfg, pending, request.args.get("code", ""))
    except ValueError as err:
        return _sso_bounce(sso_error=str(err))
    sub = claims["sub"]
    email = str(claims.get("email") or "").strip().lower()
    if email and (len(email) > accounts.MAX_EMAIL_LEN or not accounts.EMAIL_RE.match(email)):
        email = ""

    with _lock:
        db = storage.load_db()
        owner = user_by_oidc_sub(db, sub)
        if pending["mode"] == "link":
            user = db["users"].get(pending["user_id"])
            if user is None:
                return _sso_bounce(sso_error="your account is gone — log in again")
            if owner is not None and owner["id"] != user["id"]:
                return _sso_bounce(sso_error=f"that sign-on identity is already linked to {owner['name']}")
            user["oidc_sub"] = sub
            if not user.get("email") and email and not accounts.user_by_email(db, email):
                user["email"] = email
            storage.save_db(db)
            return _sso_bounce(sso="linked")

        user, joined = owner, None
        if user is None and cfg["match_email"] and email:
            match = accounts.user_by_email(db, email)
            if match is not None:
                if match.get("oidc_sub"):
                    return _sso_bounce(sso_error=(
                        f"the account with {email} is linked to a different sign-on identity — "
                        "ask an admin to unlink it"))
                if cfg["require_verified_email"] and not email_is_verified(claims):
                    return _sso_bounce(sso_error=(
                        f"an account with {email} exists, but the identity provider doesn't "
                        "vouch that the address is verified — log in with your password and "
                        "link sign-on from your account settings instead"))
                user = match
                user["oidc_sub"] = sub
        if user is None:
            code = pending["code"]
            wheel = next((w for w in db["wheels"].values() if code and w["code"] == code), None)
            invite = None if wheel or not code else db.get("invites", {}).get(code)
            allowed = (not db["users"] or settings_view(db)["registration_open"]
                       or cfg["allow_signup"] or wheel is not None or invite is not None)
            if not allowed:
                who = email or claims.get("preferred_username") or "this identity"
                return _sso_bounce(sso_error=(
                    f"there's no account here for {who} — ask an admin to add your e-mail "
                    "address to your account, or for an invite"))
            free_email = email if email and not accounts.user_by_email(db, email) else ""
            user = accounts.new_user(db, sso_display_name(db, claims), email=free_email, wheel=wheel)
            user["oidc_sub"] = sub
            if invite:
                db.setdefault("invites", {}).pop(code, None)
            if wheel:
                joined = wheel["id"]
        accounts.clear_login_fails(user["id"])
        token = accounts.start_session(db, user)
        storage.save_db(db)
    handoff = secrets.token_urlsafe(24)
    with _oidc_lock:
        _oidc_handoffs[handoff] = {"token": token, "joined": joined, "at": time.monotonic()}
    return _sso_bounce(sso=handoff)


@app.post("/api/auth/oidc/finish")
def oidc_finish():
    """Trade the one-time code from the callback bounce for the session."""
    payload = request.get_json(force=True, silent=True) or {}
    code = str(payload.get("code", ""))
    with _oidc_lock:
        _prune_oidc(time.monotonic())
        handoff = _oidc_handoffs.pop(code, None)
    if handoff is None:
        abort(400, description="that sign-in expired — try again")
    with _lock:
        db = storage.load_db()
        session = db["sessions"].get(accounts.token_key(handoff["token"]))
        user = db["users"].get(session["user"]) if session else None
        if user is None:
            abort(400, description="that sign-in expired — try again")
        me = accounts.me_payload(db, user)
    if handoff["joined"]:
        me["joined"] = handoff["joined"]
    return jsonify({"token": handoff["token"], "me": me})


@app.post("/api/me/sso/unlink")
def unlink_own_sso():
    """Detach your account from its sign-on identity. Only with a password
    to fall back on — otherwise you'd lock yourself out."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        if not password_login_allowed(db):
            abort(400, description="passwords are switched off on this server — "
                                   "unlinking would lock you out")
        if not user.get("password"):
            abort(400, description="set a password first — otherwise you couldn't log in anymore")
        user.pop("oidc_sub", None)
        storage.save_db(db)
        return jsonify(accounts.me_payload(db, user))
