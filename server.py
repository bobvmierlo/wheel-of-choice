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
directory with the HOLIDAY_DATA_DIR environment variable (the systemd
unit sets it to /var/lib/holiday-picker). Old databases are migrated
automatically: v2 *spaces* are split into independent wheels (the old
space code keeps working — it now joins the holidays wheel), and the
even older single-household layout becomes unclaimed wheels that the
first registered account can adopt.

Run directly for a quick start:

    python3 server.py            # serves on http://0.0.0.0:8000
"""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.environ.get("HOLIDAY_DATA_DIR", str(ROOT / "data")))
DB_FILE = DATA_DIR / "db.json"
UPDATE_FLAG = DATA_DIR / "update-requested"

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
    sandboxed and unprivileged (see deploy/holiday-picker.service), so it
    can't run `sudo git pull` itself — it drops a flag file in the state
    directory instead, and the root-level holiday-picker-update.path unit
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
        if rnd["pending"] and rnd["pending"]["dest_id"] == dest_id:
            rnd["pending"] = None  # the veto shoots down the waiting pick
        save_db(db)
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
                return jsonify({"final": False, "round": round_payload(db, user, wheel)})
        finalize_pick(wheel, dest["id"], dest["name"], dest["flag"], user["name"])
        save_db(db)
        return jsonify({
            "final": True,
            "history": wheel["history"],
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
        if pending_blockers(db, wheel, rnd):
            save_db(db)
            return jsonify({"final": False, "round": round_payload(db, user, wheel)})
        finalize_pick(wheel, pending["dest_id"], pending["name"], pending["flag"], pending["by_name"])
        save_db(db)
        return jsonify({
            "final": True,
            "history": wheel["history"],
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
        wheel = get_wheel(db, current_user(db), wheel_id)
        try:
            dest = clean_destination(
                request.get_json(force=True, silent=True), travel=is_travel(wheel)
            )
        except ValueError as err:
            abort(400, description=str(err))
        wheel["destinations"].append(dest)
        save_db(db)
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
        return jsonify(get_wheel(db, current_user(db), wheel_id)["history"])


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
        return jsonify({"history": wheel["history"], "destination": destination})


@app.delete("/api/wheels/<wheel_id>/history")
def clear_history(wheel_id):
    with _lock:
        db = load_db()
        wheel = get_wheel(db, current_user(db), wheel_id)
        wheel["history"] = []
        wheel["round"] = empty_round()  # clean slate all round
        save_db(db)
    return "", 204


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
