"""Database migration: v1/v2 layouts must upgrade to v3 without data loss.

A migration bug corrupts real user data silently, so these pin the shape
transitions the README promises: old space codes keep working, histories
survive, and pre-account layouts become claimable wheels.
"""
import server
from conftest import register, write_db


def test_v1_flat_layout_becomes_claimable_wheels(client):
    """The oldest single-household layout (a bare destinations/history)
    migrates into unclaimed wheels the first account can adopt."""
    write_db({
        "destinations": [
            {"id": "d-1", "name": "Rome", "flag": "🇮🇹"},
        ],
        "history": [{"id": "h-1", "name": "Rome", "flag": "🇮🇹", "date": "2020-01-01", "by": "old"}],
    })
    db = server.load_db()
    assert db["version"] == 3
    # the legacy household's holidays wheel carries Rome, unclaimed
    holidays = [w for w in db["wheels"].values() if w["type"] == "holidays"]
    assert any(any(d["name"] == "Rome" for d in w["destinations"]) for w in holidays)
    assert server.unclaimed_wheel_ids(db)  # nobody holds them yet

    # the first account can adopt them
    s = register(client, "Alice")
    assert s.me["legacy_available"] is True
    resp = s.post("/api/wheels/claim")
    assert resp.status_code == 200
    assert any(w["type"] == "holidays" for w in resp.get_json()["wheels"])


def test_v2_space_splits_into_independent_wheels(client):
    """A v2 onboarded space becomes stand-alone wheels; the old space code
    keeps working as the holidays wheel's join code."""
    write_db({
        "version": 2,
        "users": {
            "u-old": {"id": "u-old", "name": "Alice", "salt": "aa" * 16,
                      "password": "x", "space": "s-1", "prefs": {}},
        },
        "sessions": {},
        "spaces": {
            "s-1": {
                "id": "s-1", "code": "OLDCODE", "onboarded": True,
                "wheels": {
                    "holidays": {"destinations": [{"id": "d-1", "name": "Rome", "flag": "🇮🇹"}],
                                 "history": []},
                    "citytrips": {"destinations": [{"id": "d-2", "name": "Porto", "flag": "🇵🇹"}],
                                  "history": []},
                },
            },
        },
    })
    db = server.load_db()
    assert db["version"] == 3

    # Alice now holds two independent wheels
    alice = db["users"]["u-old"]
    assert len(alice["wheels"]) == 2

    # the old space code joins the holidays wheel
    holidays = next(w for w in db["wheels"].values() if w["type"] == "holidays")
    assert holidays["code"] == "OLDCODE"
    assert any(d["name"] == "Rome" for d in holidays["destinations"])

    # and a joiner using the old code lands on exactly that wheel
    joiner = register(client, "Bob")
    resp = joiner.post("/api/wheels/join", json={"code": "OLDCODE"})
    assert resp.status_code == 200
    assert holidays["id"] in [w["id"] for w in resp.get_json()["wheels"]]


def test_migration_is_persisted_once(client):
    """After migrating, the db is saved at v3 so it isn't re-migrated."""
    write_db({
        "destinations": [{"id": "d-1", "name": "Rome", "flag": "🇮🇹"}],
        "history": [],
    })
    server.load_db()
    on_disk = server.json.loads(server.DB_FILE.read_text())
    assert on_disk["version"] == 3
