#!/usr/bin/env python3
"""Flask server for Wheel of Wander.

Serves the static site plus a small JSON API. Since v2 the app has
accounts: every user belongs to a *space* — a shared set of wheels
(destinations + spin history). Couples share one space: one partner
registers and answers the onboarding questions (which seeds the wheels),
the other joins with the space's share code. Personal filter preferences
are stored per user, so partners can each keep their own filters.

There are two wheels per space, each with its own list and history:
  - "holidays"  : whole-country holiday destinations
  - "citytrips" : city trips in and around Europe

Storage: data/db.json next to this file by default; override the
directory with the HOLIDAY_DATA_DIR environment variable (the systemd
unit sets it to /var/lib/holiday-picker). Old single-household databases
are migrated automatically: the existing wheels become an unclaimed
space that the first registered account adopts.

Run directly for a quick start:

    python3 server.py            # serves on http://0.0.0.0:8000
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.environ.get("HOLIDAY_DATA_DIR", str(ROOT / "data")))
DB_FILE = DATA_DIR / "db.json"

WHEEL_SEEDS = {
    "holidays": ROOT / "seed-destinations.json",
    "citytrips": ROOT / "seed-citytrips.json",
}

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

app = Flask(__name__)
_lock = threading.Lock()


@app.errorhandler(HTTPException)
def as_json(err):
    """The frontend shows API error messages to the user — send JSON."""
    return jsonify(error=err.description), err.code


# ── Storage ──────────────────────────────────────────────────────────
def catalog(wheel):
    return json.loads(WHEEL_SEEDS[wheel].read_text(encoding="utf-8"))


def default_wheel(wheel):
    """Full catalogue as shipped — used when migrating pre-account data."""
    dests = [{k: v for k, v in entry.items() if k != "near"} for entry in catalog(wheel)]
    return {"destinations": dests, "history": []}


def empty_wheels():
    return {wheel: {"destinations": [], "history": []} for wheel in WHEEL_SEEDS}


def new_invite_code():
    # No 0/O/1/I — the code gets read out loud across the sofa.
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)
    )


def new_space(onboarded=False, wheels=None):
    sid = "s-" + uuid.uuid4().hex[:10]
    return {
        "id": sid,
        "code": new_invite_code(),
        "onboarded": onboarded,
        "wheels": wheels if wheels is not None else empty_wheels(),
    }


def load_db():
    if not DB_FILE.exists():
        return {"version": 2, "users": {}, "sessions": {}, "spaces": {}}
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))
    if "users" in db:
        return db
    # Migrate v1 (single shared household, no accounts). The old wheels
    # become an unclaimed space that the first registered account adopts.
    wheels = db.get("wheels")
    if wheels is None and "destinations" in db:  # even older flat layout
        wheels = {"holidays": {
            "destinations": db.get("destinations", []),
            "history": db.get("history", []),
        }}
    db = {"version": 2, "users": {}, "sessions": {}, "spaces": {}}
    if wheels:
        for wheel in WHEEL_SEEDS:
            wheels.setdefault(wheel, default_wheel(wheel))
        space = new_space(onboarded=True, wheels=wheels)
        db["spaces"][space["id"]] = space
        save_db(db)
    return db


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


def default_prefs():
    return {
        wheel: {"budget": [], "distance": [], "vibe": [], "season": [], "party": "couple"}
        for wheel in WHEEL_SEEDS
    }


def clean_prefs(payload, existing):
    """Per-wheel filter preferences; unknown values are dropped."""
    if not isinstance(payload, dict):
        payload = {}
    prefs = {}
    for wheel in WHEEL_SEEDS:
        base = existing.get(wheel, {})
        raw = payload.get(wheel, base)
        if not isinstance(raw, dict):
            raw = base
        out = {}
        for key, allowed in (("budget", BUDGETS), ("distance", DISTANCES),
                             ("vibe", VIBES), ("season", SEASONS)):
            values = raw.get(key, base.get(key, []))
            out[key] = [v for v in values if v in allowed] if isinstance(values, list) else []
        party = raw.get("party", base.get("party", "couple"))
        out["party"] = party if party in PARTIES else "couple"
        prefs[wheel] = out
    return prefs


def current_user(db):
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    session = db["sessions"].get(token)
    user = db["users"].get(session["user"]) if session else None
    if not user:
        abort(401, description="please log in")
    return user


def space_members(db, space_id):
    return sorted(u["name"] for u in db["users"].values() if u["space"] == space_id)


def unclaimed_space(db):
    """The space nobody belongs to — exists only after migrating a
    pre-account database, and holds that household's old wheels."""
    claimed = {u["space"] for u in db["users"].values()}
    return next((s for s in db["spaces"].values() if s["id"] not in claimed), None)


def me_payload(db, user):
    space = db["spaces"][user["space"]]
    return {
        "user": {"id": user["id"], "name": user["name"]},
        "prefs": user["prefs"],
        "space": {
            "code": space["code"],
            "onboarded": space["onboarded"],
            "members": space_members(db, space["id"]),
        },
        # lets the onboarding screen offer "keep your old wheels"
        "legacy_available": unclaimed_space(db) is not None,
    }


def start_session(db, user):
    token = secrets.token_urlsafe(32)
    db["sessions"][token] = {
        "user": user["id"],
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return token


def drop_space_if_empty(db, space_id):
    if not any(u["space"] == space_id for u in db["users"].values()):
        db["spaces"].pop(space_id, None)


@app.post("/api/auth/register")
def register():
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name", "")).strip()[:30]
    password = str(payload.get("password", ""))
    if len(name) < 2:
        abort(400, description="pick a name of at least 2 characters")
    if len(password) < 4:
        abort(400, description="pick a password of at least 4 characters")
    with _lock:
        db = load_db()
        if any(u["name"].lower() == name.lower() for u in db["users"].values()):
            abort(400, description="that name is already taken — log in instead?")
        space = new_space()
        db["spaces"][space["id"]] = space
        user = {
            "id": "u-" + uuid.uuid4().hex[:10],
            "name": name,
            "salt": secrets.token_hex(16),
            "space": space["id"],
            "prefs": default_prefs(),
            "created": datetime.now(timezone.utc).isoformat(),
        }
        user["password"] = hash_password(password, user["salt"])
        db["users"][user["id"]] = user
        token = start_session(db, user)
        save_db(db)
        return jsonify({"token": token, "me": me_payload(db, user)}), 201


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
        user["prefs"] = clean_prefs(payload, user["prefs"])
        save_db(db)
        return jsonify(user["prefs"])


# ── Spaces (sharing) ─────────────────────────────────────────────────
@app.post("/api/space/join")
def join_space():
    payload = request.get_json(force=True, silent=True) or {}
    code = str(payload.get("code", "")).strip().upper().replace(" ", "")
    if not code:
        abort(400, description="enter a share code")
    with _lock:
        db = load_db()
        user = current_user(db)
        target = next((s for s in db["spaces"].values() if s["code"] == code), None)
        if target is None:
            abort(404, description="no wheels found for that code — check it with your partner")
        if target["id"] == user["space"]:
            abort(400, description="you're already sharing these wheels")
        old = user["space"]
        user["space"] = target["id"]
        drop_space_if_empty(db, old)
        save_db(db)
        return jsonify(me_payload(db, user))


@app.post("/api/space/claim")
def claim_space():
    """Adopt the wheels migrated from a pre-account database."""
    with _lock:
        db = load_db()
        user = current_user(db)
        target = unclaimed_space(db)
        if target is None:
            abort(404, description="no unclaimed wheels found — they may already be claimed")
        old = user["space"]
        user["space"] = target["id"]
        drop_space_if_empty(db, old)
        save_db(db)
        return jsonify(me_payload(db, user))


@app.post("/api/space/leave")
def leave_space():
    """Start over: move to a fresh, not-yet-onboarded space of your own."""
    with _lock:
        db = load_db()
        user = current_user(db)
        old = user["space"]
        space = new_space()
        db["spaces"][space["id"]] = space
        user["space"] = space["id"]
        drop_space_if_empty(db, old)
        save_db(db)
        return jsonify(me_payload(db, user))


# ── Onboarding ───────────────────────────────────────────────────────
def seed_wheels(home, roam, vibes, budget):
    """Build both wheels from the catalogues, tailored to the answers.

    - distance is recomputed relative to `home` (each catalogue entry
      lists the home regions it is "regional" for; long-haul stays put)
    - destinations beyond the chosen roam range are kept but disabled,
      so they stay discoverable in the manage panel
    - entries matching the chosen vibes (and budget) get pre-starred
    """
    allowed = ROAM_DISTANCES[roam]
    wheels = {}
    for wheel in WHEEL_SEEDS:
        destinations = []
        for entry in catalog(wheel):
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
                "enabled": distance in allowed,
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
        wheels[wheel] = {"destinations": destinations, "history": []}
    return wheels


@app.post("/api/onboarding")
def onboarding():
    payload = request.get_json(force=True, silent=True) or {}
    home = payload.get("home")
    roam = payload.get("roam", "europe")
    budget = payload.get("budget", "mix")
    vibes = payload.get("vibes", [])
    if home not in HOME_REGIONS:
        abort(400, description="tell us where home is first")
    if roam not in ROAM_DISTANCES:
        abort(400, description="unknown roam range")
    if budget not in BUDGET_STYLES:
        abort(400, description="unknown budget style")
    vibes = [v for v in vibes if v in VIBES] if isinstance(vibes, list) else []
    with _lock:
        db = load_db()
        user = current_user(db)
        space = db["spaces"][user["space"]]
        if space["onboarded"]:
            abort(409, description="these wheels are already set up")
        space["wheels"] = seed_wheels(home, roam, vibes, budget)
        space["onboarded"] = True
        save_db(db)
        return jsonify(me_payload(db, user))


# ── Validation ───────────────────────────────────────────────────────
def clean_destination(payload, existing=None):
    """Normalise and validate a destination payload; raises ValueError."""
    base = existing or {}
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")

    name = str(payload.get("name", base.get("name", ""))).strip()[:60]
    if not name:
        raise ValueError("name is required")

    def subset(key, allowed, fallback):
        values = payload.get(key, base.get(key, fallback))
        if not isinstance(values, list):
            return fallback
        values = [v for v in values if v in allowed]
        return values or fallback

    budget = payload.get("budget", base.get("budget", "mid"))
    distance = payload.get("distance", base.get("distance", "europe"))
    return {
        "id": base.get("id") or "d-" + uuid.uuid4().hex[:10],
        "name": name,
        "flag": (str(payload.get("flag", base.get("flag", "📍"))).strip() or "📍")[:28],
        "budget": budget if budget in BUDGETS else "mid",
        "distance": distance if distance in DISTANCES else "europe",
        "vibes": subset("vibes", VIBES, ["nature"]),
        "seasons": subset("seasons", SEASONS, sorted(SEASONS)),
        "party": subset("party", PARTIES, sorted(PARTIES)),
        "favorite": bool(payload.get("favorite", base.get("favorite", False))),
        "enabled": bool(payload.get("enabled", base.get("enabled", True))),
    }


def wheel_data(db, user, wheel):
    if wheel not in WHEEL_SEEDS:
        abort(404, description=f"unknown wheel '{wheel}'")
    return db["spaces"][user["space"]]["wheels"].setdefault(
        wheel, {"destinations": [], "history": []}
    )


# ── Destinations API ─────────────────────────────────────────────────
@app.get("/api/wheels/<wheel>/destinations")
def list_destinations(wheel):
    with _lock:
        db = load_db()
        return jsonify(wheel_data(db, current_user(db), wheel)["destinations"])


@app.post("/api/wheels/<wheel>/destinations")
def create_destination(wheel):
    try:
        dest = clean_destination(request.get_json(force=True, silent=True))
    except ValueError as err:
        abort(400, description=str(err))
    with _lock:
        db = load_db()
        wheel_data(db, current_user(db), wheel)["destinations"].append(dest)
        save_db(db)
    return jsonify(dest), 201


@app.put("/api/wheels/<wheel>/destinations/<dest_id>")
def update_destination(wheel, dest_id):
    with _lock:
        db = load_db()
        destinations = wheel_data(db, current_user(db), wheel)["destinations"]
        for i, dest in enumerate(destinations):
            if dest["id"] == dest_id:
                try:
                    updated = clean_destination(request.get_json(force=True, silent=True), existing=dest)
                except ValueError as err:
                    abort(400, description=str(err))
                destinations[i] = updated
                save_db(db)
                return jsonify(updated)
    abort(404, description="destination not found")


@app.delete("/api/wheels/<wheel>/destinations/<dest_id>")
def delete_destination(wheel, dest_id):
    with _lock:
        db = load_db()
        data = wheel_data(db, current_user(db), wheel)
        before = len(data["destinations"])
        data["destinations"] = [d for d in data["destinations"] if d["id"] != dest_id]
        if len(data["destinations"]) == before:
            abort(404, description="destination not found")
        save_db(db)
    return "", 204


# ── History API ──────────────────────────────────────────────────────
@app.get("/api/wheels/<wheel>/history")
def list_history(wheel):
    with _lock:
        db = load_db()
        return jsonify(wheel_data(db, current_user(db), wheel)["history"])


@app.post("/api/wheels/<wheel>/history")
def add_history(wheel):
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get("name", "")).strip()[:60]
    if not name:
        abort(400, description="name is required")
    with _lock:
        db = load_db()
        user = current_user(db)
        entry = {
            "name": name,
            "flag": (str(payload.get("flag", "📍")).strip() or "📍")[:28],
            "date": datetime.now(timezone.utc).isoformat(),
            "by": user["name"],
        }
        data = wheel_data(db, user, wheel)
        data["history"] = ([entry] + data["history"])[:HISTORY_LIMIT]
        save_db(db)
        return jsonify(data["history"]), 201


@app.delete("/api/wheels/<wheel>/history")
def clear_history(wheel):
    with _lock:
        db = load_db()
        wheel_data(db, current_user(db), wheel)["history"] = []
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
