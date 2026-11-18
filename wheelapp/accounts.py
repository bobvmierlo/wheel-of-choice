"""accounts — part of the Wheel of Choice server (see server.py)."""
import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import abort, jsonify, request

from .core import (
    BUDGETS,
    DISTANCES,
    PARTIES,
    SEASONS,
    SESSION_TTL_DAYS,
    VIBES,
    _lock,
    app,
    settings_view,
)
from . import oidc, storage


# ── Accounts ─────────────────────────────────────────────────────────
MIN_PASSWORD_LEN = 8  # for new/changed passwords; existing logins are unaffected


def hash_password(password, salt_hex):
    return hashlib.scrypt(
        password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1
    ).hex()


def password_ok(user, password):
    """Whether `password` is this account's password. Accounts created
    through single sign-on may have no password at all — those never
    pass a password check (they log in through the identity provider,
    or an admin sets them a password)."""
    if not user or not user.get("password") or not user.get("salt"):
        return False
    return hmac.compare_digest(user["password"], hash_password(password, user["salt"]))


# ── E-mail addresses ─────────────────────────────────────────────────
# An account can carry one e-mail address. It's a second way to log in
# (next to the name) and — more importantly — the key used to match an
# existing local account to a single-sign-on identity the first time
# someone logs in through the identity provider. So it is unique across
# accounts, case-insensitively, and stored lower-cased. No mail is ever
# sent; the address isn't verified by this server.
MAX_EMAIL_LEN = 254
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(value):
    """A normalised e-mail address, '' for none. Aborts 400 on anything
    that doesn't look like an address."""
    email = str(value or "").strip().lower()
    if not email:
        return ""
    if len(email) > MAX_EMAIL_LEN or not EMAIL_RE.match(email):
        abort(400, description="that doesn't look like an e-mail address")
    return email


def user_by_email(db, email):
    if not email:
        return None
    email = email.strip().lower()
    return next((u for u in db["users"].values() if (u.get("email") or "") == email), None)


def user_by_name(db, name):
    name = name.strip().lower()
    return next((u for u in db["users"].values() if u["name"].lower() == name), None)


def user_by_login(db, ident):
    """The account a login form's identifier points at: an e-mail address
    when it contains an @ (names can't — see check_name), else a name."""
    ident = ident.strip()
    if not ident:
        return None
    if "@" in ident:
        # a name from before names were barred from containing an @ still works
        return user_by_email(db, ident) or user_by_name(db, ident)
    return user_by_name(db, ident)


def ensure_email_free(db, email, user_id=None):
    other = user_by_email(db, email)
    if other is not None and other["id"] != user_id:
        abort(400, description="that e-mail address is already used by another account")


def check_name(name):
    """Display names double as log-in names, so they can't contain an @
    (that would make them ambiguous with e-mail addresses)."""
    if len(name) < 2:
        abort(400, description="pick a name of at least 2 characters")
    if "@" in name:
        abort(400, description="a name can't contain an @ — put your e-mail address in the e-mail field")


# ── Login throttling ─────────────────────────────────────────────────
# Online password guessing is the realistic attack on a small,
# network-exposed app. scrypt already makes each guess costly; this caps
# how many wrong guesses one account can absorb in a window. It's keyed by
# the *targeted account name*, not the source IP: behind a reverse proxy
# every request shares one IP, so the account is the reliable signal — and
# it means one account under attack can't lock the others out. In-memory
# is fine here (single process; a restart just clears the counters).
LOGIN_MAX_FAILS = 10       # wrong guesses tolerated within the window
LOGIN_WINDOW = 15 * 60     # seconds the failures are counted over
_login_fails = {}          # user id, or the typed identifier for unknown accounts -> [timestamps]
_login_lock = threading.Lock()


def _throttle_key(name):
    return name.strip().lower()


def _recent_fails(key, now):
    return [t for t in _login_fails.get(key, []) if now - t < LOGIN_WINDOW]


def login_retry_after(name):
    """0 if a login attempt is allowed now, else the seconds to wait."""
    now = time.monotonic()
    key = _throttle_key(name)
    with _login_lock:
        fails = _recent_fails(key, now)
        _login_fails[key] = fails  # prune stale entries as we go
        if len(fails) >= LOGIN_MAX_FAILS:
            return int(LOGIN_WINDOW - (now - fails[0])) + 1
    return 0


def record_login_fail(name):
    now = time.monotonic()
    key = _throttle_key(name)
    with _login_lock:
        _login_fails[key] = _recent_fails(key, now) + [now]


def clear_login_fails(name):
    with _login_lock:
        _login_fails.pop(_throttle_key(name), None)


def default_wheel_prefs():
    return {"budget": [], "distance": [], "vibe": [], "season": [], "party": "couple"}


def clean_prefs(db, user, payload):
    """Per-wheel filter preferences, keyed by wheel id; unknown wheels
    and unknown values are dropped. Restaurant wheels have no filters."""
    if not isinstance(payload, dict):
        payload = {}
    existing = user.get("prefs") or {}
    prefs = {}
    for wid in user.get("wheels", []):
        wheel = db["wheels"].get(wid)
        if wheel is None or not storage.is_travel(wheel):
            continue
        base = existing.get(wid) or default_wheel_prefs()
        raw = payload.get(wid, base)
        if not isinstance(raw, dict):
            raw = base
        out = {}
        for key, allowed in (("budget", BUDGETS), ("distance", DISTANCES),
                             ("vibe", VIBES), ("season", SEASONS)):
            values = raw.get(key, base.get(key, []))
            out[key] = [v for v in values if v in allowed] if isinstance(values, list) else []
        party = raw.get("party", base.get("party", "couple"))
        out["party"] = party if party in PARTIES else "couple"
        prefs[wid] = out
    return prefs


def session_cutoff():
    return (datetime.now(timezone.utc) - timedelta(days=SESSION_TTL_DAYS)).isoformat()


# ── Sessions ─────────────────────────────────────────────────────────
# A login hands the browser a random bearer token. The server only ever
# stores its SHA-256 — so the database (and every backup of it) can't be
# used to log in as anyone. Each session also remembers roughly which
# device it is and when it was last used, so people can see their logins
# and end the ones they don't recognise.
SESSION_TOUCH_EVERY = 3600  # seconds between "last seen" updates (each one is a write)


def token_key(token):
    """What a session is stored under: the token's SHA-256, hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_token_key(key):
    return len(key) == 64 and all(c in "0123456789abcdef" for c in key)


def request_token():
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


def request_session_key():
    token = request_token()
    return token_key(token) if token else ""


def hash_legacy_sessions(db):
    """Sessions from before tokens were hashed are keyed by the raw token:
    re-key them by its hash. Nobody gets logged out. True if anything
    changed (so the caller saves)."""
    raw = [k for k in db.get("sessions", {}) if not is_token_key(k)]
    for token in raw:
        db["sessions"][token_key(token)] = db["sessions"].pop(token)
    return bool(raw)


def device_label(user_agent):
    """'Safari on iPhone'-style label for a session — just enough to tell
    your own devices apart, nothing more is kept."""
    ua = user_agent or ""
    if "iPhone" in ua:
        os_name = "iPhone"
    elif "iPad" in ua:
        os_name = "iPad"
    elif "Android" in ua:
        os_name = "Android"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        os_name = "Mac"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = ""
    for marker, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                         ("FxiOS", "Firefox"), ("CriOS", "Chrome"), ("Chrome/", "Chrome"),
                         ("Safari/", "Safari")):
        if marker in ua:
            browser = name
            break
    else:
        browser = ""
    if browser and os_name:
        return f"{browser} on {os_name}"
    return browser or os_name or "Unknown device"


def current_session(db):
    key = request_session_key()
    session = db["sessions"].get(key) if key else None
    if session and session.get("created", "") < session_cutoff():
        session = None  # expired — same as not logged in
    return key, session


def current_user(db):
    key, session = current_session(db)
    user = db["users"].get(session["user"]) if session else None
    if not user:
        abort(401, description="please log in")
    # keep "last seen" roughly current, without a write on every request
    now = datetime.now(timezone.utc)
    try:
        stale = (now - datetime.fromisoformat(session.get("last_seen") or session["created"])
                 ).total_seconds() > SESSION_TOUCH_EVERY
    except (ValueError, TypeError):
        stale = True
    if stale:
        session["last_seen"] = now.isoformat()
        storage.save_db(db)
    return user


def end_sessions(db, user_id, keep=None):
    """Log a user out everywhere — except the session `keep`, if given."""
    db["sessions"] = {k: s for k, s in db["sessions"].items()
                      if s["user"] != user_id or k == keep}


def sessions_view(db, user):
    current = request_session_key()
    cutoff = session_cutoff()
    out = [{
        "id": key[:16],
        "device": s.get("device") or "Unknown device",
        "created": s.get("created"),
        "last_seen": s.get("last_seen") or s.get("created"),
        "current": key == current,
    } for key, s in db["sessions"].items()
        if s["user"] == user["id"] and s.get("created", "") >= cutoff]
    return sorted(out, key=lambda s: (not s["current"], s["last_seen"] or ""), reverse=False)


# ── Rate limits ──────────────────────────────────────────────────────
# Cheap sliding-window caps on the unauthenticated endpoints that create
# things (accounts, a restored server, SSO round trips), so a script
# can't hammer them. Server-wide per action plus per client address:
# behind a reverse proxy every request shares one address, which simply
# makes the per-address cap act server-wide too. In memory, like the
# login throttle.
_rate_hits = {}
_rate_lock = threading.Lock()
RATE_LIMITS = {
    # action: (max hits, window in seconds) per client address
    "register": (10, 3600),
    "setup-restore": (10, 600),
    "sso-start": (30, 300),
}
RATE_GLOBAL_FACTOR = 5  # the whole server may do this many times the per-address cap


def rate_limit(action):
    limit, window = RATE_LIMITS[action]
    now = time.monotonic()
    addr = request.remote_addr or "?"
    with _rate_lock:
        for key, cap in (((action, addr), limit), ((action, "*"), limit * RATE_GLOBAL_FACTOR)):
            hits = [t for t in _rate_hits.get(key, []) if now - t < window]
            _rate_hits[key] = hits
            if len(hits) >= cap:
                wait = int(window - (now - hits[0])) + 1
                abort(429, description=f"too many attempts — try again in about {max(1, wait // 60)} min")
        for key in ((action, addr), (action, "*")):
            _rate_hits[key].append(now)


def wheel_member_users(db, wheel_id):
    return [u for u in db["users"].values() if wheel_id in u.get("wheels", [])]


def wheel_members(db, wheel_id):
    return sorted(u["name"] for u in wheel_member_users(db, wheel_id))


def wheel_pending_invitees(db, wheel_id):
    """Names of people invited to this wheel who haven't accepted yet —
    shown to the sharer as 'waiting to accept'. Someone who joined by
    another route in the meantime is already a member, so drops off."""
    return sorted(
        u["name"] for u in db["users"].values()
        if wheel_id not in u.get("wheels", [])
        and any(inv["wheel_id"] == wheel_id for inv in u.get("invites", []))
    )


def pending_invites_for(db, user):
    """The wheel invitations waiting on this user. A dangling invite —
    the wheel is gone, or they've since joined it another way — is
    skipped so the banner never offers something that can't be accepted."""
    out = []
    for inv in user.get("invites", []):
        wheel = db["wheels"].get(inv["wheel_id"])
        if wheel is None or inv["wheel_id"] in user.get("wheels", []):
            continue
        out.append({
            "wheel_id": wheel["id"],
            "type": wheel["type"],
            "name": wheel["name"],
            "from_name": inv.get("from_name", ""),
            "at": inv.get("at", ""),
        })
    return out


def unclaimed_wheel_ids(db):
    """Wheels nobody has in their list — exist only after migrating a
    pre-account database, holding that household's old wheels."""
    claimed = {wid for u in db["users"].values() for wid in u.get("wheels", [])}
    return [wid for wid in db["wheels"] if wid not in claimed]


def wheel_summary(db, wheel):
    return {
        "id": wheel["id"],
        "type": wheel["type"],
        "name": wheel["name"],
        "code": wheel["code"],
        "members": wheel_members(db, wheel["id"]),
        "pending": wheel_pending_invitees(db, wheel["id"]),
    }


def maps_api_key(db):
    """The Google Maps Platform key an admin has stored, or '' when
    restaurant location search is switched off. Never sent to the browser
    — only used server-side to proxy Places calls (see maps_search)."""
    return str((db.get("settings") or {}).get("google_maps_api_key") or "").strip()


def me_payload(db, user):
    wheels = [db["wheels"][wid] for wid in user.get("wheels", []) if wid in db["wheels"]]
    return {
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user.get("email") or "",
            "admin": bool(user.get("admin")),
            "has_password": bool(user.get("password")),
            "sso_linked": bool(user.get("oidc_sub")),
        },
        "prefs": user.get("prefs", {}),
        "wheels": [wheel_summary(db, w) for w in wheels],
        # wheels other people have shared with you, awaiting accept/decline
        "invites": pending_invites_for(db, user),
        # lets the first-wheel screen offer "keep your old wheels"
        "legacy_available": bool(unclaimed_wheel_ids(db)),
        # the restaurant add/edit form only offers inline place search when
        # the server has a key configured; otherwise it falls back to a link
        "maps_enabled": bool(maps_api_key(db)),
        # lets the account screen offer "link single sign-on"
        "sso": sso_public(db),
    }


def sso_public(db):
    cfg = oidc.oidc_settings(db)
    return {"enabled": cfg["enabled"], "label": cfg["button_label"],
            "password_login": oidc.password_login_allowed(db)}


def start_session(db, user):
    # a new log-in is a good moment to sweep out expired sessions
    cutoff = session_cutoff()
    db["sessions"] = {t: s for t, s in db["sessions"].items() if s.get("created", "") >= cutoff}
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    db["sessions"][token_key(token)] = {
        "user": user["id"],
        "created": now,
        "last_seen": now,
        "device": device_label(request.headers.get("User-Agent")),
    }
    return token


def drop_wheel_if_empty(db, wheel_id):
    if not wheel_member_users(db, wheel_id):
        db["wheels"].pop(wheel_id, None)
        # tidy up any invitations that were still pointing at it
        for u in db["users"].values():
            if u.get("invites"):
                u["invites"] = [i for i in u["invites"] if i["wheel_id"] != wheel_id]


@app.post("/api/auth/register")
def register():
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name", "")).strip()[:30]
    password = str(payload.get("password", ""))
    email = clean_email(payload.get("email"))  # optional
    # Registering through an invite link carries the share code along, so
    # the new account starts with that wheel already in its list.
    code = str(payload.get("code", "")).strip().upper().replace(" ", "")
    check_name(name)
    rate_limit("register")
    if len(password) < MIN_PASSWORD_LEN:
        abort(400, description=f"pick a password of at least {MIN_PASSWORD_LEN} characters")
    with _lock:
        db = storage.load_db()
        if user_by_name(db, name):
            abort(400, description="that name is already taken — log in instead?")
        ensure_email_free(db, email)
        first_account = not db["users"]  # the very first account bootstraps the server
        if not first_account:
            oidc.require_password_login(db)
        # A code can be a wheel's share code (join that wheel) or an admin
        # account-invite (create an account, join nothing). Wheels win the
        # namespace; invites never collide with a wheel code (see below).
        wheel = None
        invite = None
        if code:
            wheel = next((w for w in db["wheels"].values() if w["code"] == code), None)
            if wheel is None:
                invite = db.get("invites", {}).get(code)
            if wheel is None and invite is None:
                abort(400, description="that invite link doesn't work (anymore) — "
                                       "ask for a fresh one, or register without it")
        # Closed registration keeps casual sign-ups out on an internet-facing
        # server. The first account and anyone holding a valid invite (wheel
        # or admin) still get through.
        if not first_account and not settings_view(db)["registration_open"] \
                and wheel is None and invite is None:
            abort(403, description="registration is invite-only on this server right now — "
                                   "ask an admin for an invite link")
        user = new_user(db, name, email=email, wheel=wheel)
        set_password(user, password)
        if invite:
            db.setdefault("invites", {}).pop(code, None)  # single-use — spent now
        token = start_session(db, user)
        storage.save_db(db)
        me = me_payload(db, user)
        if wheel:
            me["joined"] = wheel["id"]
        return jsonify({"token": token, "me": me}), 201


def new_user(db, name, email="", wheel=None):
    """Create (and store) a fresh account. The very first account on the
    server becomes its admin. No password is set here — the caller does
    that, or leaves it off for an account that logs in through SSO."""
    user = {
        "id": "u-" + uuid.uuid4().hex[:10],
        "name": name,
        "email": email,
        "wheels": [wheel["id"]] if wheel else [],
        "prefs": {},
        "admin": not db["users"],
        "created": datetime.now(timezone.utc).isoformat(),
    }
    db["users"][user["id"]] = user
    return user


@app.post("/api/auth/login")
def login():
    payload = request.get_json(force=True, silent=True) or {}
    # `login` is a name or an e-mail address; `name` is the older field
    ident = str(payload.get("login") or payload.get("name") or "").strip()
    password = str(payload.get("password", ""))
    with _lock:
        db = storage.load_db()
        oidc.require_password_login(db)
        user = user_by_login(db, ident)
        # Throttle per account, so guessing via the name and via the
        # e-mail address draws from the same allowance.
        key = user["id"] if user else ident
        retry = login_retry_after(key)
        if retry:
            abort(429, description="too many attempts on this account — "
                                   f"wait about {max(1, retry // 60)} min and try again")
        if not password_ok(user, password):
            record_login_fail(key)
            abort(401, description="wrong name/e-mail or password")
        clear_login_fails(key)  # a good login wipes the slate
        token = start_session(db, user)
        storage.save_db(db)
        return jsonify({"token": token, "me": me_payload(db, user)})


@app.post("/api/auth/logout")
def logout():
    key = request_session_key()
    with _lock:
        db = storage.load_db()
        if key and db["sessions"].pop(key, None):
            storage.save_db(db)
    return "", 204


@app.get("/api/me/sessions")
def list_sessions():
    """Where you're logged in: device, when, and which one is this."""
    with _lock:
        db = storage.load_db()
        user = current_user(db)
        return jsonify(sessions_view(db, user))


@app.delete("/api/me/sessions/<session_id>")
def end_session(session_id):
    """Log one of your devices out (by the short id from the list)."""
    with _lock:
        db = storage.load_db()
        user = current_user(db)
        match = [k for k, s in db["sessions"].items()
                 if s["user"] == user["id"] and k[:16] == session_id]
        if not match:
            abort(404, description="no such session (already logged out?)")
        for key in match:
            db["sessions"].pop(key)
        storage.save_db(db)
        return jsonify(sessions_view(db, user))


@app.post("/api/me/sessions/end-others")
def end_other_sessions():
    """Log out every device but this one — e.g. after losing a phone."""
    with _lock:
        db = storage.load_db()
        user = current_user(db)
        end_sessions(db, user["id"], keep=request_session_key())
        storage.save_db(db)
        return jsonify(sessions_view(db, user))


@app.get("/api/me")
def me():
    with _lock:
        db = storage.load_db()
        return jsonify(me_payload(db, current_user(db)))


@app.put("/api/me/prefs")
def update_prefs():
    payload = request.get_json(force=True, silent=True)
    with _lock:
        db = storage.load_db()
        user = current_user(db)
        user["prefs"] = clean_prefs(db, user, payload)
        storage.save_db(db)
        return jsonify(user["prefs"])


def set_password(user, new_password):
    """Give the account a fresh salt + hash. A new salt on every change
    means two identical passwords never share a stored hash."""
    user["salt"] = secrets.token_hex(16)
    user["password"] = hash_password(new_password, user["salt"])


@app.put("/api/me/password")
def change_password():
    """Change your own password. The current one must check out; the new
    one meets the same floor as registration. Other devices stay logged
    in — this isn't a 'kick everyone' button, just a password swap."""
    payload = request.get_json(force=True, silent=True) or {}
    current = str(payload.get("current", ""))
    new_password = str(payload.get("new", ""))
    if len(new_password) < MIN_PASSWORD_LEN:
        abort(400, description=f"pick a password of at least {MIN_PASSWORD_LEN} characters")
    with _lock:
        db = storage.load_db()
        user = current_user(db)
        oidc.require_password_login(db)
        # an SSO-only account has no password yet — it may set one freely
        if user.get("password") and not password_ok(user, current):
            abort(403, description="that's not your current password")
        set_password(user, new_password)
        # a new password should shut out whoever might know the old one
        end_sessions(db, user["id"], keep=request_session_key())
        storage.save_db(db)
    return jsonify({"changed": True})


@app.put("/api/me/email")
def change_email():
    """Set, change or clear your own e-mail address. It's what links your
    account to a single-sign-on identity, so changing it takes your
    current password (an SSO-only account without one skips that)."""
    payload = request.get_json(force=True, silent=True) or {}
    email = clean_email(payload.get("email"))
    password = str(payload.get("password", ""))
    with _lock:
        db = storage.load_db()
        user = current_user(db)
        # with passwords switched off there's nothing to confirm with
        if user.get("password") and oidc.password_login_allowed(db) and not password_ok(user, password):
            abort(403, description="that's not your current password")
        ensure_email_free(db, email, user["id"])
        user["email"] = email
        storage.save_db(db)
        return jsonify(me_payload(db, user))


@app.get("/api/config")
def public_config():
    """The handful of facts the logged-out screen needs before anyone's
    authenticated — currently just whether open registration is on, so it
    can show or hide the 'Create account' tab. Nothing sensitive: with no
    accounts yet the server always allows the first sign-up, so this
    reports open until that bootstrap account exists."""
    with _lock:
        db = storage.load_db()
        fresh = not db["users"]
        open_reg = settings_view(db)["registration_open"] or fresh
        sso = sso_public(db)
    return jsonify({
        "registration_open": bool(open_reg),
        "sso": sso,
        "password_login": sso["password_login"] or fresh,
        # a brand-new server offers "restore from a backup" on its first screen
        "fresh": fresh,
    })
