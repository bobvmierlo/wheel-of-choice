"""push — part of the Wheel of Choice server (see server.py)."""
import base64
import json
import threading

from flask import abort, jsonify, request

from .core import (
    DATA_DIR,
    MAX_PUSH_SUBS,
    PUSH_TTL,
    VAPID_KEY_FILE,
    VAPID_SUBJECT,
    WebPushException,
    _lock,
    app,
    webpush,
)
from . import accounts, storage


# ── Push notifications (Web Push / VAPID) ────────────────────────────
# Standard Web Push: the server signs every message with its own VAPID
# key and POSTs it straight to whatever endpoint each browser handed us
# (Apple's relay for iPhones, Google's for Chrome, Mozilla's for
# Firefox). Payloads are end-to-end encrypted, so the relays only ever
# see ciphertext — no Firebase project, no accounts with anyone.
_vapid_lock = threading.Lock()
_vapid_public_key = None


def vapid_public_key():
    """Base64url public key the browser needs as applicationServerKey.
    Generates the keypair on first use. cryptography ships with
    pywebpush, so it's only imported when push is actually available."""
    global _vapid_public_key
    if webpush is None:
        return None
    with _vapid_lock:
        if _vapid_public_key:
            return _vapid_public_key
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        if VAPID_KEY_FILE.exists():
            key = serialization.load_pem_private_key(VAPID_KEY_FILE.read_bytes(), password=None)
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            key = ec.generate_private_key(ec.SECP256R1())
            VAPID_KEY_FILE.write_bytes(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            VAPID_KEY_FILE.chmod(0o600)
        raw = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        _vapid_public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return _vapid_public_key


def clean_subscription(payload):
    """Whittle a browser's PushSubscription.toJSON() down to the fields
    delivery needs; rejects anything that doesn't look like one."""
    if not isinstance(payload, dict):
        abort(400, description="that doesn't look like a push subscription")
    endpoint = str(payload.get("endpoint", ""))
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        abort(400, description="that doesn't look like a push subscription")
    return {
        "endpoint": endpoint[:1000],
        "keys": {"p256dh": str(keys["p256dh"])[:200], "auth": str(keys["auth"])[:200]},
    }


def store_subscription(db, user, sub):
    """One endpoint belongs to one account: a shared tablet buzzes for
    whoever enabled notifications on it last."""
    for other in db["users"].values():
        if other.get("push_subs"):
            other["push_subs"] = [
                s for s in other["push_subs"] if s["endpoint"] != sub["endpoint"]
            ]
    subs = user.setdefault("push_subs", [])
    subs.append(sub)
    user["push_subs"] = subs[-MAX_PUSH_SUBS:]


def push_to_users(users, title, body, wheel, tag=None, gcal_url=None, url=None):
    """Queue one notification to every device of `users`. Delivery runs
    on a background thread: a round-trip to Apple's or Google's relay
    can take seconds, and the member who spun is still waiting for
    their HTTP response. Notifications sharing a tag replace each other
    on the device; the default groups per wheel, so round events
    coalesce — pass a distinct tag for news that shouldn't overwrite an
    actionable alert (like a pick waiting for a thumbs-up). `gcal_url`
    rides along for the service worker to offer as an 'Add to calendar'
    action button (platforms without action support just get the plain
    notification)."""
    if webpush is None or vapid_public_key() is None:
        return  # the call also guarantees the key file exists before delivery reads it
    targets = [
        (u["id"], {"endpoint": s["endpoint"], "keys": dict(s["keys"])})
        for u in users for s in u.get("push_subs", [])
    ]
    if not targets:
        return
    data = {
        "title": title,
        "body": body,
        "tag": tag or wheel["id"],
        "url": url or f"/#{wheel['id']}",
    }
    if gcal_url:
        data["gcal_url"] = gcal_url
    payload = json.dumps(data, ensure_ascii=False)
    threading.Thread(target=_deliver_pushes, args=(targets, payload), daemon=True).start()


def _deliver_pushes(targets, payload):
    dead = []
    for user_id, sub in targets:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=str(VAPID_KEY_FILE),
                vapid_claims={"sub": VAPID_SUBJECT},  # fresh dict each call — pywebpush mutates it
                ttl=PUSH_TTL,
                headers={"Urgency": "high"},
            )
        except WebPushException as err:
            status = err.response.status_code if err.response is not None else None
            if status in (404, 410):  # the device unsubscribed — forget it
                dead.append((user_id, sub["endpoint"]))
            else:
                app.logger.warning("push to %s… failed: %s", sub["endpoint"][:60], err)
        except Exception as err:  # DNS down, relay unreachable — never take the app with us
            app.logger.warning("push delivery error: %s", err)
    if dead:
        with _lock:
            db = storage.load_db()
            changed = False
            for user_id, endpoint in dead:
                user = db["users"].get(user_id)
                if user and any(s["endpoint"] == endpoint for s in user.get("push_subs", [])):
                    user["push_subs"] = [
                        s for s in user["push_subs"] if s["endpoint"] != endpoint
                    ]
                    changed = True
            if changed:
                storage.save_db(db)


@app.get("/api/push/status")
def push_status():
    with _lock:
        db = storage.load_db()
        accounts.current_user(db)
    return jsonify({"enabled": webpush is not None, "public_key": vapid_public_key()})


@app.post("/api/push/subscribe")
def push_subscribe():
    if webpush is None:
        abort(503, description="this server can't send notifications yet — "
                               "install pywebpush and restart (see the README)")
    sub = clean_subscription(request.get_json(force=True, silent=True))
    with _lock:
        db = storage.load_db()
        store_subscription(db, accounts.current_user(db), sub)
        storage.save_db(db)
    return jsonify({"subscribed": True})


@app.post("/api/push/unsubscribe")
def push_unsubscribe():
    payload = request.get_json(force=True, silent=True) or {}
    endpoint = str(payload.get("endpoint", ""))
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        before = user.get("push_subs", [])
        user["push_subs"] = [s for s in before if s["endpoint"] != endpoint]
        if len(user["push_subs"]) != len(before):
            storage.save_db(db)
    return "", 204


@app.post("/api/push/resubscribe")
def push_resubscribe():
    """Browsers occasionally rotate a subscription behind the app's
    back; the service worker reports the swap here. A worker has no
    login token, so this is authenticated by knowing the old endpoint —
    an unguessable URL only that browser and this server ever saw."""
    payload = request.get_json(force=True, silent=True) or {}
    old_endpoint = str(payload.get("old_endpoint") or "")
    sub = clean_subscription(payload.get("subscription"))
    if not old_endpoint:
        return "", 204
    with _lock:
        db = storage.load_db()
        owner = next(
            (u for u in db["users"].values()
             if any(s["endpoint"] == old_endpoint for s in u.get("push_subs", []))),
            None,
        )
        if owner is not None:
            owner["push_subs"] = [
                s for s in owner["push_subs"] if s["endpoint"] != old_endpoint
            ]
            store_subscription(db, owner, sub)
            storage.save_db(db)
    return "", 204
