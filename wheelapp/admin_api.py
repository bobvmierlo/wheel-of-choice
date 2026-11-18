"""admin_api — part of the Wheel of Choice server (see server.py)."""
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import abort, jsonify, request

from .core import (
    ALL_ZONES,
    _lock,
    app,
    apply_runtime_settings,
    clamp_evening_from,
    clamp_horizon,
    clean_timezone,
    settings_view,
)
from . import i18n, accounts, calendars, oidc, storage


# ── Admin ────────────────────────────────────────────────────────────
def require_admin(db):
    user = accounts.current_user(db)
    if not user.get("admin"):
        abort(403, description="admins only")
    return user


def admin_user_list(db):
    def sharing_with(user):
        others = set()
        for wid in user.get("wheels", []):
            for member in accounts.wheel_member_users(db, wid):
                if member["id"] != user["id"]:
                    others.add(member["name"])
        return sorted(others)

    users = sorted(db["users"].values(), key=lambda u: u.get("created", ""))
    return [{
        "id": u["id"],
        "name": u["name"],
        "email": u.get("email") or "",
        "admin": bool(u.get("admin")),
        "has_password": bool(u.get("password")),
        "sso_linked": bool(u.get("oidc_sub")),
        "created": u.get("created"),
        "wheel_count": len(u.get("wheels", [])),
        "sharing_with": sharing_with(u),
    } for u in users]


@app.get("/api/admin/users")
def admin_users():
    with _lock:
        db = storage.load_db()
        require_admin(db)
        return jsonify(admin_user_list(db))


@app.get("/api/admin/settings")
def admin_get_settings():
    with _lock:
        db = storage.load_db()
        require_admin(db)
        # the raw Maps key rides along only on this admin-only endpoint, so
        # the admin can see and edit it; it never appears in any other view
        return jsonify({**settings_view(db), "zones": ALL_ZONES,
                        "google_maps_api_key": accounts.maps_api_key(db)})


@app.put("/api/admin/settings")
def admin_put_settings():
    """Tune the server settings: the three poll knobs (timezone, when an
    evening starts, how far ahead a poll may reach) and whether open
    registration is on. Values are clamped/validated; the calendar cache
    is cleared so busy hints recompute against the new timezone/window."""
    payload = request.get_json(force=True, silent=True) or {}
    with _lock:
        db = storage.load_db()
        require_admin(db)
        stored = db.setdefault("settings", {})
        current = settings_view(db)  # fall back to what's in force, not the env default
        if "timezone" in payload:
            stored["timezone"] = clean_timezone(payload.get("timezone"), current["timezone"])
        if "evening_from" in payload:
            stored["evening_from"] = clamp_evening_from(
                payload.get("evening_from"), current["evening_from"])
        if "poll_horizon_days" in payload:
            stored["poll_horizon_days"] = clamp_horizon(
                payload.get("poll_horizon_days"), current["poll_horizon_days"])
        if "registration_open" in payload:
            stored["registration_open"] = bool(payload.get("registration_open"))
        if "google_maps_api_key" in payload:
            # an empty value clears it (switches location search back off)
            key = str(payload.get("google_maps_api_key") or "").strip()[:200]
            if key:
                stored["google_maps_api_key"] = key
            else:
                stored.pop("google_maps_api_key", None)
        storage.save_db(db)
        apply_runtime_settings(db)
        view = {**settings_view(db), "google_maps_api_key": accounts.maps_api_key(db)}
    with calendars._cal_lock:  # busy-sets were digested against the old timezone/window
        calendars._cal_cache.clear()
    return jsonify(view)


# ── Account invites ──────────────────────────────────────────────────
# When registration is closed, an admin hands out single-use invite codes
# so specific people can still create an account. They share the code
# format with wheel codes but live in their own bucket and are minted so
# they never collide with a wheel's code.
def invite_view(db):
    invites = db.get("invites", {}) or {}
    return sorted(
        ({"code": inv["code"], "created": inv.get("created"), "by": inv.get("by_name", "")}
         for inv in invites.values()),
        key=lambda i: i.get("created") or "", reverse=True,
    )


@app.get("/api/admin/invites")
def admin_list_invites():
    with _lock:
        db = storage.load_db()
        require_admin(db)
        return jsonify(invite_view(db))


@app.post("/api/admin/invites")
def admin_create_invite():
    with _lock:
        db = storage.load_db()
        admin = require_admin(db)
        invites = db.setdefault("invites", {})
        wheel_codes = {w["code"] for w in db["wheels"].values()}
        code = storage.new_invite_code()
        while code in invites or code in wheel_codes:  # keep the namespaces disjoint
            code = storage.new_invite_code()
        invites[code] = {
            "code": code,
            "created": datetime.now(timezone.utc).isoformat(),
            "by": admin["id"],
            "by_name": admin["name"],
        }
        storage.save_db(db)
        return jsonify({"code": code, "invites": invite_view(db)}), 201


@app.delete("/api/admin/invites/<code>")
def admin_revoke_invite(code):
    with _lock:
        db = storage.load_db()
        require_admin(db)
        if db.setdefault("invites", {}).pop(code, None) is None:
            abort(404, description="no such invite (already used or revoked?)")
        storage.save_db(db)
        return jsonify(invite_view(db))


@app.put("/api/admin/users/<user_id>")
def admin_set_admin(user_id):
    payload = request.get_json(force=True, silent=True) or {}
    make_admin = bool(payload.get("admin"))
    with _lock:
        db = storage.load_db()
        admin = require_admin(db)
        target = db["users"].get(user_id)
        if target is None:
            abort(404, description="no such user")
        if target["id"] == admin["id"] and not make_admin:
            abort(400, description="you can't take away your own admin rights")
        target["admin"] = make_admin
        storage.save_db(db)
        return jsonify(admin_user_list(db))


@app.put("/api/admin/users/<user_id>/password")
def admin_reset_password(user_id):
    """Set a temporary password for a user who's locked out — the only
    recovery path in an app with no email. It logs that user out of every
    device (their old sessions die), so they come back in with the new
    password and can then change it themselves. Never reveals or changes
    anyone's admin rights; the admin's own token is untouched."""
    payload = request.get_json(force=True, silent=True) or {}
    new_password = str(payload.get("new", ""))
    if len(new_password) < accounts.MIN_PASSWORD_LEN:
        abort(400, description=i18n.msg('pick a password of at least {min} characters', min=accounts.MIN_PASSWORD_LEN))
    with _lock:
        db = storage.load_db()
        require_admin(db)
        oidc.require_password_login(db)
        target = db["users"].get(user_id)
        if target is None:
            abort(404, description="no such user")
        accounts.set_password(target, new_password)
        # force the target to log back in everywhere with the new password
        accounts.end_sessions(db, user_id)
        accounts.clear_login_fails(target["id"])  # don't punish them for the lock-out
        storage.save_db(db)
    return jsonify({"reset": True})


@app.put("/api/admin/users/<user_id>/email")
def admin_set_email(user_id):
    """Give an account its e-mail address (or clear it) — the step that
    lets an existing account be matched to its single-sign-on identity."""
    payload = request.get_json(force=True, silent=True) or {}
    email = accounts.clean_email(payload.get("email"))
    with _lock:
        db = storage.load_db()
        require_admin(db)
        target = db["users"].get(user_id)
        if target is None:
            abort(404, description="no such user")
        accounts.ensure_email_free(db, email, user_id)
        target["email"] = email
        storage.save_db(db)
        return jsonify(admin_user_list(db))


@app.post("/api/admin/users/<user_id>/unlink-sso")
def admin_unlink_sso(user_id):
    """Detach an account from its sign-on identity, e.g. when it got
    matched to the wrong person. Their next SSO login matches afresh."""
    with _lock:
        db = storage.load_db()
        require_admin(db)
        target = db["users"].get(user_id)
        if target is None:
            abort(404, description="no such user")
        target.pop("oidc_sub", None)
        storage.save_db(db)
        return jsonify(admin_user_list(db))


@app.get("/api/admin/sso")
def admin_get_sso():
    with _lock:
        db = storage.load_db()
        require_admin(db)
        return jsonify(oidc.oidc_settings_view(db))


@app.put("/api/admin/sso")
def admin_put_sso():
    """Configure single sign-on. An omitted or empty client_secret keeps
    the stored one (the panel never sees it); clear_secret removes it."""
    payload = request.get_json(force=True, silent=True) or {}
    with _lock:
        db = storage.load_db()
        admin = require_admin(db)
        stored = db.setdefault("settings", {}).setdefault("oidc", {})
        for key, cap in (("issuer", 300), ("client_id", 200), ("public_url", 300),
                         ("button_label", 40)):
            if key in payload:
                value = str(payload.get(key) or "").strip()[:cap]
                if key in ("issuer", "public_url") and value:
                    parsed = urlparse(value)
                    if parsed.scheme not in ("http", "https") or not parsed.netloc:
                        abort(400, description=i18n.msg('the {field} must be an http(s):// address', field=key.replace('_', ' ')))
                    if key == "public_url":
                        value = value.rstrip("/")
                stored[key] = value
        if payload.get("client_secret"):
            stored["client_secret"] = str(payload["client_secret"]).strip()[:500]
        if payload.get("clear_secret"):
            stored.pop("client_secret", None)
        for key in ("match_email", "require_verified_email", "allow_signup"):
            if key in payload:
                stored[key] = bool(payload.get(key))
        warning = ""
        if "disable_password_login" in payload:
            disable = bool(payload.get("disable_password_login"))
            if disable and not stored.get("disable_password_login"):
                # Guard rails: only with working SSO config, and never in a
                # way that locks out the admin flipping the switch. The rest
                # of the form still saves — only this switch stays put.
                if not oidc.oidc_settings(db)["enabled"]:
                    warning = i18n.msg("password log-in stays on: set up single sign-on first "
                               "(issuer, client ID and secret)")
                elif not admin.get("oidc_sub"):
                    warning = i18n.msg("password log-in stays on: link your own account to single "
                               "sign-on first (🔑 → Single sign-on → Link) — otherwise you'd "
                               "lock yourself out")
            if not warning:
                stored["disable_password_login"] = disable
        storage.save_db(db)
        return jsonify({**oidc.oidc_settings_view(db), "warning": i18n.t(warning) if warning else ""})


@app.post("/api/admin/sso/test")
def admin_test_sso():
    """Fetch the provider's metadata now, so a typo in the issuer shows up
    in the admin panel instead of on someone's login."""
    with _lock:
        db = storage.load_db()
        require_admin(db)
        cfg = oidc.oidc_settings(db)
    if not cfg["issuer"]:
        abort(400, description="fill in the issuer URL first")
    try:
        meta = oidc.oidc_discover(cfg["issuer"], fresh=True)
    except ValueError as err:
        abort(502, description=i18n.reason(err))
    return jsonify({"ok": True, "issuer": meta["issuer"],
                    "authorization_endpoint": meta["authorization_endpoint"]})


@app.post("/api/admin/users/<user_id>/unshare")
def admin_unshare(user_id):
    """Pull a user out of every wheel they share with someone else. The
    other members keep those wheels; the user keeps any solo wheels."""
    with _lock:
        db = storage.load_db()
        require_admin(db)
        target = db["users"].get(user_id)
        if target is None:
            abort(404, description="no such user")
        shared = [wid for wid in target.get("wheels", [])
                  if len(accounts.wheel_member_users(db, wid)) > 1]
        if not shared:
            abort(400, description=i18n.msg("{target} isn't sharing any wheels", target=target['name']))
        target["wheels"] = [wid for wid in target["wheels"] if wid not in shared]
        for wid in shared:
            target.get("prefs", {}).pop(wid, None)
        storage.save_db(db)
        return jsonify(admin_user_list(db))


@app.delete("/api/admin/users/<user_id>")
def admin_delete_user(user_id):
    with _lock:
        db = storage.load_db()
        admin = require_admin(db)
        if user_id == admin["id"]:
            abort(400, description="you can't delete your own account")
        target = db["users"].pop(user_id, None)
        if target is None:
            abort(404, description="no such user")
        accounts.end_sessions(db, user_id)
        for wid in target.get("wheels", []):
            accounts.drop_wheel_if_empty(db, wid)
        storage.save_db(db)
        return jsonify(admin_user_list(db))
