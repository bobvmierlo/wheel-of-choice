"""Backups hold the whole server (db + VAPID key) and restore in one step,
including onto a brand-new server with no accounts."""
import json

import pytest

from conftest import wipe_db_files, register, login, make_restaurant_wheel
from wheelapp import core, push

PEM = None


@pytest.fixture(autouse=True)
def fresh_key():
    """Each test starts without a VAPID key on disk or in memory."""
    core.VAPID_KEY_FILE.unlink(missing_ok=True)
    push._vapid_public_key = None
    yield
    core.VAPID_KEY_FILE.unlink(missing_ok=True)
    push._vapid_public_key = None


def _key_pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()


def wipe_server():
    """Simulate a blank container: no db, no key."""
    wipe_db_files()
    core.VAPID_KEY_FILE.unlink(missing_ok=True)
    push._vapid_public_key = None


def test_backup_contains_db_and_vapid_key(client):
    admin = register(client, "Alice")
    r = admin.get("/api/admin/backup")
    assert r.status_code == 200
    backup = json.loads(r.data)
    assert backup["format"] == "wheel-of-choice-backup"
    assert "Alice" in [u["name"] for u in backup["db"]["users"].values()]
    if core.webpush is not None:
        assert "PRIVATE KEY" in backup["files"]["vapid-private-key.pem"]


def test_restore_on_blank_server_without_account(client):
    admin = register(client, "Alice")
    make_restaurant_wheel(admin, "Pizza night")
    pub_before = admin.get("/api/push/status").get_json().get("public_key") \
        if core.webpush is not None else None
    backup = json.loads(admin.get("/api/admin/backup").data)

    wipe_server()
    assert client.get("/api/config").get_json()["fresh"] is True
    r = client.post("/api/setup/restore", json=backup)
    assert r.status_code == 200, r.get_json()

    assert client.get("/api/config").get_json()["fresh"] is False
    s = login(client, "Alice")
    assert [w["name"] for w in s.me["wheels"]] == ["Pizza night"]
    assert s.me["user"]["admin"] is True
    if core.webpush is not None:
        assert r.get_json()["files"] == ["vapid-private-key.pem"]
        assert push.vapid_public_key() == pub_before  # same key → subscriptions keep working


def test_setup_restore_refused_once_accounts_exist(client):
    admin = register(client, "Alice")
    backup = json.loads(admin.get("/api/admin/backup").data)
    assert client.post("/api/setup/restore", json=backup).status_code == 403


def test_admin_restore_replaces_vapid_key(client):
    if core.webpush is None:
        pytest.skip("push support not installed")
    admin = register(client, "Alice")
    backup = json.loads(admin.get("/api/admin/backup").data)
    other = _key_pem()
    backup["files"]["vapid-private-key.pem"] = other
    r = admin.post("/api/admin/restore", json=backup)
    assert r.status_code == 200 and r.get_json()["relogin"] is False
    assert core.VAPID_KEY_FILE.read_text() == other
    assert admin.get("/api/me").status_code == 200  # admin stays logged in


def test_legacy_bare_db_backup_still_restores(client):
    admin = register(client, "Alice")
    db = json.loads(admin.get("/api/admin/backup").data)["db"]
    if core.webpush is not None:
        key_before = core.VAPID_KEY_FILE.read_text()
    r = admin.post("/api/admin/restore", json=db)
    assert r.status_code == 200 and r.get_json()["files"] == []
    if core.webpush is not None:
        assert core.VAPID_KEY_FILE.read_text() == key_before  # kept as is


def test_damaged_key_rejected_and_nothing_written(client):
    admin = register(client, "Alice")
    backup = json.loads(admin.get("/api/admin/backup").data)
    backup["files"]["vapid-private-key.pem"] = "not a key"
    backup["db"]["users"] = {}
    assert admin.post("/api/admin/restore", json=backup).status_code == 400
    assert admin.get("/api/me").status_code == 200


def test_unknown_files_ignored(client, tmp_path):
    admin = register(client, "Alice")
    backup = json.loads(admin.get("/api/admin/backup").data)
    backup["files"]["../../evil.sh"] = "rm -rf /"
    assert admin.post("/api/admin/restore", json=backup).status_code == 200
    assert not (core.DATA_DIR.parent / "evil.sh").exists()


def test_newer_backup_format_refused(client):
    admin = register(client, "Alice")
    backup = json.loads(admin.get("/api/admin/backup").data)
    backup["format_version"] = 99
    assert admin.post("/api/admin/restore", json=backup).status_code == 400


def test_garbage_refused_on_setup(client):
    assert client.post("/api/setup/restore", json={"hello": 1}).status_code == 400


# ── Automatic daily backups ──────────────────────────────────────────
from wheelapp import backup  # noqa: E402


@pytest.fixture
def backup_dir():
    import shutil
    shutil.rmtree(backup.BACKUP_DIR, ignore_errors=True)
    yield backup.BACKUP_DIR
    shutil.rmtree(backup.BACKUP_DIR, ignore_errors=True)


def test_auto_backup_writes_once_per_day_and_prunes(client, backup_dir):
    admin = register(client, "Alice")
    assert backup.run_auto_backup("2026-01-01") is not None
    assert backup.run_auto_backup("2026-01-01") is None  # already there
    admin.put("/api/admin/backups", json={"keep": 3})
    for day in ("2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"):
        backup.run_auto_backup(day)
    names = [b["name"] for b in admin.get("/api/admin/backups").get_json()["backups"]]
    assert len(names) == 3 and names[0] > names[-1]
    assert "wheel-of-choice-2026-01-01.json" not in names
    saved = json.loads((backup_dir / names[0]).read_text())
    assert saved["format"] == "wheel-of-choice-backup"


def test_auto_backup_off_and_empty_server(client, backup_dir):
    assert backup.run_auto_backup("2026-01-01") is None  # no accounts yet
    admin = register(client, "Alice")
    admin.put("/api/admin/backups", json={"keep": 0})
    assert backup.run_auto_backup("2026-01-02") is None


def test_download_and_restore_auto_backup(client, backup_dir):
    admin = register(client, "Alice")
    backup.run_auto_backup("2026-01-01")
    register(client, "Bob")  # made after the backup
    r = admin.get("/api/admin/backups/wheel-of-choice-2026-01-01.json")
    assert r.status_code == 200 and json.loads(r.data)["db"]["users"]
    r = admin.post("/api/admin/backups/wheel-of-choice-2026-01-01.json/restore")
    assert r.status_code == 200 and r.get_json()["relogin"] is False
    names = [u["name"] for u in admin.get("/api/admin/users").get_json()]
    assert names == ["Alice"]


def test_auto_backup_names_are_checked(client, backup_dir):
    admin = register(client, "Alice")
    assert admin.get("/api/admin/backups/..%2Fwheel-of-choice.sqlite3").status_code == 404
    assert admin.get("/api/admin/backups/evil.json").status_code == 404
    bob = register(client, "Bob")
    backup.run_auto_backup("2026-01-01")
    assert bob.get("/api/admin/backups").status_code == 403
