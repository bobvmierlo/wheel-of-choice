#!/usr/bin/env python3
"""Flask server for Wheel of Choice.

Serves the static site plus a small JSON API. Since v3 the app is built
around stand-alone *wheels*: every wheel is its own little world — a
list of things to spin for, a spin history, and its own share code. A
user can have any number of wheels, and a wheel can be shared with any
number of accounts: joining with a wheel's code adds that one wheel to
your list, nothing else. Personal filter preferences are stored per
user per wheel, so partners sharing a wheel each keep their own filters.

There are three kinds of wheel:
  - "holidays"    : whole-country holiday destinations (seeded from the
                    catalogue via onboarding questions)
  - "citytrips"   : city trips in and around Europe (seeded the same way)
  - "restaurants" : a fully custom list — no seeding, you add every
                    entry yourself, and there are no vetoes: what the
                    wheel says, goes.

Travel wheels carry the current *round*: which destinations have been
vetoed (every member gets exactly one veto per round, tracked by user
id) and the pick that is waiting for the partner's thumbs-up. A pick
only lands in the history once everyone who could still veto has had
their say. The round is kept on the server so all members' devices see
the same thing. Restaurant wheels skip all of that — an accepted spin
goes straight into the history.

The first account ever registered becomes the admin; admins can promote
other users, delete accounts, and pull a user out of shared wheels (see
the /api/admin endpoints).

Storage: data/db.json next to this file by default; override the
directory with the WHEEL_DATA_DIR environment variable (the systemd
unit sets it to /var/lib/wheel-of-choice). Old databases are migrated
automatically: v2 *spaces* are split into independent wheels (the old
space code keeps working — it now joins the holidays wheel), and the
even older single-household layout becomes unclaimed wheels that the
first registered account can adopt.

Run directly for a quick start:

    python3 server.py            # serves on http://0.0.0.0:8000
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

try:  # push notifications are optional — everything else works without
    from pywebpush import WebPushException, webpush
except ImportError:
    webpush = None
    WebPushException = Exception

try:  # calendar availability is optional — date polls work fully without
    from icalendar import Calendar as ICalendar
    import recurring_ical_events
except ImportError:
    ICalendar = None
    recurring_ical_events = None

ROOT = Path(__file__).parent.resolve()
# HOLIDAY_DATA_DIR is the pre-rename name — still honoured so a git pull
# under an old unit file can't silently start with an empty database
DATA_DIR = Path(
    os.environ.get("WHEEL_DATA_DIR")
    or os.environ.get("HOLIDAY_DATA_DIR", str(ROOT / "data"))
)
DB_FILE = DATA_DIR / "db.json"
UPDATE_FLAG = DATA_DIR / "update-requested"

# Web Push (see the README's notifications section). The VAPID keypair
# identifies this server to the browsers' push relays; it's generated on
# first use and lives next to the database — losing it silently breaks
# every existing subscription, so it's part of what a backup should hold.
VAPID_KEY_FILE = DATA_DIR / "vapid-private-key.pem"
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")
PUSH_TTL = 12 * 3600  # seconds the relay keeps an undelivered push — a spin is stale news after that
MAX_PUSH_SUBS = 10  # devices per user; the oldest subscription falls off

WHEEL_TYPES = {
    "holidays": {"label": "Holidays", "travel": True, "seed": ROOT / "seed-destinations.json"},
    "citytrips": {"label": "City trips", "travel": True, "seed": ROOT / "seed-citytrips.json"},
    "restaurants": {"label": "Restaurants", "travel": False, "seed": None},
}
TRAVEL_TYPES = [t for t, meta in WHEEL_TYPES.items() if meta["travel"]]

BUDGETS = {"low", "mid", "high"}
DISTANCES = {"regional", "europe", "longhaul"}
VIBES = {"nature", "culture", "food", "beach", "nightlife", "adventure", "wellness", "winter"}
SEASONS = {"spring", "summer", "autumn", "winter"}
PARTIES = {"couple", "group"}

# Onboarding vocabulary
HOME_REGIONS = {"northwest", "british", "nordic", "south", "central"}
ROAM_DISTANCES = {
    "close": {"regional"},
    "europe": {"regional", "europe"},
    "anywhere": set(DISTANCES),
}
BUDGET_STYLES = BUDGETS | {"mix"}

HISTORY_LIMIT = 50
MAX_SEED_FAVORITES = 8  # per wheel, when onboarding picks favourites
HISTORY_STATUSES = {"booked", "visited"}  # a pick's life after the spin
SESSION_TTL_DAYS = 90  # log-ins older than this expire

# Date polls (restaurant wheels): once a pick lands, members coordinate an
# evening. A poll lives on the history entry, so it syncs, dies and clears
# with the entry. The evening window is what "busy" means for a dinner.
POLL_MAX_DATES = 10  # candidate dates a proposer may put up
POLL_HORIZON_DAYS = 60  # how far ahead a poll (and calendar lookahead) reaches
LOCAL_TZ = ZoneInfo(os.environ.get("WHEEL_TZ", "Europe/Amsterdam"))
EVENING_FROM = 17  # local hour a dinner evening starts (17:00–23:59 counts as busy)

# Personal calendar feeds (secret ICS URLs) power the poll's busy/free hints.
# Read-only, per user, never shown to anyone else — see the README.
MAX_ICS_FEEDS = 4  # feeds per account
ICS_TTL = 20 * 60  # seconds a fetched calendar's busy-set is cached
ICS_TIMEOUT = 8  # seconds to wait on a feed before giving up
ICS_MAX_BYTES = 2_000_000  # refuse calendars larger than this

app = Flask(__name__)
_lock = threading.Lock()

SERVER_STARTED = datetime.now(timezone.utc).isoformat()


def git_version():
    """Short hash, date and subject of the checked-out commit. All None
    when git or the repo isn't available (e.g. a tarball deploy). The
    safe.directory override is needed under systemd: the repo is owned
    by root while the service runs as an unprivileged DynamicUser."""
    none = {"commit": None, "commit_date": None, "commit_subject": None}
    try:
        out = subprocess.run(
            ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT),
             "log", "-1", "--format=%h%n%cI%n%s"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return none
    lines = out.stdout.strip().split("\n") if out.returncode == 0 else []
    if len(lines) < 3:
        return none
    return {"commit": lines[0], "commit_date": lines[1], "commit_subject": lines[2]}


GIT_VERSION = git_version()


@app.errorhandler(HTTPException)
def as_json(err):
    """The frontend shows API error messages to the user — send JSON."""
    return jsonify(error=err.description), err.code


# ── Storage ──────────────────────────────────────────────────────────
def is_travel(wheel):
    return WHEEL_TYPES[wheel["type"]]["travel"]


def catalog(wheel_type):
    return json.loads(WHEEL_TYPES[wheel_type]["seed"].read_text(encoding="utf-8"))


def default_links(name):
    """Starter reading links for a seeded destination. The catalogue's
    tags are hand-curated editorial guesses; these links point at places
    to read up on (and double-check) a pick. Wikivoyage/Wikipedia keep
    redirects for alternate names (Türkiye → Turkey), so building the
    URL from the display name is safe."""
    slug = quote(name.replace(" ", "_"))
    return [
        {"label": "Wikivoyage travel guide", "url": f"https://en.wikivoyage.org/wiki/{slug}"},
        {"label": "Wikipedia", "url": f"https://en.wikipedia.org/wiki/{slug}"},
    ]


def full_catalog_destinations(wheel_type):
    """Full catalogue as shipped — used when migrating pre-account data."""
    dests = []
    for entry in catalog(wheel_type):
        dest = {k: v for k, v in entry.items() if k != "near"}
        dest.setdefault("notes", "")
        dest.setdefault("links", default_links(entry["name"]))
        dests.append(dest)
    return dests


def new_invite_code():
    # No 0/O/1/I — the code gets read out loud across the sofa.
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)
    )


def empty_round():
    return {"vetoes": {}, "pending": None}


def new_wheel(wheel_type, name=""):
    return {
        "id": "w-" + uuid.uuid4().hex[:10],
        "code": new_invite_code(),
        "type": wheel_type,
        "name": name or WHEEL_TYPES[wheel_type]["label"],
        "destinations": [],
        "history": [],
        "round": empty_round(),
    }


def ensure_admin(db):
    """The first account ever registered is the admin — backfill databases
    from before admins existed. Later admins are appointed by an admin."""
    users = list(db["users"].values())
    if users and not any(u.get("admin") for u in users):
        min(users, key=lambda u: u.get("created", ""))["admin"] = True
        save_db(db)
    return db


def migrate_db(db):
    """v1 (single household, no accounts) and v2 (accounts + shared
    spaces of two fixed wheels) → v3 (stand-alone wheels, each with its
    own share code, each user holding a list of wheel ids)."""
    if "users" not in db:
        # v1 → v2 shape first: the old wheels become one onboarded space.
        wheels = db.get("wheels")
        if wheels is None and "destinations" in db:  # even older flat layout
            wheels = {"holidays": {
                "destinations": db.get("destinations", []),
                "history": db.get("history", []),
            }}
        db = {"version": 2, "users": {}, "sessions": {}, "spaces": {}}
        if wheels:
            for wheel_type in TRAVEL_TYPES:
                wheels.setdefault(wheel_type, {
                    "destinations": full_catalog_destinations(wheel_type),
                    "history": [],
                })
            db["spaces"]["s-legacy"] = {
                "id": "s-legacy", "code": new_invite_code(),
                "onboarded": True, "wheels": wheels,
            }
    # v2 → v3: every onboarded space splits into independent wheels. A
    # space that never finished onboarding holds nothing worth keeping —
    # its members simply start at the "create your first wheel" screen.
    new = {"version": 3, "users": {}, "sessions": db.get("sessions", {}), "wheels": {}}
    space_wheel_ids = {}
    for space in db.get("spaces", {}).values():
        ids = []
        if space.get("onboarded"):
            for wheel_type in TRAVEL_TYPES:
                data = space.get("wheels", {}).get(wheel_type) or {}
                wheel = new_wheel(wheel_type)
                if wheel_type == "holidays":
                    # invite links with the old space code stay usable —
                    # they now join the holidays wheel
                    wheel["code"] = space.get("code") or wheel["code"]
                wheel["destinations"] = data.get("destinations", [])
                wheel["history"] = data.get("history", [])
                wheel["round"] = data.get("round") or empty_round()
                new["wheels"][wheel["id"]] = wheel
                ids.append(wheel["id"])
        space_wheel_ids[space["id"]] = ids
    for user in db.get("users", {}).values():
        user = dict(user)
        wheel_ids = space_wheel_ids.get(user.pop("space", None), [])
        old_prefs = user.get("prefs") or {}
        user["wheels"] = list(wheel_ids)
        user["prefs"] = {
            wid: old_prefs[new["wheels"][wid]["type"]]
            for wid in wheel_ids if new["wheels"][wid]["type"] in old_prefs
        }
        new["users"][user["id"]] = user
    return new


def load_db():
    if not DB_FILE.exists():
        return {"version": 3, "users": {}, "sessions": {}, "wheels": {}}
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))
    if db.get("version", 0) < 3:
        db = migrate_db(db)
        save_db(db)
    return ensure_admin(db)


def save_db(db):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_FILE)


# ── Accounts ─────────────────────────────────────────────────────────
def hash_password(password, salt_hex):
    return hashlib.scrypt(
        password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1
    ).hex()


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
        if wheel is None or not is_travel(wheel):
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


def current_user(db):
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    session = db["sessions"].get(token)
    if session and session.get("created", "") < session_cutoff():
        session = None  # expired — same as not logged in
    user = db["users"].get(session["user"]) if session else None
    if not user:
        abort(401, description="please log in")
    return user


def wheel_member_users(db, wheel_id):
    return [u for u in db["users"].values() if wheel_id in u.get("wheels", [])]


def wheel_members(db, wheel_id):
    return sorted(u["name"] for u in wheel_member_users(db, wheel_id))


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
    }


def me_payload(db, user):
    wheels = [db["wheels"][wid] for wid in user.get("wheels", []) if wid in db["wheels"]]
    return {
        "user": {"id": user["id"], "name": user["name"], "admin": bool(user.get("admin"))},
        "prefs": user.get("prefs", {}),
        "wheels": [wheel_summary(db, w) for w in wheels],
        # lets the first-wheel screen offer "keep your old wheels"
        "legacy_available": bool(unclaimed_wheel_ids(db)),
    }


def start_session(db, user):
    # a new log-in is a good moment to sweep out expired sessions
    cutoff = session_cutoff()
    db["sessions"] = {t: s for t, s in db["sessions"].items() if s.get("created", "") >= cutoff}
    token = secrets.token_urlsafe(32)
    db["sessions"][token] = {
        "user": user["id"],
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return token


def drop_wheel_if_empty(db, wheel_id):
    if not wheel_member_users(db, wheel_id):
        db["wheels"].pop(wheel_id, None)


@app.post("/api/auth/register")
def register():
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name", "")).strip()[:30]
    password = str(payload.get("password", ""))
    # Registering through an invite link carries the share code along, so
    # the new account starts with that wheel already in its list.
    code = str(payload.get("code", "")).strip().upper().replace(" ", "")
    if len(name) < 2:
        abort(400, description="pick a name of at least 2 characters")
    if len(password) < 4:
        abort(400, description="pick a password of at least 4 characters")
    with _lock:
        db = load_db()
        if any(u["name"].lower() == name.lower() for u in db["users"].values()):
            abort(400, description="that name is already taken — log in instead?")
        wheel = None
        if code:
            wheel = next((w for w in db["wheels"].values() if w["code"] == code), None)
            if wheel is None:
                abort(400, description="that invite link doesn't work (anymore) — "
                                       "ask for a fresh one, or register without it")
        user = {
            "id": "u-" + uuid.uuid4().hex[:10],
            "name": name,
            "salt": secrets.token_hex(16),
            "wheels": [wheel["id"]] if wheel else [],
            "prefs": {},
            "admin": not db["users"],  # the very first account runs the place
            "created": datetime.now(timezone.utc).isoformat(),
        }
        user["password"] = hash_password(password, user["salt"])
        db["users"][user["id"]] = user
        token = start_session(db, user)
        save_db(db)
        me = me_payload(db, user)
        if wheel:
            me["joined"] = wheel["id"]
        return jsonify({"token": token, "me": me}), 201


@app.post("/api/auth/login")
def login():
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name", "")).strip()
    password = str(payload.get("password", ""))
    with _lock:
        db = load_db()
        user = next((u for u in db["users"].values() if u["name"].lower() == name.lower()), None)
        if not user or not hmac.compare_digest(user["password"], hash_password(password, user["salt"])):
            abort(401, description="wrong name or password")
        token = start_session(db, user)
        save_db(db)
        return jsonify({"token": token, "me": me_payload(db, user)})


@app.post("/api/auth/logout")
def logout():
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    with _lock:
        db = load_db()
        if db["sessions"].pop(token, None):
            save_db(db)
    return "", 204


@app.get("/api/me")
def me():
    with _lock:
        db = load_db()
        return jsonify(me_payload(db, current_user(db)))


@app.put("/api/me/prefs")
def update_prefs():
    payload = request.get_json(force=True, silent=True)
    with _lock:
        db = load_db()
        user = current_user(db)
        user["prefs"] = clean_prefs(db, user, payload)
        save_db(db)
        return jsonify(user["prefs"])


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


def push_to_users(users, title, body, wheel, tag=None):
    """Queue one notification to every device of `users`. Delivery runs
    on a background thread: a round-trip to Apple's or Google's relay
    can take seconds, and the member who spun is still waiting for
    their HTTP response. Notifications sharing a tag replace each other
    on the device; the default groups per wheel, so round events
    coalesce — pass a distinct tag for news that shouldn't overwrite an
    actionable alert (like a pick waiting for a thumbs-up)."""
    if webpush is None or vapid_public_key() is None:
        return  # the call also guarantees the key file exists before delivery reads it
    targets = [
        (u["id"], {"endpoint": s["endpoint"], "keys": dict(s["keys"])})
        for u in users for s in u.get("push_subs", [])
    ]
    if not targets:
        return
    payload = json.dumps({
        "title": title,
        "body": body,
        "tag": tag or wheel["id"],
        "url": f"/#{wheel['id']}",
    }, ensure_ascii=False)
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
            db = load_db()
            changed = False
            for user_id, endpoint in dead:
                user = db["users"].get(user_id)
                if user and any(s["endpoint"] == endpoint for s in user.get("push_subs", [])):
                    user["push_subs"] = [
                        s for s in user["push_subs"] if s["endpoint"] != endpoint
                    ]
                    changed = True
            if changed:
                save_db(db)


@app.get("/api/push/status")
def push_status():
    with _lock:
        db = load_db()
        current_user(db)
    return jsonify({"enabled": webpush is not None, "public_key": vapid_public_key()})


@app.post("/api/push/subscribe")
def push_subscribe():
    if webpush is None:
        abort(503, description="this server can't send notifications yet — "
                               "install pywebpush and restart (see the README)")
    sub = clean_subscription(request.get_json(force=True, silent=True))
    with _lock:
        db = load_db()
        store_subscription(db, current_user(db), sub)
        save_db(db)
    return jsonify({"subscribed": True})


@app.post("/api/push/unsubscribe")
def push_unsubscribe():
    payload = request.get_json(force=True, silent=True) or {}
    endpoint = str(payload.get("endpoint", ""))
    with _lock:
        db = load_db()
        user = current_user(db)
        before = user.get("push_subs", [])
        user["push_subs"] = [s for s in before if s["endpoint"] != endpoint]
        if len(user["push_subs"]) != len(before):
            save_db(db)
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
        db = load_db()
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
            save_db(db)
    return "", 204


# ── Calendar availability (secret ICS feeds) ─────────────────────────
# A personal aid for date polls: each member may link their own calendar
# by its secret iCal URL (Google/Outlook/iCloud/Nextcloud all offer one).
# We fetch it read-only and work out which *evenings* are busy — shown
# only to that member, never to the rest of the wheel. Feed URLs are
# secrets: stored per user, never returned in full by any endpoint.
#
# The cache holds each feed's *digested* busy-date set (not raw events),
# keyed by URL, guarded by its own lock that is NEVER nested inside the
# db lock — network fetches happen with no lock held, like push delivery.
_cal_lock = threading.Lock()
_cal_cache = {}  # url -> {"at": epoch, "busy": set[str] | None}


def to_local(dt):
    """A VEVENT datetime in LOCAL_TZ. Floating (naive) times are read as
    already-local — the pragmatic choice for a home calendar."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def evening_dates(start_dt, end_dt):
    """Local dates whose EVENING_FROM–midnight window the event [start,
    end) overlaps. Bounded to the poll horizon so a stray multi-day timed
    event can't blow up the loop."""
    days = set()
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=1)  # zero-length event → its start evening
    day = start_dt.date()
    last = min((end_dt - timedelta(seconds=1)).date(),
               day + timedelta(days=POLL_HORIZON_DAYS + 1))
    while day <= last:
        window_start = datetime.combine(day, datetime.min.time(),
                                        tzinfo=LOCAL_TZ).replace(hour=EVENING_FROM)
        window_end = datetime.combine(day, datetime.max.time(), tzinfo=LOCAL_TZ)
        if start_dt < window_end and end_dt > window_start:
            days.add(day.isoformat())
        day += timedelta(days=1)
    return days


def compute_busy(raw):
    """Busy evening dates (ISO strings) from raw ICS bytes, over today →
    horizon. All-day events are skipped on purpose — birthday and
    public-holiday calendars would otherwise mark whole weeks busy — as
    are CANCELLED and free/TRANSPARENT events."""
    cal = ICalendar.from_ical(raw)
    today = datetime.now(LOCAL_TZ).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=LOCAL_TZ)
    end = start + timedelta(days=POLL_HORIZON_DAYS + 1)
    busy = set()
    for event in recurring_ical_events.of(cal).between(start, end):
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        dtstart = event.get("DTSTART")
        if dtstart is None:
            continue
        begin = dtstart.dt
        if not isinstance(begin, datetime):
            continue  # date-typed DTSTART = all-day → skip
        dtend = event.get("DTEND")
        finish = dtend.dt if dtend is not None and isinstance(dtend.dt, datetime) else begin
        busy |= evening_dates(to_local(begin), to_local(finish))
    return busy


def cached_busy(url):
    """This feed's busy-date set, fetched-and-cached. Returns None only
    when the feed has never yielded readable data; a fetch failure with a
    prior good result keeps serving the stale set (better than blank for
    a home dinner poll)."""
    now = time.time()
    with _cal_lock:
        entry = _cal_cache.get(url)
        if entry and now - entry["at"] < ICS_TTL:
            return entry["busy"]
    busy = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wheel-of-choice"})
        with urllib.request.urlopen(req, timeout=ICS_TIMEOUT) as resp:  # noqa: S310 (scheme vetted)
            raw = resp.read(ICS_MAX_BYTES + 1)
        if len(raw) > ICS_MAX_BYTES:
            raise ValueError("calendar too large")
        busy = compute_busy(raw)
    except Exception as err:
        app.logger.info("calendar fetch failed for %s…: %s", urlparse(url).hostname, err)
    with _cal_lock:
        prev = _cal_cache.get(url)
        if busy is None and prev and prev.get("busy") is not None:
            busy = prev["busy"]  # stale beats blank
        _cal_cache[url] = {"at": now, "busy": busy}
    return busy


def busy_evenings_for(feeds):
    """(busy date-set, readable?) across a user's feeds. `readable` is
    False only when not one feed yielded data — callers then show
    'unknown' rather than a confident 'free'."""
    busy = set()
    readable = False
    for feed in feeds:
        result = cached_busy(feed["url"])
        if result is not None:
            busy |= result
            readable = True
    return busy, readable


def clean_feed_url(url):
    """Normalize + vet a pasted calendar URL. webcal→https; only https
    (plus http on localhost, so the feature is testable) is allowed. We
    keep SSRF defences proportionate for a single-household home server:
    scheme allow-list, size cap, timeout — no DNS-rebinding theatre."""
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not 0 < len(url) <= 500:
        abort(400, description="paste your calendar's secret iCal address")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" or (parsed.scheme == "http" and host in ("localhost", "127.0.0.1")):
        return url
    abort(400, description="that needs to be an https:// (or webcal://) calendar address")


def feed_summary(feed):
    """Safe-to-share view of a feed — never the secret URL itself."""
    return {"id": feed["id"], "label": feed["label"], "host": urlparse(feed["url"]).hostname or ""}


@app.get("/api/me/calendars")
def list_calendars():
    with _lock:
        db = load_db()
        user = current_user(db)
        feeds = [feed_summary(f) for f in user.get("ics_feeds", [])]
    return jsonify({"enabled": ICalendar is not None, "feeds": feeds})


@app.post("/api/me/calendars")
def add_calendar():
    if ICalendar is None:
        abort(503, description="this server can't read calendars yet — install icalendar "
                               "and recurring-ical-events and restart (see the README)")
    payload = request.get_json(force=True, silent=True) or {}
    url = clean_feed_url(str(payload.get("url", "")))
    label = str(payload.get("label", "")).strip()[:40] or urlparse(url).hostname or "Calendar"
    # test-fetch outside the db lock — proves the URL works and warms the
    # cache, and network I/O must never block other requests on _lock
    try:
        busy = cached_busy_fresh(url)
    except Exception as err:
        abort(400, description=f"couldn't read that calendar — {err}")
    with _lock:
        db = load_db()
        user = current_user(db)
        feeds = user.setdefault("ics_feeds", [])
        if any(f["url"] == url for f in feeds):
            abort(409, description="you've already linked that calendar")
        if len(feeds) >= MAX_ICS_FEEDS:
            abort(400, description=f"{MAX_ICS_FEEDS} calendars is the limit — remove one first")
        feed = {"id": "c-" + uuid.uuid4().hex[:8], "label": label, "url": url}
        feeds.append(feed)
        save_db(db)
    return jsonify({"feed": feed_summary(feed), "busy_days": len(busy)})


def cached_busy_fresh(url):
    """Fetch + parse now (bypassing the TTL), storing the result. Raises
    on failure so the add-calendar route can report why."""
    req = urllib.request.Request(url, headers={"User-Agent": "wheel-of-choice"})
    with urllib.request.urlopen(req, timeout=ICS_TIMEOUT) as resp:  # noqa: S310 (scheme vetted)
        raw = resp.read(ICS_MAX_BYTES + 1)
    if len(raw) > ICS_MAX_BYTES:
        raise ValueError("that calendar is too big")
    busy = compute_busy(raw)
    with _cal_lock:
        _cal_cache[url] = {"at": time.time(), "busy": busy}
    return busy


@app.delete("/api/me/calendars/<feed_id>")
def remove_calendar(feed_id):
    with _lock:
        db = load_db()
        user = current_user(db)
        feeds = user.get("ics_feeds", [])
        removed = next((f for f in feeds if f["id"] == feed_id), None)
        if removed:
            user["ics_feeds"] = [f for f in feeds if f["id"] != feed_id]
            save_db(db)
    if removed:
        with _cal_lock:
            _cal_cache.pop(removed["url"], None)
    return "", 204


@app.get("/api/me/availability")
def availability():
    """Busy/free per date for the requesting user's own calendars, over a
    window. Only ever the caller's own feeds — a poll's other members see
    nothing but the ticks people choose to place."""
    if ICalendar is None:
        return jsonify({"enabled": False, "linked": False, "days": {}})
    try:
        start = datetime.strptime(request.args.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        start = datetime.now(LOCAL_TZ).date()
    try:
        span = max(1, min(POLL_HORIZON_DAYS, int(request.args.get("days", 28))))
    except (TypeError, ValueError):
        span = 28
    with _lock:
        db = load_db()
        user = current_user(db)
        feeds = [dict(f) for f in user.get("ics_feeds", [])]  # copy, then drop the lock
    if not feeds:
        return jsonify({"enabled": True, "linked": False, "days": {}})
    busy, readable = busy_evenings_for(feeds)  # network I/O, no _lock held
    days = {}
    for i in range(span):
        iso = (start + timedelta(days=i)).isoformat()
        days[iso] = "busy" if iso in busy else ("free" if readable else "unknown")
    return jsonify({"enabled": True, "linked": True, "days": days})


# ── Wheel seeding (travel wheels only) ───────────────────────────────
def seed_destinations(wheel_type, home, roam, vibes, budget, favorites, user_id):
    """Build one travel wheel from its catalogue, tailored to the answers.

    - distance is recomputed relative to `home` (each catalogue entry
      lists the home regions it is "regional" for; long-haul stays put)
    - destinations beyond the chosen roam range are kept but disabled,
      so they stay discoverable in the manage panel
    - catalogue entries marked "enabled": false are niche picks that also
      start off the wheel (a full catalogue would drown it in segments) —
      *unless* they're regional from this home: a local gem like
      Maastricht belongs on a Benelux wheel but not on a Spanish one
    - entries matching the chosen vibes (and budget) get pre-starred
    - `favorites` (a list of catalogue ids) are places named during
      creation: starred *by this user* and enabled even beyond the roam
      range — they asked for it. Stars are per user (starred_by); a
      place starred by several members gets an even bigger segment.
    """
    allowed = ROAM_DISTANCES[roam]
    destinations = []
    for entry in catalog(wheel_type):
        if entry["distance"] == "longhaul":
            distance = "longhaul"
        elif home in entry.get("near", []):
            distance = "regional"
        else:
            distance = "europe"
        destinations.append({
            "id": entry["id"],
            "name": entry["name"],
            "flag": entry["flag"],
            "budget": entry["budget"],
            "distance": distance,
            "vibes": entry["vibes"],
            "seasons": entry["seasons"],
            "party": entry["party"],
            "favorite": False,
            "starred_by": [],
            "enabled": distance in allowed
            and (entry.get("enabled", True) or distance == "regional"),
            "notes": entry.get("notes", ""),
            "links": entry.get("links") or default_links(entry["name"]),
        })
    if vibes:
        scored = []
        for dest in destinations:
            if not dest["enabled"]:
                continue
            matches = len(set(dest["vibes"]) & set(vibes))
            score = matches * 2 + (1 if budget in ("mix", dest["budget"]) else 0)
            # at least two vibe matches, or one match plus a budget fit
            if matches and score >= 3:
                scored.append((score, dest))
        scored.sort(key=lambda pair: -pair[0])
        for _, dest in scored[:MAX_SEED_FAVORITES]:
            dest["favorite"] = True
    picked = set(favorites or [])
    for dest in destinations:
        if dest["id"] in picked:
            dest["favorite"] = True
            dest["enabled"] = True
            if user_id:
                dest["starred_by"] = [user_id]
    return destinations


@app.get("/api/catalog")
def catalog_overview():
    """Names of everything in the seed catalogues, per travel wheel type
    — lets the wheel-creation screen offer a favourites picker. Unknown
    ids sent back with the answers are simply ignored."""
    with _lock:
        db = load_db()
        current_user(db)
    return jsonify({
        wheel_type: [{"id": e["id"], "name": e["name"], "flag": e["flag"]}
                     for e in catalog(wheel_type)]
        for wheel_type in TRAVEL_TYPES
    })


# ── Wheels (create / join / leave / claim) ───────────────────────────
@app.post("/api/wheels")
def create_wheel():
    """Create a wheel of your own. Travel wheels are seeded from the
    catalogue using the onboarding answers; a restaurant wheel starts
    empty — every entry on it is added by hand."""
    payload = request.get_json(force=True, silent=True) or {}
    wheel_type = payload.get("type")
    if wheel_type not in WHEEL_TYPES:
        abort(400, description="unknown wheel type")
    name = str(payload.get("name", "")).strip()[:40]
    travel = WHEEL_TYPES[wheel_type]["travel"]
    if travel:
        home = payload.get("home")
        roam = payload.get("roam", "europe")
        budget = payload.get("budget", "mix")
        vibes = payload.get("vibes", [])
        favorites = payload.get("favorites", [])
        if home not in HOME_REGIONS:
            abort(400, description="tell us where home is first")
        if roam not in ROAM_DISTANCES:
            abort(400, description="unknown roam range")
        if budget not in BUDGET_STYLES:
            abort(400, description="unknown budget style")
        vibes = [v for v in vibes if v in VIBES] if isinstance(vibes, list) else []
        favorites = [f for f in favorites if isinstance(f, str)] if isinstance(favorites, list) else []
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel = new_wheel(wheel_type, name)
        if travel:
            wheel["destinations"] = seed_destinations(
                wheel_type, home, roam, vibes, budget, favorites, user["id"]
            )
        db["wheels"][wheel["id"]] = wheel
        user.setdefault("wheels", []).append(wheel["id"])
        save_db(db)
        me = me_payload(db, user)
        me["created"] = wheel["id"]
        return jsonify(me), 201


@app.post("/api/wheels/join")
def join_wheel():
    """Add someone else's wheel to your list via its share code. Your
    other wheels are untouched — sharing is strictly per wheel."""
    payload = request.get_json(force=True, silent=True) or {}
    code = str(payload.get("code", "")).strip().upper().replace(" ", "")
    if not code:
        abort(400, description="enter a share code")
    with _lock:
        db = load_db()
        user = current_user(db)
        target = next((w for w in db["wheels"].values() if w["code"] == code), None)
        if target is None:
            abort(404, description="no wheel found for that code — double-check it with whoever shared it")
        if target["id"] in user.get("wheels", []):
            abort(400, description="that wheel is already in your list")
        user.setdefault("wheels", []).append(target["id"])
        save_db(db)
        me = me_payload(db, user)
        me["joined"] = target["id"]
        return jsonify(me)


@app.post("/api/wheels/claim")
def claim_wheels():
    """Adopt the wheels migrated from a pre-account database."""
    with _lock:
        db = load_db()
        user = current_user(db)
        ids = unclaimed_wheel_ids(db)
        if not ids:
            abort(404, description="no unclaimed wheels found — they may already be claimed")
        user.setdefault("wheels", []).extend(ids)
        save_db(db)
        return jsonify(me_payload(db, user))


@app.post("/api/wheels/<wheel_id>/leave")
def leave_wheel(wheel_id):
    """Take a wheel off your list. Other members keep spinning it; a
    wheel nobody is left on is deleted for good."""
    with _lock:
        db = load_db()
        user = current_user(db)
        if wheel_id not in user.get("wheels", []):
            abort(404, description="that wheel isn't in your list")
        user["wheels"].remove(wheel_id)
        user.get("prefs", {}).pop(wheel_id, None)
        drop_wheel_if_empty(db, wheel_id)
        save_db(db)
        return jsonify(me_payload(db, user))


# ── Admin ────────────────────────────────────────────────────────────
def require_admin(db):
    user = current_user(db)
    if not user.get("admin"):
        abort(403, description="admins only")
    return user


def admin_user_list(db):
    def sharing_with(user):
        others = set()
        for wid in user.get("wheels", []):
            for member in wheel_member_users(db, wid):
                if member["id"] != user["id"]:
                    others.add(member["name"])
        return sorted(others)

    users = sorted(db["users"].values(), key=lambda u: u.get("created", ""))
    return [{
        "id": u["id"],
        "name": u["name"],
        "admin": bool(u.get("admin")),
        "created": u.get("created"),
        "wheel_count": len(u.get("wheels", [])),
        "sharing_with": sharing_with(u),
    } for u in users]


@app.get("/api/admin/users")
def admin_users():
    with _lock:
        db = load_db()
        require_admin(db)
        return jsonify(admin_user_list(db))


@app.put("/api/admin/users/<user_id>")
def admin_set_admin(user_id):
    payload = request.get_json(force=True, silent=True) or {}
    make_admin = bool(payload.get("admin"))
    with _lock:
        db = load_db()
        admin = require_admin(db)
        target = db["users"].get(user_id)
        if target is None:
            abort(404, description="no such user")
        if target["id"] == admin["id"] and not make_admin:
            abort(400, description="you can't take away your own admin rights")
        target["admin"] = make_admin
        save_db(db)
        return jsonify(admin_user_list(db))


@app.post("/api/admin/users/<user_id>/unshare")
def admin_unshare(user_id):
    """Pull a user out of every wheel they share with someone else. The
    other members keep those wheels; the user keeps any solo wheels."""
    with _lock:
        db = load_db()
        require_admin(db)
        target = db["users"].get(user_id)
        if target is None:
            abort(404, description="no such user")
        shared = [wid for wid in target.get("wheels", [])
                  if len(wheel_member_users(db, wid)) > 1]
        if not shared:
            abort(400, description=f"{target['name']} isn't sharing any wheels")
        target["wheels"] = [wid for wid in target["wheels"] if wid not in shared]
        for wid in shared:
            target.get("prefs", {}).pop(wid, None)
        save_db(db)
        return jsonify(admin_user_list(db))


@app.delete("/api/admin/users/<user_id>")
def admin_delete_user(user_id):
    with _lock:
        db = load_db()
        admin = require_admin(db)
        if user_id == admin["id"]:
            abort(400, description="you can't delete your own account")
        target = db["users"].pop(user_id, None)
        if target is None:
            abort(404, description="no such user")
        db["sessions"] = {t: s for t, s in db["sessions"].items() if s["user"] != user_id}
        for wid in target.get("wheels", []):
            drop_wheel_if_empty(db, wid)
        save_db(db)
        return jsonify(admin_user_list(db))


@app.get("/api/admin/backup")
def admin_backup():
    """The whole database as a downloadable file — cheap insurance for a
    self-hosted app whose world lives in one db.json."""
    with _lock:
        db = load_db()
        require_admin(db)
        payload = json.dumps(db, ensure_ascii=False, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    response = app.response_class(payload, mimetype="application/json")
    response.headers["Content-Disposition"] = (
        f"attachment; filename=wheel-of-choice-backup-{stamp}.json"
    )
    return response


@app.post("/api/admin/restore")
def admin_restore():
    """Replace the database with an uploaded backup. Backups from the
    spaces era (v2) are accepted too — they're migrated on the next
    load. The current admin's login is carried over when their account
    exists in the backup; otherwise everyone (including them) has to
    log in again."""
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), dict) for key in ("users", "sessions")
    ) or not any(isinstance(payload.get(key), dict) for key in ("wheels", "spaces")):
        abort(400, description="that doesn't look like a Wheel of Choice backup")
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    with _lock:
        db = load_db()
        admin = require_admin(db)
        payload.setdefault("version", 2 if "spaces" in payload else 3)
        relogin = admin["id"] not in payload["users"]
        if not relogin:
            payload["sessions"][token] = db["sessions"][token]
        save_db(payload)
    return jsonify({"restored": True, "relogin": relogin})


@app.post("/api/admin/update")
def admin_update():
    """Ask the host to update to the latest version. The Flask process is
    sandboxed and unprivileged (see deploy/wheel-of-choice.service), so it
    can't run `sudo git pull` itself — it drops a flag file in the state
    directory instead, and the root-level wheel-of-choice-update.path unit
    (see deploy/) picks it up, pulls, and restarts this service."""
    with _lock:
        db = load_db()
        require_admin(db)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            UPDATE_FLAG.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        except OSError:
            abort(500, description="could not write the update request")
    return jsonify({"requested": True}), 202


@app.get("/api/version")
def version():
    """What's running right now. The frontend shows the commit + date in
    the UI and polls this after an update request: server_started changes
    once the updater has restarted us, and the commit moves when the pull
    actually brought something new. update_pending reports whether the
    flag file is still waiting to be picked up."""
    return jsonify({
        **GIT_VERSION,
        "server_started": SERVER_STARTED,
        "update_pending": UPDATE_FLAG.exists(),
    })


# ── Validation ───────────────────────────────────────────────────────
def clean_links(values, fallback):
    """User-supplied reading links; anything that isn't http(s) is dropped."""
    if not isinstance(values, list):
        return fallback
    links = []
    for item in values[:12]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()[:300]
        if not url.startswith(("http://", "https://")):
            continue
        label = str(item.get("label", "")).strip()[:60]
        links.append({"label": label or url, "url": url})
    return links


def clean_destination(payload, existing=None, travel=True):
    """Normalise and validate a destination payload; raises ValueError.
    Restaurant entries skip the travel-only fields — they're just a
    name, an emoji, notes and links."""
    base = existing or {}
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")

    name = str(payload.get("name", base.get("name", ""))).strip()[:60]
    if not name:
        raise ValueError("name is required")

    default_flag = "📍" if travel else "🍽️"
    dest = {
        "id": base.get("id") or "d-" + uuid.uuid4().hex[:10],
        "name": name,
        "flag": (str(payload.get("flag", base.get("flag", default_flag))).strip() or default_flag)[:28],
        "favorite": bool(payload.get("favorite", base.get("favorite", False))),
        # who starred it is only changed via the "starred" toggle in the
        # update endpoint, never straight from a payload
        "starred_by": [s for s in base.get("starred_by", []) if isinstance(s, str)],
        "enabled": bool(payload.get("enabled", base.get("enabled", True))),
        "notes": str(payload.get("notes", base.get("notes", ""))).strip()[:1000],
        "links": clean_links(payload.get("links", base.get("links", [])), base.get("links", [])),
    }
    if not travel:
        return dest

    def subset(key, allowed, fallback):
        values = payload.get(key, base.get(key, fallback))
        if not isinstance(values, list):
            return fallback
        values = [v for v in values if v in allowed]
        return values or fallback

    budget = payload.get("budget", base.get("budget", "mid"))
    distance = payload.get("distance", base.get("distance", "europe"))
    dest.update({
        "budget": budget if budget in BUDGETS else "mid",
        "distance": distance if distance in DISTANCES else "europe",
        "vibes": subset("vibes", VIBES, ["nature"]),
        "seasons": subset("seasons", SEASONS, sorted(SEASONS)),
        "party": subset("party", PARTIES, sorted(PARTIES)),
    })
    return dest


def get_wheel(db, user, wheel_id):
    wheel = db["wheels"].get(wheel_id)
    if wheel is None or wheel_id not in user.get("wheels", []):
        abort(404, description="that wheel isn't in your list")
    # History entries predating trip statuses have no id — backfill (and
    # persist) so the status endpoint can address them.
    changed = False
    for entry in wheel["history"]:
        if not entry.get("id"):
            entry["id"] = "h-" + uuid.uuid4().hex[:10]
            changed = True
    if changed:
        save_db(db)
    return wheel


# ── Rounds (shared vetoes + pending pick, travel wheels only) ────────
def round_data(wheel):
    """The wheel's current round; older databases don't have one yet."""
    rnd = wheel.setdefault("round", empty_round())
    rnd.setdefault("vetoes", {})
    rnd.setdefault("pending", None)
    return rnd


def pending_blockers(db, wheel, rnd):
    """Members who can still stop the pending pick: everyone on the
    wheel except the picker who has neither confirmed it nor spent
    their veto. With more than two members the pick stays pending until
    this list is empty — every voice gets heard, not just the first to
    answer."""
    p = rnd["pending"]
    confirmed = set(p.get("confirmed", []))
    return [
        u for u in wheel_member_users(db, wheel["id"])
        if u["id"] != p["by"] and u["id"] not in confirmed and u["id"] not in rnd["vetoes"]
    ]


def round_payload(db, user, wheel):
    """Round state as one user sees it — 'my' fields are personalised.
    Restaurant wheels have no vetoes, so their round is always blank."""
    rnd = round_data(wheel)
    pending = None
    if rnd["pending"]:
        p = rnd["pending"]
        pending = {k: v for k, v in p.items() if k not in ("by", "confirmed")}
        pending["mine"] = p["by"] == user["id"]
        pending["i_confirmed"] = user["id"] in p.get("confirmed", [])
        pending["waiting_names"] = sorted(
            u["name"] for u in pending_blockers(db, wheel, rnd)
        )
    return {
        "vetoed_ids": sorted(set(rnd["vetoes"].values())),
        "my_veto_used": user["id"] in rnd["vetoes"],
        "vetoes_used": len(rnd["vetoes"]),
        "members": len(wheel_member_users(db, wheel["id"])),
        "pending": pending,
    }


def finalize_pick(wheel, dest_id, name, flag, by_name):
    entry = {
        "id": "h-" + uuid.uuid4().hex[:10],
        "dest_id": dest_id,  # lets the frontend link back to the destination's info
        "name": name,
        "flag": flag,
        "date": datetime.now(timezone.utc).isoformat(),
        "by": by_name,
    }
    wheel["history"] = ([entry] + wheel["history"])[:HISTORY_LIMIT]
    wheel["round"] = empty_round()  # a decision closes the round


@app.get("/api/wheels/<wheel_id>/round")
def get_round(wheel_id):
    with _lock:
        db = load_db()
        user = current_user(db)
        return jsonify(round_payload(db, user, get_wheel(db, user, wheel_id)))


@app.post("/api/wheels/<wheel_id>/round/veto")
def veto_destination(wheel_id):
    payload = request.get_json(force=True, silent=True) or {}
    dest_id = str(payload.get("dest_id", ""))
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel = get_wheel(db, user, wheel_id)
        if not is_travel(wheel):
            abort(400, description="this wheel has no vetoes — the wheel's word is final")
        rnd = round_data(wheel)
        if user["id"] in rnd["vetoes"]:
            abort(409, description="you've already used your veto this round")
        if not any(d["id"] == dest_id for d in wheel["destinations"]):
            abort(404, description="destination not found")
        rnd["vetoes"][user["id"]] = dest_id
        shot_down = rnd["pending"] if rnd["pending"] and rnd["pending"]["dest_id"] == dest_id else None
        if shot_down:
            rnd["pending"] = None  # the veto shoots down the waiting pick
        save_db(db)
        if shot_down:
            push_to_users(
                [u for u in wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
                f"🙅 {user['name']} vetoed {shot_down['flag']} {shot_down['name']}",
                f"Back to square one on {wheel['name']} — give it another spin!",
                wheel,
            )
        return jsonify(round_payload(db, user, wheel))


@app.post("/api/wheels/<wheel_id>/round/pick")
def propose_pick(wheel_id):
    """The spinner accepted a result. On travel wheels it only becomes
    history once every member who could still veto has had the chance —
    until then it is the round's pending pick. Restaurant wheels have
    no vetoes, so the pick lands in the history straight away."""
    payload = request.get_json(force=True, silent=True) or {}
    dest_id = str(payload.get("dest_id", ""))
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel = get_wheel(db, user, wheel_id)
        rnd = round_data(wheel)
        dest = next((d for d in wheel["destinations"] if d["id"] == dest_id), None)
        if dest is None:
            abort(404, description="destination not found")
        if is_travel(wheel):
            if dest_id in rnd["vetoes"].values():
                abort(409, description="that destination was vetoed this round")
            can_still_veto = any(
                u["id"] != user["id"] and u["id"] not in rnd["vetoes"]
                for u in wheel_member_users(db, wheel["id"])
            )
            if can_still_veto:
                rnd["pending"] = {
                    "dest_id": dest["id"],
                    "name": dest["name"],
                    "flag": dest["flag"],
                    "by": user["id"],
                    "by_name": user["name"],
                    "confirmed": [],
                    "date": datetime.now(timezone.utc).isoformat(),
                }
                save_db(db)
                push_to_users(
                    pending_blockers(db, wheel, rnd),
                    f"🎡 {user['name']} spun {dest['flag']} {dest['name']}",
                    f"{wheel['name']}: are you in — or is this your veto?",
                    wheel,
                )
                return jsonify({"final": False, "round": round_payload(db, user, wheel)})
        finalize_pick(wheel, dest["id"], dest["name"], dest["flag"], user["name"])
        save_db(db)
        push_to_users(
            [u for u in wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            f"🎉 {dest['flag']} {dest['name']} it is!",
            f"{user['name']} spun {wheel['name']} — time to book! 🧳" if is_travel(wheel)
            else f"{user['name']} spun {wheel['name']} — enjoy your meal! 🍽️",
            wheel,
        )
        return jsonify({
            "final": True,
            "history": history_view(db, wheel, user),
            "round": round_payload(db, user, wheel),
        })


@app.post("/api/wheels/<wheel_id>/round/confirm")
def confirm_pick(wheel_id):
    """A member okays the pending pick. With three or four people on the
    wheel, the pick only becomes history once *everyone* who could
    still veto has given their thumbs-up."""
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel = get_wheel(db, user, wheel_id)
        rnd = round_data(wheel)
        pending = rnd["pending"]
        if not pending:
            abort(404, description="no pick is waiting for a thumbs-up")
        if pending["by"] == user["id"]:
            abort(400, description="the others have to confirm this one")
        confirmed = pending.setdefault("confirmed", [])
        if user["id"] not in confirmed:
            confirmed.append(user["id"])
        blockers = pending_blockers(db, wheel, rnd)
        if blockers:
            save_db(db)
            # only the spinner needs the play-by-play — the others get
            # pinged once the pick is final
            spinner = db["users"].get(pending["by"])
            push_to_users(
                [spinner] if spinner else [],
                f"👍 {user['name']} is in for {pending['flag']} {pending['name']}",
                f"Still waiting for {' & '.join(sorted(u['name'] for u in blockers))} · {wheel['name']}",
                wheel,
            )
            return jsonify({"final": False, "round": round_payload(db, user, wheel)})
        finalize_pick(wheel, pending["dest_id"], pending["name"], pending["flag"], pending["by_name"])
        save_db(db)
        push_to_users(
            [u for u in wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            f"🎉 {pending['flag']} {pending['name']} it is!",
            f"Everyone's in on {wheel['name']} — time to book! 🧳",
            wheel,
        )
        return jsonify({
            "final": True,
            "history": history_view(db, wheel, user),
            "round": round_payload(db, user, wheel),
        })


# ── Destinations API ─────────────────────────────────────────────────
@app.get("/api/wheels/<wheel_id>/destinations")
def list_destinations(wheel_id):
    with _lock:
        db = load_db()
        return jsonify(get_wheel(db, current_user(db), wheel_id)["destinations"])


@app.post("/api/wheels/<wheel_id>/destinations")
def create_destination(wheel_id):
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel = get_wheel(db, user, wheel_id)
        try:
            dest = clean_destination(
                request.get_json(force=True, silent=True), travel=is_travel(wheel)
            )
        except ValueError as err:
            abort(400, description=str(err))
        wheel["destinations"].append(dest)
        save_db(db)
        label = " ".join(part for part in (dest.get("flag", ""), dest["name"]) if part)
        push_to_users(
            [u for u in wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            f"➕ {user['name']} added {label}",
            f"Fresh on {wheel['name']} — worth a star? ⭐",
            wheel,
            tag=f"{wheel['id']}-add",  # don't overwrite a spin waiting for a thumbs-up
        )
    return jsonify(dest), 201


@app.put("/api/wheels/<wheel_id>/destinations/<dest_id>")
def update_destination(wheel_id, dest_id):
    payload = request.get_json(force=True, silent=True)
    # "starred" toggles *this user's* star — handled here because
    # clean_destination doesn't know who is asking
    starred = payload.pop("starred", None) if isinstance(payload, dict) else None
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel = get_wheel(db, user, wheel_id)
        destinations = wheel["destinations"]
        for i, dest in enumerate(destinations):
            if dest["id"] == dest_id:
                try:
                    updated = clean_destination(payload, existing=dest, travel=is_travel(wheel))
                except ValueError as err:
                    abort(400, description=str(err))
                if starred is not None:
                    stars = set(updated["starred_by"])
                    stars.add(user["id"]) if starred else stars.discard(user["id"])
                    updated["starred_by"] = sorted(stars)
                    # touching the star replaces any legacy shared star
                    updated["favorite"] = bool(stars)
                destinations[i] = updated
                save_db(db)
                return jsonify(updated)
    abort(404, description="destination not found")


@app.delete("/api/wheels/<wheel_id>/destinations/<dest_id>")
def delete_destination(wheel_id, dest_id):
    with _lock:
        db = load_db()
        wheel = get_wheel(db, current_user(db), wheel_id)
        before = len(wheel["destinations"])
        wheel["destinations"] = [d for d in wheel["destinations"] if d["id"] != dest_id]
        if len(wheel["destinations"]) == before:
            abort(404, description="destination not found")
        save_db(db)
    return "", 204


# ── History API ──────────────────────────────────────────────────────
@app.get("/api/wheels/<wheel_id>/history")
def list_history(wheel_id):
    with _lock:
        db = load_db()
        user = current_user(db)
        return jsonify(history_view(db, get_wheel(db, user, wheel_id), user))


@app.put("/api/wheels/<wheel_id>/history/<entry_id>")
def update_history(wheel_id, entry_id):
    """Life after the spin: mark a past pick as booked or been-there and
    note the trip date. Marking it visited also takes the destination off
    the wheel (it stays in the manage list, just unticked)."""
    payload = request.get_json(force=True, silent=True) or {}
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel = get_wheel(db, user, wheel_id)
        entry = next((e for e in wheel["history"] if e.get("id") == entry_id), None)
        if entry is None:
            abort(404, description="that history entry no longer exists")
        if "status" in payload:
            status = str(payload["status"] or "")
            if status and status not in HISTORY_STATUSES:
                abort(400, description="unknown trip status")
            if status:
                entry["status"] = status
            else:
                entry.pop("status", None)
        if "trip_date" in payload:
            trip_date = str(payload["trip_date"] or "").strip()[:20]
            if trip_date:
                entry["trip_date"] = trip_date
            else:
                entry.pop("trip_date", None)
        destination = None
        if entry.get("status") == "visited":
            destination = next(
                (d for d in wheel["destinations"] if d["id"] == entry.get("dest_id")), None
            )
            if destination is not None and destination["enabled"]:
                destination["enabled"] = False
        save_db(db)
        return jsonify({"history": history_view(db, wheel, user), "destination": destination})


@app.delete("/api/wheels/<wheel_id>/history")
def clear_history(wheel_id):
    with _lock:
        db = load_db()
        wheel = get_wheel(db, current_user(db), wheel_id)
        wheel["history"] = []
        wheel["round"] = empty_round()  # clean slate all round
        save_db(db)
    return "", 204


# ── Date polls (restaurant wheels) ───────────────────────────────────
# After a restaurant pick lands in the history, any member can open a
# Doodle-style poll on it: the proposer puts up a handful of evenings,
# everyone ticks what works, and a date everyone ticked can be locked
# in. The poll lives *on* the history entry, so it rides the existing
# 5-second history poll, dies when the entry is deleted, and clears with
# the history — no separate lifecycle to keep in sync.
def pretty_date(iso):
    """'2026-07-24' → 'Friday 24 Jul' (falls back to the raw string)."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%A %-d %b")
    except (ValueError, TypeError):
        return str(iso)


def clean_poll_dates(values):
    """Validate a proposer's candidate dates: ISO YYYY-MM-DD, no past,
    within the horizon, deduped and sorted, 2–POLL_MAX_DATES of them."""
    if not isinstance(values, list):
        abort(400, description="pick a few evenings first")
    today = datetime.now(LOCAL_TZ).date()
    horizon = today + timedelta(days=POLL_HORIZON_DAYS)
    dates = set()
    for value in values:
        try:
            day = datetime.strptime(str(value), "%Y-%m-%d").date()
        except ValueError:
            abort(400, description="that's not a date I understand")
        if day < today:
            abort(400, description="those evenings are in the past — pick dates still to come")
        if day > horizon:
            abort(400, description=f"keep it within the next {POLL_HORIZON_DAYS} days")
        dates.add(day.isoformat())
    dates = sorted(dates)
    if len(dates) < 2:
        abort(400, description="put up at least two evenings to choose between")
    if len(dates) > POLL_MAX_DATES:
        abort(400, description=f"that's a lot of evenings — {POLL_MAX_DATES} at most")
    return dates


def restaurant_poll_entry(db, user, wheel_id, entry_id):
    """The (wheel, history entry) a poll route targets. Polls are a
    restaurant thing; travel picks track a trip date via trip status."""
    wheel = get_wheel(db, user, wheel_id)
    if is_travel(wheel):
        abort(400, description="date polls are a restaurant thing — travel picks use trip status")
    entry = next((e for e in wheel["history"] if e.get("id") == entry_id), None)
    if entry is None:
        abort(404, description="that pick is no longer in the history")
    return wheel, entry


def poll_unanimous(members, poll):
    """Dates every *current* member ticked — recomputed live so members
    joining or leaving mid-poll self-heal (a newcomer must vote before
    anything is unanimous; a leaver's votes simply stop counting)."""
    votes = poll.get("votes", {})
    return [d for d in poll["dates"]
            if members and all(d in votes.get(u["id"], ()) for u in members)]


def poll_view(db, wheel, entry, user):
    """One member's view of a poll: their own vote, everyone's ticks by
    name, which dates are unanimous, and who's still to vote. Names are
    resolved here (never stored in votes), so renames and departures need
    no rewrite of stored data."""
    poll = entry.get("poll")
    if not poll:
        return None
    members = wheel_member_users(db, wheel["id"])
    names = {u["id"]: u["name"] for u in members}
    votes = poll.get("votes", {})
    return {
        "id": poll["id"],
        "status": poll["status"],
        "dates": poll["dates"],
        "by_name": db["users"].get(poll["by"], {}).get("name", "someone"),
        "locked_date": poll.get("locked_date"),
        "locked_by_name": (db["users"].get(poll["locked_by"], {}).get("name")
                           if poll.get("locked_by") else None),
        "my_dates": votes.get(user["id"]),  # None → this member hasn't voted yet
        "votes": sorted(
            ({"name": names[uid], "dates": ds, "mine": uid == user["id"]}
             for uid, ds in votes.items() if uid in names),
            key=lambda v: v["name"].lower(),
        ),
        "unanimous": poll_unanimous(members, poll),
        "waiting_names": sorted(names[u["id"]] for u in members if u["id"] not in votes),
        "members": len(members),
    }


def history_view(db, wheel, user):
    """History with each entry's poll serialized for this viewer. Every
    endpoint that hands back history routes through here, so the client
    always sees one shape."""
    return [
        {**entry, "poll": poll_view(db, wheel, entry, user)} if entry.get("poll") else entry
        for entry in wheel["history"]
    ]


@app.post("/api/wheels/<wheel_id>/history/<entry_id>/poll")
def start_poll(wheel_id, entry_id):
    dates = clean_poll_dates((request.get_json(force=True, silent=True) or {}).get("dates"))
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel, entry = restaurant_poll_entry(db, user, wheel_id, entry_id)
        if entry.get("poll"):
            abort(409, description="there's already a date poll on this pick — "
                                   "vote there, or scrap it first")
        entry["poll"] = {
            "id": "p-" + uuid.uuid4().hex[:8],
            "status": "open",
            "by": user["id"],
            "created": datetime.now(timezone.utc).isoformat(),
            "dates": dates,
            # the proposer ticks all their own dates — they'd hardly put up
            # evenings they can't do; they can untick afterwards
            "votes": {user["id"]: list(dates)},
            "locked_date": None,
            "locked_by": None,
        }
        save_db(db)
        label = " ".join(p for p in (entry.get("flag", ""), entry["name"]) if p)
        push_to_users(
            [u for u in wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            f"📅 {user['name']} started a date poll for {label}",
            f"{wheel['name']}: tick the evenings you can make!",
            wheel,
            tag=f"{wheel['id']}-poll-{entry_id}",
        )
        return jsonify({"history": history_view(db, wheel, user)})


@app.put("/api/wheels/<wheel_id>/history/<entry_id>/poll/votes")
def vote_poll(wheel_id, entry_id):
    picked = (request.get_json(force=True, silent=True) or {}).get("dates")
    if not isinstance(picked, list):
        abort(400, description="send the evenings you can make")
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel, entry = restaurant_poll_entry(db, user, wheel_id, entry_id)
        poll = entry.get("poll")
        if not poll:
            abort(404, description="that date poll is gone")
        if poll["status"] == "locked":
            abort(409, description="the date's already locked in")
        had_voted = user["id"] in poll["votes"]  # a re-vote must not re-ping
        picked = set(map(str, picked))
        poll["votes"][user["id"]] = [d for d in poll["dates"] if d in picked]
        members = wheel_member_users(db, wheel["id"])
        member_ids = {u["id"] for u in members}
        save_db(db)
        # ping the proposer only when the *last* remaining member first votes
        if not had_voted and member_ids <= set(poll["votes"]):
            spinner = db["users"].get(poll["by"])
            if spinner and spinner["id"] != user["id"]:
                unanimous = poll_unanimous(members, poll)
                body = (f"{pretty_date(unanimous[0])} works for all of you — lock it in! 🔒"
                        if unanimous else
                        "…but no evening works for everyone yet. Add more dates?")
                push_to_users([spinner], f"🗳️ Everyone's voted on {entry['name']}",
                              body, wheel, tag=f"{wheel['id']}-poll-{entry_id}")
        return jsonify({"history": history_view(db, wheel, user)})


@app.post("/api/wheels/<wheel_id>/history/<entry_id>/poll/lock")
def lock_poll(wheel_id, entry_id):
    date_str = str((request.get_json(force=True, silent=True) or {}).get("date", ""))
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel, entry = restaurant_poll_entry(db, user, wheel_id, entry_id)
        poll = entry.get("poll")
        if not poll:
            abort(404, description="that date poll is gone")
        if poll["status"] == "locked":
            abort(409, description=f"too late — {pretty_date(poll['locked_date'])} "
                                   "is already locked in")
        if date_str not in poll["dates"]:
            abort(400, description="that date isn't one of the options")
        # revalidate against current members: a fresh join or changed vote
        # since the button rendered must not be steamrolled
        members = wheel_member_users(db, wheel["id"])
        if date_str not in poll_unanimous(members, poll):
            abort(409, description="not everyone's free that evening (yet)")
        poll["status"] = "locked"
        poll["locked_date"] = date_str
        poll["locked_by"] = user["id"]
        entry["trip_date"] = date_str  # reuse the existing field
        save_db(db)
        push_to_users(
            [u for u in wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            f"🗓️ {pretty_date(date_str)} it is — dinner at {entry['name']}!",
            f"{user['name']} locked it in on {wheel['name']} — pop it in your calendar 📅",
            wheel,
            tag=f"{wheel['id']}-poll-{entry_id}",
        )
        return jsonify({"history": history_view(db, wheel, user)})


@app.delete("/api/wheels/<wheel_id>/history/<entry_id>/poll")
def delete_poll(wheel_id, entry_id):
    """Scrap the poll — also the 'unlock and pick a new date' path. Any
    member may, like clearing the history (household trust)."""
    with _lock:
        db = load_db()
        user = current_user(db)
        wheel, entry = restaurant_poll_entry(db, user, wheel_id, entry_id)
        if entry.pop("poll", None) is not None:
            entry.pop("trip_date", None)  # a scrapped lock frees the date too
            save_db(db)
        return jsonify({"history": history_view(db, wheel, user)})


# ── Static site ──────────────────────────────────────────────────────
@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/<path:filename>")
def assets(filename):
    # send_from_directory refuses paths that escape ROOT
    return send_from_directory(ROOT, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
