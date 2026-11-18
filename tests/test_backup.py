"""Backups hold the whole server (db + VAPID key) and restore in one step,
including onto a brand-new server with no accounts."""
import json

import pytest

import server
from conftest import register, login, make_restaurant_wheel

PEM = None


@pytest.fixture(autouse=True)
def fresh_key():
    """Each test starts without a VAPID key on disk or in memory."""
    server.VAPID_KEY_FILE.unlink(missing_ok=True)
    server._vapid_public_key = None
    yield
    server.VAPID_KEY_FILE.unlink(missing_ok=True)
    server._vapid_public_key = None


def _key_pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()


def wipe_server():
    """Simulate a blank container: no db, no key."""
    server.DB_FILE.unlink(missing_ok=True)
    server.VAPID_KEY_FILE.unlink(missing_ok=True)
    server._vapid_public_key = None


def test_backup_contains_db_and_vapid_key(client):
    admin = register(client, "Alice")
    r = admin.get("/api/admin/backup")
    assert r.status_code == 200
    backup = json.loads(r.data)
    assert backup["format"] == "wheel-of-choice-backup"
    assert "Alice" in [u["name"] for u in backup["db"]["users"].values()]
    if server.webpush is not None:
        assert "PRIVATE KEY" in backup["files"]["vapid-private-key.pem"]


def test_restore_on_blank_server_without_account(client):
    admin = register(client, "Alice")
    make_restaurant_wheel(admin, "Pizza night")
    pub_before = admin.get("/api/push/status").get_json().get("public_key") \
        if server.webpush is not None else None
    backup = json.loads(admin.get("/api/admin/backup").data)

    wipe_server()
    assert client.get("/api/config").get_json()["fresh"] is True
    r = client.post("/api/setup/restore", json=backup)
    assert r.status_code == 200, r.get_json()

    assert client.get("/api/config").get_json()["fresh"] is False
    s = login(client, "Alice")
    assert [w["name"] for w in s.me["wheels"]] == ["Pizza night"]
    assert s.me["user"]["admin"] is True
    if server.webpush is not None:
        assert r.get_json()["files"] == ["vapid-private-key.pem"]
        assert server.vapid_public_key() == pub_before  # same key → subscriptions keep working


def test_setup_restore_refused_once_accounts_exist(client):
    admin = register(client, "Alice")
    backup = json.loads(admin.get("/api/admin/backup").data)
    assert client.post("/api/setup/restore", json=backup).status_code == 403


def test_admin_restore_replaces_vapid_key(client):
    if server.webpush is None:
        pytest.skip("push support not installed")
    admin = register(client, "Alice")
    backup = json.loads(admin.get("/api/admin/backup").data)
    other = _key_pem()
    backup["files"]["vapid-private-key.pem"] = other
    r = admin.post("/api/admin/restore", json=backup)
    assert r.status_code == 200 and r.get_json()["relogin"] is False
    assert server.VAPID_KEY_FILE.read_text() == other
    assert admin.get("/api/me").status_code == 200  # admin stays logged in


def test_legacy_bare_db_backup_still_restores(client):
    admin = register(client, "Alice")
    db = json.loads(admin.get("/api/admin/backup").data)["db"]
    if server.webpush is not None:
        key_before = server.VAPID_KEY_FILE.read_text()
    r = admin.post("/api/admin/restore", json=db)
    assert r.status_code == 200 and r.get_json()["files"] == []
    if server.webpush is not None:
        assert server.VAPID_KEY_FILE.read_text() == key_before  # kept as is


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
    assert not (server.DATA_DIR.parent / "evil.sh").exists()


def test_newer_backup_format_refused(client):
    admin = register(client, "Alice")
    backup = json.loads(admin.get("/api/admin/backup").data)
    backup["format_version"] = 99
    assert admin.post("/api/admin/restore", json=backup).status_code == 400


def test_garbage_refused_on_setup(client):
    assert client.post("/api/setup/restore", json={"hello": 1}).status_code == 400
