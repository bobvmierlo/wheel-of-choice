"""Wheels: creation, sharing by code, isolation, leaving."""
from conftest import (register, make_travel_wheel, make_restaurant_wheel,
                      add_entry)


def test_create_restaurant_wheel_starts_empty(client):
    s = register(client, "Alice")
    wid, me = make_restaurant_wheel(s)
    dests = s.get(f"/api/wheels/{wid}/destinations").get_json()
    assert dests == []


def test_create_travel_wheel_is_seeded(client):
    s = register(client, "Alice")
    wid, me = make_travel_wheel(s, "holidays")
    dests = s.get(f"/api/wheels/{wid}/destinations").get_json()
    assert len(dests) > 0
    # every seeded row carries the travel-only fields
    assert all("budget" in d and "vibes" in d for d in dests)


def test_travel_wheel_requires_home(client):
    s = register(client, "Alice")
    resp = s.post("/api/wheels", json={"type": "holidays", "roam": "europe"})
    assert resp.status_code == 400


def test_join_by_code_shares_the_same_wheel(client):
    owner = register(client, "Alice")
    wid, me = make_restaurant_wheel(owner)
    code = next(w["code"] for w in me["wheels"] if w["id"] == wid)

    joiner = register(client, "Bob")
    resp = joiner.post("/api/wheels/join", json={"code": code})
    assert resp.status_code == 200
    assert wid in [w["id"] for w in resp.get_json()["wheels"]]

    # an entry the owner adds is visible to the joiner: same wheel
    add_entry(owner, wid, name="Shared Bistro")
    joiner_view = joiner.get(f"/api/wheels/{wid}/destinations").get_json()
    assert any(d["name"] == "Shared Bistro" for d in joiner_view)


def test_join_with_bad_code_404s(client):
    s = register(client, "Alice")
    assert s.post("/api/wheels/join", json={"code": "NOPE99"}).status_code == 404


def test_wheel_not_in_your_list_is_inaccessible(client):
    owner = register(client, "Alice")
    wid, _ = make_restaurant_wheel(owner)
    stranger = register(client, "Bob")
    assert stranger.get(f"/api/wheels/{wid}/destinations").status_code == 404


def test_leaving_a_solo_wheel_deletes_it(client):
    import server
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    assert s.post(f"/api/wheels/{wid}/leave").status_code == 200
    db = server.load_db()
    assert wid not in db["wheels"]


def test_leaving_a_shared_wheel_keeps_it_for_others(client):
    import server
    owner = register(client, "Alice")
    wid, me = make_restaurant_wheel(owner)
    code = next(w["code"] for w in me["wheels"] if w["id"] == wid)
    joiner = register(client, "Bob")
    joiner.post("/api/wheels/join", json={"code": code})

    owner.post(f"/api/wheels/{wid}/leave")
    db = server.load_db()
    assert wid in db["wheels"]  # Bob still holds it
