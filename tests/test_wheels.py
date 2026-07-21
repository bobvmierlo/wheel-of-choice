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


def test_restaurant_location_saved_and_editable(client):
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    created = s.post(f"/api/wheels/{wid}/destinations",
                     json={"name": "Trattoria", "location": "  Main St 1, Springfield  "})
    assert created.status_code == 201
    dest = created.get_json()
    assert dest["location"] == "Main St 1, Springfield"

    updated = s.put(f"/api/wheels/{wid}/destinations/{dest['id']}",
                    json={"location": "Elm St 2"}).get_json()
    assert updated["location"] == "Elm St 2"
    assert updated["name"] == "Trattoria"  # untouched fields survive the edit


def test_travel_destinations_have_no_location(client):
    s = register(client, "Alice")
    wid, _ = make_travel_wheel(s)
    resp = s.post(f"/api/wheels/{wid}/destinations",
                  json={"name": "Lisbon", "location": "somewhere"})
    assert resp.status_code == 201
    assert "location" not in resp.get_json()


def test_dinner_gcal_url_carries_name_address_and_evening():
    import server
    url = server.dinner_gcal_url("Trattoria", "Main St 1, Springfield", "2026-08-01")
    assert "dates=20260801T190000/20260801T210000" in url
    assert "location=Trattoria%2C%20Main%20St%201%2C%20Springfield" in url
    # without an address the name alone marks the spot
    bare = server.dinner_gcal_url("Trattoria", "", "2026-08-01")
    assert "location=Trattoria" in bare


def test_invite_creates_a_pending_invitation(client):
    owner = register(client, "Alice")
    wid, _ = make_restaurant_wheel(owner)
    bob = register(client, "Bob")

    resp = owner.post(f"/api/wheels/{wid}/invite", json={"user_ids": [bob.user_id]})
    assert resp.status_code == 200
    assert resp.get_json()["invited"] == 1

    # Bob sees a pending invite but isn't a member yet
    bob_me = bob.get("/api/me").get_json()
    assert [i["wheel_id"] for i in bob_me["invites"]] == [wid]
    assert bob_me["invites"][0]["from_name"] == "Alice"
    assert wid not in [w["id"] for w in bob_me["wheels"]]

    # Alice sees Bob under the wheel's "pending" list
    alice_me = owner.get("/api/me").get_json()
    wheel = next(w for w in alice_me["wheels"] if w["id"] == wid)
    assert wheel["pending"] == ["Bob"]


def test_accepting_an_invite_joins_the_wheel(client):
    owner = register(client, "Alice")
    wid, _ = make_restaurant_wheel(owner)
    add_entry(owner, wid, name="Shared Bistro")
    bob = register(client, "Bob")
    owner.post(f"/api/wheels/{wid}/invite", json={"user_ids": [bob.user_id]})

    resp = bob.post(f"/api/wheels/{wid}/accept")
    assert resp.status_code == 200
    me = resp.get_json()
    assert me["joined"] == wid
    assert wid in [w["id"] for w in me["wheels"]]
    assert me["invites"] == []  # spent

    # really shares the wheel now
    bob_view = bob.get(f"/api/wheels/{wid}/destinations").get_json()
    assert any(d["name"] == "Shared Bistro" for d in bob_view)


def test_declining_an_invite_drops_it_without_joining(client):
    owner = register(client, "Alice")
    wid, _ = make_restaurant_wheel(owner)
    bob = register(client, "Bob")
    owner.post(f"/api/wheels/{wid}/invite", json={"user_ids": [bob.user_id]})

    assert bob.post(f"/api/wheels/{wid}/decline").status_code == 200
    bob_me = bob.get("/api/me").get_json()
    assert bob_me["invites"] == []
    assert wid not in [w["id"] for w in bob_me["wheels"]]
    assert bob.get(f"/api/wheels/{wid}/destinations").status_code == 404


def test_only_a_member_can_invite_to_a_wheel(client):
    owner = register(client, "Alice")
    wid, _ = make_restaurant_wheel(owner)
    stranger = register(client, "Bob")
    carol = register(client, "Carol")
    assert stranger.post(f"/api/wheels/{wid}/invite",
                         json={"user_ids": [carol.user_id]}).status_code == 404


def test_invitable_list_excludes_self_members_and_invited(client):
    owner = register(client, "Alice")
    wid, me = make_restaurant_wheel(owner)
    bob = register(client, "Bob")
    carol = register(client, "Carol")
    bob.post("/api/wheels/join", json={
        "code": next(w["code"] for w in me["wheels"] if w["id"] == wid)})

    # Bob is a member, Alice is self → only Carol is invitable
    names = [u["name"] for u in owner.get(f"/api/wheels/{wid}/invitable").get_json()]
    assert names == ["Carol"]

    # once Carol is invited she drops off the list too
    owner.post(f"/api/wheels/{wid}/invite", json={"user_ids": [carol.user_id]})
    assert owner.get(f"/api/wheels/{wid}/invitable").get_json() == []


def test_inviting_an_existing_member_is_a_no_op(client):
    owner = register(client, "Alice")
    wid, me = make_restaurant_wheel(owner)
    bob = register(client, "Bob")
    bob.post("/api/wheels/join", json={
        "code": next(w["code"] for w in me["wheels"] if w["id"] == wid)})

    resp = owner.post(f"/api/wheels/{wid}/invite", json={"user_ids": [bob.user_id]})
    assert resp.get_json()["invited"] == 0
    assert bob.get("/api/me").get_json()["invites"] == []


def test_joining_by_code_clears_a_pending_invite(client):
    owner = register(client, "Alice")
    wid, me = make_restaurant_wheel(owner)
    bob = register(client, "Bob")
    owner.post(f"/api/wheels/{wid}/invite", json={"user_ids": [bob.user_id]})

    code = next(w["code"] for w in me["wheels"] if w["id"] == wid)
    bob.post("/api/wheels/join", json={"code": code})
    assert bob.get("/api/me").get_json()["invites"] == []


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
