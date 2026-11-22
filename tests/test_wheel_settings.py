"""Newer wheel features: settling stuck rounds, the wheel revision counter,
wheel settings (rename, new code, members, tab order), history clean-up,
the custom wheel type with tags, bulk add, exports and travel date polls.
"""
from datetime import date, timedelta

from conftest import (register, make_travel_wheel, make_restaurant_wheel,
                      add_entry)


def code_of(me, wid):
    return next(w["code"] for w in me["wheels"] if w["id"] == wid)


def enabled_dests(session, wid):
    return [d["id"] for d in session.get(f"/api/wheels/{wid}/destinations").get_json()
            if d.get("enabled", True)]


def three_on_a_travel_wheel(client):
    alice = register(client, "Alice")
    wid, me = make_travel_wheel(alice)
    bob = register(client, "Bob")
    carol = register(client, "Carol")
    for s in (bob, carol):
        assert s.post("/api/wheels/join", json={"code": code_of(me, wid)}).status_code == 200
    return alice, bob, carol, wid


# ── Rounds: a pending pick never gets stuck or overwritten ───────────
def test_last_holdout_vetoing_something_else_settles_the_pick(client):
    alice, bob, carol, wid = three_on_a_travel_wheel(client)
    first, other = enabled_dests(alice, wid)[:2]
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": first})
    bob.post(f"/api/wheels/{wid}/round/confirm")
    body = carol.post(f"/api/wheels/{wid}/round/veto", json={"dest_id": other}).get_json()
    assert body["pending"] is None
    history = alice.get(f"/api/wheels/{wid}/history").get_json()
    assert history and history[0]["dest_id"] == first


def test_last_holdout_leaving_settles_the_pick(client):
    alice, bob, carol, wid = three_on_a_travel_wheel(client)
    first = enabled_dests(alice, wid)[0]
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": first})
    bob.post(f"/api/wheels/{wid}/round/confirm")
    carol.post(f"/api/wheels/{wid}/leave")
    assert alice.get(f"/api/wheels/{wid}/round").get_json()["pending"] is None
    assert alice.get(f"/api/wheels/{wid}/history").get_json()[0]["dest_id"] == first


def test_removing_the_last_holdout_settles_the_pick(client):
    alice, bob, carol, wid = three_on_a_travel_wheel(client)
    first = enabled_dests(alice, wid)[0]
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": first})
    bob.post(f"/api/wheels/{wid}/round/confirm")
    assert alice.post(f"/api/wheels/{wid}/members/{carol.user_id}/remove").status_code == 200
    assert alice.get(f"/api/wheels/{wid}/history").get_json()[0]["dest_id"] == first


def test_someone_elses_waiting_pick_is_not_overwritten(client):
    alice, bob, carol, wid = three_on_a_travel_wheel(client)
    first, other = enabled_dests(alice, wid)[:2]
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": first})
    resp = carol.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": other})
    assert resp.status_code == 409
    assert alice.get(f"/api/wheels/{wid}/round").get_json()["pending"]["dest_id"] == first


def test_spinner_can_withdraw_their_waiting_pick(client):
    alice, bob, carol, wid = three_on_a_travel_wheel(client)
    first = enabled_dests(alice, wid)[0]
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": first})
    assert bob.delete(f"/api/wheels/{wid}/round/pending").status_code == 403
    body = alice.delete(f"/api/wheels/{wid}/round/pending").get_json()
    assert body["pending"] is None and body["vetoes_used"] == 0
    assert alice.get(f"/api/wheels/{wid}/history").get_json() == []


# ── Revision counter ─────────────────────────────────────────────────
def test_rev_changes_when_a_partner_edits_the_wheel(client):
    alice = register(client, "Alice")
    wid, me = make_restaurant_wheel(alice)
    bob = register(client, "Bob")
    bob.post("/api/wheels/join", json={"code": code_of(me, wid)})
    rev = alice.get(f"/api/wheels/{wid}/round").get_json()["rev"]
    assert alice.get(f"/api/wheels/{wid}/round").get_json()["rev"] == rev  # reads don't bump
    entry = add_entry(bob, wid, name="Pho 88")
    rev2 = alice.get(f"/api/wheels/{wid}/round").get_json()["rev"]
    assert rev2 > rev
    bob.put(f"/api/wheels/{wid}/destinations/{entry['id']}", json={"starred": True})
    assert alice.get(f"/api/wheels/{wid}/round").get_json()["rev"] > rev2


# ── Wheel settings ───────────────────────────────────────────────────
def test_rename_wheel(client):
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    me = s.put(f"/api/wheels/{wid}", json={"name": "  Friday   eats "}).get_json()
    assert next(w for w in me["wheels"] if w["id"] == wid)["name"] == "Friday eats"
    assert s.put(f"/api/wheels/{wid}", json={"name": " "}).status_code == 400


def test_new_share_code_retires_the_old_one(client):
    alice = register(client, "Alice")
    wid, me = make_restaurant_wheel(alice)
    old = code_of(me, wid)
    new = code_of(alice.post(f"/api/wheels/{wid}/code").get_json(), wid)
    assert new != old
    bob = register(client, "Bob")
    assert bob.post("/api/wheels/join", json={"code": old}).status_code == 404
    assert bob.post("/api/wheels/join", json={"code": new}).status_code == 200


def test_remove_member_and_withdraw_invite(client):
    alice = register(client, "Alice")
    wid, me = make_restaurant_wheel(alice)
    bob = register(client, "Bob")
    carol = register(client, "Carol")
    bob.post("/api/wheels/join", json={"code": code_of(me, wid)})
    alice.post(f"/api/wheels/{wid}/invite", json={"user_ids": [carol.user_id]})
    summary = next(w for w in alice.get("/api/me").get_json()["wheels"] if w["id"] == wid)
    assert [m["name"] for m in summary["member_list"]] == ["Alice", "Bob"]
    assert [m["name"] for m in summary["pending_list"]] == ["Carol"]

    me = alice.post(f"/api/wheels/{wid}/members/{bob.user_id}/remove").get_json()
    assert next(w for w in me["wheels"] if w["id"] == wid)["members"] == ["Alice"]
    assert wid not in [w["id"] for w in bob.get("/api/me").get_json()["wheels"]]
    assert bob.get(f"/api/wheels/{wid}/destinations").status_code == 404

    alice.post(f"/api/wheels/{wid}/members/{carol.user_id}/remove")
    assert carol.get("/api/me").get_json()["invites"] == []
    assert alice.post(f"/api/wheels/{wid}/members/{alice.user_id}/remove").status_code == 400
    # outsiders can't remove anyone
    assert bob.post(f"/api/wheels/{wid}/members/{alice.user_id}/remove").status_code == 404


def test_reorder_wheel_tabs(client):
    s = register(client, "Alice")
    a, _ = make_restaurant_wheel(s, name="A")
    b, _ = make_restaurant_wheel(s, name="B")
    me = s.put("/api/me/wheel-order", json={"order": [b, a]}).get_json()
    assert [w["id"] for w in me["wheels"]] == [b, a]
    assert s.put("/api/me/wheel-order", json={"order": [b]}).status_code == 400


# ── History ──────────────────────────────────────────────────────────
def test_delete_one_history_entry(client):
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    for name in ("One", "Two"):
        e = add_entry(s, wid, name=name)
        s.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": e["id"]})
    history = s.get(f"/api/wheels/{wid}/history").get_json()
    body = s.delete(f"/api/wheels/{wid}/history/{history[0]['id']}").get_json()
    assert [e["name"] for e in body["history"]] == ["One"]
    assert s.delete(f"/api/wheels/{wid}/history/{history[0]['id']}").status_code == 404


def test_unmarking_been_there_puts_it_back_on_the_wheel(client):
    s = register(client, "Alice")
    wid, _ = make_travel_wheel(s)
    dest = enabled_dests(s, wid)[0]
    s.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest})
    hid = s.get(f"/api/wheels/{wid}/history").get_json()[0]["id"]
    body = s.put(f"/api/wheels/{wid}/history/{hid}", json={"status": "visited"}).get_json()
    assert body["destination"]["enabled"] is False
    body = s.put(f"/api/wheels/{wid}/history/{hid}", json={"status": "booked"}).get_json()
    assert body["destination"]["enabled"] is True
    assert dest in enabled_dests(s, wid)


def test_unmarking_does_not_enable_what_was_already_off(client):
    s = register(client, "Alice")
    wid, _ = make_travel_wheel(s)
    dest = enabled_dests(s, wid)[0]
    s.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest})
    s.put(f"/api/wheels/{wid}/destinations/{dest}", json={"enabled": False})
    hid = s.get(f"/api/wheels/{wid}/history").get_json()[0]["id"]
    s.put(f"/api/wheels/{wid}/history/{hid}", json={"status": "visited"})
    s.put(f"/api/wheels/{wid}/history/{hid}", json={"status": ""})
    assert dest not in enabled_dests(s, wid)


def test_trip_dates_are_validated(client):
    s = register(client, "Alice")
    wid, _ = make_travel_wheel(s)
    s.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": enabled_dests(s, wid)[0]})
    hid = s.get(f"/api/wheels/{wid}/history").get_json()[0]["id"]
    url = f"/api/wheels/{wid}/history/{hid}"
    assert s.put(url, json={"trip_date": "next summer"}).status_code == 400
    assert s.put(url, json={"trip_date": "2027-07-01", "trip_end": "2027-06-01"}).status_code == 400
    body = s.put(url, json={"trip_date": "2027-07-01", "trip_end": "2027-07-08"}).get_json()
    assert body["history"][0]["trip_end"] == "2027-07-08"


# ── Custom wheels, tags, bulk add ────────────────────────────────────
def test_custom_wheel_with_tags_and_tag_filter_prefs(client):
    s = register(client, "Alice")
    resp = s.post("/api/wheels", json={"type": "custom", "name": "Films"})
    assert resp.status_code == 201
    wid = resp.get_json()["created"]
    e = s.post(f"/api/wheels/{wid}/destinations",
               json={"name": "Alien", "tags": ["Horror", " horror ", "Classic"]}).get_json()
    assert e["flag"] == "🎯" and e["tags"] == ["horror", "classic"]
    assert "location" not in e and "budget" not in e
    # spinning a custom wheel is final, like a restaurant wheel
    body = s.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": e["id"]}).get_json()
    assert body["final"] is True
    hid = body["history"][0]["id"]
    # … and it has no date polls
    assert s.post(f"/api/wheels/{wid}/history/{hid}/poll", json={"dates": []}).status_code == 400
    prefs = s.put("/api/me/prefs", json={wid: {"tags": ["Horror"]}}).get_json()
    assert prefs[wid] == {"tags": ["horror"]}


def test_duplicate_names_are_refused(client):
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    first = add_entry(s, wid, name="Pho 88")
    assert s.post(f"/api/wheels/{wid}/destinations", json={"name": " pho  88"}).status_code == 409
    other = add_entry(s, wid, name="Luigi")
    assert s.put(f"/api/wheels/{wid}/destinations/{other['id']}", json={"name": "PHO 88"}).status_code == 409
    assert s.put(f"/api/wheels/{wid}/destinations/{first['id']}", json={"name": "Pho 88"}).status_code == 200


def test_bulk_add(client):
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    add_entry(s, wid, name="Pho 88")
    body = s.post(f"/api/wheels/{wid}/destinations/bulk", json={
        "lines": "🍕 Luigi's\n\n- Sushi Bar\npho 88\n2. Taco Town\nsushi bar\n",
        "tags": ["Friday"],
    }).get_json()
    assert [(d["flag"], d["name"]) for d in body["added"]] == [
        ("🍕", "Luigi's"), ("🍽️", "Sushi Bar"), ("🍽️", "Taco Town")]
    assert body["added"][0]["tags"] == ["friday"]
    assert body["skipped"] == ["pho 88", "sushi bar"]
    assert len(body["destinations"]) == 4
    assert s.post(f"/api/wheels/{wid}/destinations/bulk", json={"lines": "\n"}).status_code == 400


# ── Exports ──────────────────────────────────────────────────────────
def test_exports(client):
    alice = register(client, "Alice")
    wid, me = make_restaurant_wheel(alice, name="Dinner")
    e = add_entry(alice, wid, name="Pho 88")
    alice.put(f"/api/wheels/{wid}/destinations/{e['id']}", json={"starred": True})
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": e["id"]})

    csv_resp = alice.get(f"/api/wheels/{wid}/export/history.csv")
    assert csv_resp.status_code == 200
    text = csv_resp.get_data(as_text=True)
    assert "picked,flag,name,picked_by,status,date,end" in text and "Pho 88" in text

    ics = alice.get(f"/api/wheels/{wid}/export/history.ics").get_data(as_text=True)
    assert ics.startswith("BEGIN:VCALENDAR") and "VEVENT" not in ics  # nothing dated yet

    data = alice.get(f"/api/wheels/{wid}/export/wheel.json").get_json()
    assert data["name"] == "Dinner" and data["destinations"][0]["starred_by"] == ["Alice"]
    assert "code" not in data

    outsider = register(client, "Mallory")
    assert outsider.get(f"/api/wheels/{wid}/export/wheel.json").status_code == 404


# ── Travel date polls ────────────────────────────────────────────────
def test_travel_date_poll_locks_a_trip(client):
    alice = register(client, "Alice")
    wid, me = make_travel_wheel(alice)
    bob = register(client, "Bob")
    bob.post("/api/wheels/join", json={"code": code_of(me, wid)})
    dest = enabled_dests(alice, wid)[0]
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest})
    bob.post(f"/api/wheels/{wid}/round/confirm")
    hid = alice.get(f"/api/wheels/{wid}/history").get_json()[0]["id"]

    start = (date.today() + timedelta(days=200)).isoformat()  # beyond a dinner horizon
    later = (date.today() + timedelta(days=210)).isoformat()
    url = f"/api/wheels/{wid}/history/{hid}/poll"
    assert alice.post(url, json={"dates": [start, later]}).status_code == 400  # nights missing
    assert alice.post(url, json={"dates": [start, later], "nights": 0}).status_code == 400
    body = alice.post(url, json={"dates": [start, later], "nights": 4}).get_json()
    assert body["history"][0]["poll"]["nights"] == 4
    bob.put(f"{url}/votes", json={"dates": [start]})
    body = alice.post(f"{url}/lock", json={"date": start}).get_json()
    entry = body["history"][0]
    assert entry["trip_date"] == start
    assert entry["trip_end"] == (date.fromisoformat(start) + timedelta(days=4)).isoformat()

    ics = alice.get(f"/api/wheels/{wid}/export/history.ics").get_data(as_text=True)
    assert f"DTSTART;VALUE=DATE:{start.replace('-', '')}" in ics

    # scrapping a locked trip poll frees the dates again
    entry = alice.delete(url).get_json()["history"][0]
    assert "trip_date" not in entry and "trip_end" not in entry
