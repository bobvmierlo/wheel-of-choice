"""The SQLite storage layer: row-per-record, diffed saves, legacy import."""
from conftest import register, write_db
from wheelapp import core, storage


def _rows():
    conn = storage._connect()
    try:
        return storage._read_rows(conn)
    finally:
        conn.close()


def test_one_row_per_record(client):
    register(client, "Alice")
    register(client, "Bob")
    rows = _rows()
    assert len([k for k in rows if k[0] == "users"]) == 2
    assert len([k for k in rows if k[0] == "sessions"]) == 2
    assert ("_meta", "version") in rows


def test_save_writes_only_changed_rows(client, monkeypatch):
    register(client, "Alice")
    register(client, "Bob")
    db = storage.load_db()
    alice = next(u for u in db["users"].values() if u["name"] == "Alice")
    alice["email"] = "alice@example.com"
    written = []
    real = storage._connect

    def spy():
        conn = real()

        class Wrapped:
            def __getattr__(self, name):
                return getattr(conn, name)

            def executemany(self, sql, seq):
                seq = list(seq)
                if sql.startswith("INSERT"):
                    written.extend(seq)
                return conn.executemany(sql, seq)
        return Wrapped()
    monkeypatch.setattr(storage, "_connect", spy)
    storage.save_db(db)
    assert [(c, k) for c, k, _ in written] == [("users", alice["id"])]


def test_removed_records_are_deleted(client):
    s = register(client, "Alice")
    register(client, "Bob")
    db = storage.load_db()
    db["users"] = {s.user_id: db["users"][s.user_id]}
    storage.save_db(db)
    assert [n["name"] for n in storage.load_db()["users"].values()] == ["Alice"]


def test_plain_dict_replaces_everything(client):
    register(client, "Alice")
    storage.save_db({"version": 3, "users": {}, "sessions": {}, "wheels": {}})
    assert storage.load_db()["users"] == {}
    assert not [k for k in _rows() if k[0] == "users"]


def test_legacy_json_is_imported_once(client):
    write_db({"version": 3, "users": {}, "sessions": {}, "wheels": {},
              "settings": {"registration_open": False}})
    assert storage.load_db()["settings"]["registration_open"] is False
    assert core.DB_PATH.exists()
    assert (core.DATA_DIR / "db.json.imported").exists()
    assert not core.LEGACY_DB_FILE.exists()


def test_no_database_file_until_something_is_saved(client):
    storage.load_db()
    assert not core.DB_PATH.exists()
