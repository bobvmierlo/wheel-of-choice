"""backup — part of the Wheel of Choice server (see server.py)."""
import json
from datetime import datetime, timezone

from flask import abort, jsonify, request

from .core import (
    APP_VERSION,
    DATA_DIR,
    VAPID_KEY_FILE,
    _lock,
    app,
    webpush,
)
from . import accounts, admin_api, calendars, oidc, push, storage


# ── Backup & restore ─────────────────────────────────────────────────
# A backup is one JSON file holding *everything* a server needs to come
# back exactly as it was: the database (accounts, wheels, history,
# sessions, settings — SSO config included) plus the files that live next
# to it. Today that's the VAPID private key: without it, every push
# subscription in the database is dead weight after a move to a new box.
# Restoring works from the admin panel, or — on a brand-new server with
# no accounts yet — straight from the first screen, so a blank container
# can be brought back in one step.
#
# Older backups (a bare db.json) are still accepted; they just don't
# carry the key, so the server keeps whichever one it has.
BACKUP_FORMAT = "wheel-of-choice-backup"
BACKUP_FORMAT_VERSION = 1
BACKUP_FILES = {"vapid-private-key.pem": VAPID_KEY_FILE}
MAX_BACKUP_FILE_CHARS = 20_000  # a PEM key is ~250 chars; anything near this is bogus


def build_backup(db):
    if webpush is not None:
        push.vapid_public_key()  # make sure the key exists, so it's in the backup
    files = {}
    for name, path in BACKUP_FILES.items():
        if path.exists():
            files[name] = path.read_text(encoding="utf-8")
    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "created": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "db": db,
        "files": files,
    }


def _looks_like_private_key(text):
    if webpush is not None:
        try:
            from cryptography.hazmat.primitives import serialization
            serialization.load_pem_private_key(text.encode("utf-8"), password=None)
            return True
        except Exception:
            return False
    return "-----BEGIN" in text and "PRIVATE KEY-----" in text


def parse_backup(payload):
    """(db, files) from an uploaded backup — the full format or a bare
    db.json from before it. Aborts 400 on anything that isn't one."""
    files = {}
    if isinstance(payload, dict) and payload.get("format") == BACKUP_FORMAT:
        if int(payload.get("format_version") or 0) > BACKUP_FORMAT_VERSION:
            abort(400, description="that backup comes from a newer version of Wheel of Choice — "
                                   "update this server first")
        raw_files = payload.get("files") or {}
        if not isinstance(raw_files, dict):
            abort(400, description="that backup's files section is damaged")
        for name, text in raw_files.items():
            if name not in BACKUP_FILES:
                continue  # unknown extras are ignored, never written
            if not isinstance(text, str) or len(text) > MAX_BACKUP_FILE_CHARS \
                    or not _looks_like_private_key(text):
                abort(400, description=f"the {name} in that backup is damaged")
            files[name] = text
        payload = payload.get("db")
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), dict) for key in ("users", "sessions")
    ) or not any(isinstance(payload.get(key), dict) for key in ("wheels", "spaces")):
        abort(400, description="that doesn't look like a Wheel of Choice backup")
    payload.setdefault("version", 2 if "spaces" in payload else 3)
    return payload, files


def apply_restore(db, files):
    """Write a parsed backup to disk and drop every in-memory cache that
    was derived from the old data. Call with _lock held."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = BACKUP_FILES[name]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
    if "vapid-private-key.pem" in files:
        with push._vapid_lock:
            push._vapid_public_key = None  # re-derived from the restored key on next use
    storage.save_db(db)
    with accounts._login_lock:
        accounts._login_fails.clear()
    with oidc._oidc_lock:
        oidc._oidc_discovery.clear()
        oidc._oidc_pending.clear()
        oidc._oidc_handoffs.clear()
    with calendars._cal_lock:
        calendars._cal_cache.clear()


@app.get("/api/admin/backup")
def admin_backup():
    """Everything — database and the VAPID key — as one downloadable file.
    It holds password hashes, live session tokens, the SSO client secret
    and the push signing key: keep it as safe as the server itself."""
    with _lock:
        db = storage.load_db()
        admin_api.require_admin(db)
        payload = json.dumps(build_backup(db), ensure_ascii=False, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    response = app.response_class(payload, mimetype="application/json")
    response.headers["Content-Disposition"] = (
        f"attachment; filename=wheel-of-choice-backup-{stamp}.json"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/admin/restore")
def admin_restore():
    """Replace everything with an uploaded backup. Backups from the
    spaces era (v2) are accepted too — they're migrated on the next
    load. The current admin's login is carried over when their account
    exists in the backup; otherwise everyone (including them) has to
    log in again."""
    db_new, files = parse_backup(request.get_json(force=True, silent=True))
    key = accounts.request_session_key()
    with _lock:
        db = storage.load_db()
        admin = admin_api.require_admin(db)
        relogin = admin["id"] not in db_new["users"]
        if not relogin:
            db_new["sessions"][key] = db["sessions"][key]
        apply_restore(db_new, files)
    return jsonify({"restored": True, "relogin": relogin, "files": sorted(files)})


@app.post("/api/setup/restore")
def setup_restore():
    """Bring a brand-new server back from a backup, before anyone has an
    account — so moving to a fresh container is one upload, not "register
    a throwaway admin, then restore". Only while the server has no
    accounts at all: the same window in which the first visitor could
    register and become admin anyway, so it opens no new door."""
    accounts.rate_limit("setup-restore")
    db_new, files = parse_backup(request.get_json(force=True, silent=True))
    with _lock:
        if storage.load_db()["users"]:
            abort(403, description="this server already has accounts — "
                                   "log in as an admin and restore from the admin panel")
        apply_restore(db_new, files)
    return jsonify({"restored": True, "files": sorted(files)})
