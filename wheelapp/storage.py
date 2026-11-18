"""storage — part of the Wheel of Choice server (see server.py)."""
import json
import sqlite3
import secrets
import uuid
from urllib.parse import quote


from .core import (
    DATA_DIR,
    DB_PATH,
    LEGACY_DB_FILE,
    TRAVEL_TYPES,
    WHEEL_TYPES,
    apply_runtime_settings,
)
from . import seeding


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


# ── Database (SQLite) ────────────────────────────────────────────────
# The rest of the server works with the database as one plain dict —
# {"users": {...}, "sessions": {...}, "wheels": {...}, "settings": ...} —
# loaded at the start of a request and saved at the end, all under
# core._lock. Underneath, that dict lives in SQLite, one row per account,
# session, wheel and invite (as JSON), plus a row per remaining top-level
# key. save_db() compares against what was loaded and writes only the rows
# that changed, in a single transaction: no more rewriting a whole file on
# every veto, and a crash mid-write can't leave half a database behind.
#
# A db.json from before is imported on first start and kept, renamed, as
# db.json.imported (a ready-made backup of the pre-upgrade state).
ROW_COLLECTIONS = ("users", "sessions", "wheels", "invites")
META = "_meta"
SCHEMA = """CREATE TABLE IF NOT EXISTS docs (
    coll  TEXT NOT NULL,
    key   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (coll, key)
)"""


class Doc(dict):
    """The database dict, remembering the rows it was loaded from so a
    save only has to write what changed."""
    _rows = None


def _connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA)
    return conn


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _rows_of(db):
    rows = {}
    for top, value in db.items():
        if top in ROW_COLLECTIONS and isinstance(value, dict):
            for key, item in value.items():
                rows[(top, str(key))] = _dump(item)
        else:
            rows[(META, top)] = _dump(value)
    return rows


def _read_rows(conn):
    return {(c, k): v for c, k, v in conn.execute("SELECT coll, key, value FROM docs")}


def _import_legacy_json():
    """First start after the upgrade: pull the old db.json in."""
    raw = json.loads(LEGACY_DB_FILE.read_text(encoding="utf-8"))
    save_db(raw)
    LEGACY_DB_FILE.replace(LEGACY_DB_FILE.with_name(LEGACY_DB_FILE.name + ".imported"))


def _read_db():
    if not DB_PATH.exists():
        if LEGACY_DB_FILE.exists():
            _import_legacy_json()
        else:
            return None
    conn = _connect()
    try:
        rows = _read_rows(conn)
    finally:
        conn.close()
    db = Doc()
    for (coll, key), value in rows.items():
        if coll == META:
            db[key] = json.loads(value)
        else:
            db.setdefault(coll, {})[key] = json.loads(value)
    db._rows = rows
    return db


def load_db():
    db = _read_db()
    if db is None:
        apply_runtime_settings({})
        return Doc(version=3, users={}, sessions={}, wheels={})
    if db.get("version", 0) < 3:
        db = migrate_db(db)
        save_db(db)
    for coll in ("users", "sessions", "wheels"):
        db.setdefault(coll, {})  # an emptied collection has no rows left
    if not db.get("home_roam_backfilled"):
        seeding.backfill_home_roam(db)  # stamp home/roam onto pre-existing wheels
        save_db(db)
    apply_runtime_settings(db)  # keep the effective poll knobs in sync
    return ensure_admin(db)


def save_db(db):
    """Write the database dict, touching only rows that changed since it
    was loaded. A dict that wasn't loaded (a restore, a migration result)
    replaces the database wholesale."""
    rows = _rows_of(db)
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        before = db._rows if isinstance(db, Doc) and db._rows is not None else _read_rows(conn)
        gone = [k for k in before if k not in rows]
        changed = [(c, k, v) for (c, k), v in rows.items() if before.get((c, k)) != v]
        conn.executemany("DELETE FROM docs WHERE coll = ? AND key = ?", gone)
        conn.executemany(
            "INSERT INTO docs (coll, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT (coll, key) DO UPDATE SET value = excluded.value", changed)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    if isinstance(db, Doc):
        db._rows = rows
