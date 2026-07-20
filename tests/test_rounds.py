"""Rounds: spins, vetoes, the pending-pick handshake.

This is the app's subtlest logic, so it gets the most coverage: the
"wait for everyone who can still veto" rule, one-veto-per-round, and the
restaurant shortcut where the wheel's word is final.
"""
from conftest import (register, make_travel_wheel, make_restaurant_wheel,
                      add_entry)


def code_of(me, wid):
    return next(w["code"] for w in me["wheels"] if w["id"] == wid)


def a_dest(session, wid):
    dests = session.get(f"/api/wheels/{wid}/destinations").get_json()
    return next(d["id"] for d in dests if d.get("enabled", True))


# ── Restaurant wheels: no vetoes, the wheel's word is final ──────────
def test_restaurant_pick_lands_in_history_immediately(client):
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    entry = add_entry(s, wid, name="Pho 88")
    resp = s.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": entry["id"]})
    body = resp.get_json()
    assert body["final"] is True
    assert any(h["name"] == "Pho 88" for h in body["history"])


def test_restaurant_veto_is_refused(client):
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    entry = add_entry(s, wid, name="Pho 88")
    resp = s.post(f"/api/wheels/{wid}/round/veto", json={"dest_id": entry["id"]})
    assert resp.status_code == 400


# ── Travel wheels: the pending handshake ─────────────────────────────
def test_solo_travel_pick_is_final(client):
    """With nobody else able to veto, the spinner's pick lands at once."""
    s = register(client, "Alice")
    wid, _ = make_travel_wheel(s)
    dest = a_dest(s, wid)
    body = s.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest}).get_json()
    assert body["final"] is True


def test_two_member_pick_waits_for_confirmation(client):
    alice = register(client, "Alice")
    wid, me = make_travel_wheel(alice)
    bob = register(client, "Bob")
    bob.post("/api/wheels/join", json={"code": code_of(me, wid)})

    dest = a_dest(alice, wid)
    body = alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest}).get_json()
    assert body["final"] is False
    assert body["round"]["pending"]["dest_id"] == dest

    # Bob confirms -> final
    body = bob.post(f"/api/wheels/{wid}/round/confirm").get_json()
    assert body["final"] is True
    assert any(h["dest_id"] == dest for h in body["history"])


def test_veto_shoots_down_pending_pick(client):
    alice = register(client, "Alice")
    wid, me = make_travel_wheel(alice)
    bob = register(client, "Bob")
    bob.post("/api/wheels/join", json={"code": code_of(me, wid)})

    dest = a_dest(alice, wid)
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest})
    body = bob.post(f"/api/wheels/{wid}/round/veto", json={"dest_id": dest}).get_json()
    assert body["pending"] is None
    assert dest in body["vetoed_ids"]


def test_one_veto_per_round(client):
    alice = register(client, "Alice")
    wid, me = make_travel_wheel(alice)
    bob = register(client, "Bob")
    bob.post("/api/wheels/join", json={"code": code_of(me, wid)})

    dests = [d["id"] for d in alice.get(f"/api/wheels/{wid}/destinations").get_json()
             if d.get("enabled", True)][:2]
    assert bob.post(f"/api/wheels/{wid}/round/veto", json={"dest_id": dests[0]}).status_code == 200
    # second veto in the same round is refused
    assert bob.post(f"/api/wheels/{wid}/round/veto", json={"dest_id": dests[1]}).status_code == 409


def test_vetoed_destination_cannot_be_picked(client):
    alice = register(client, "Alice")
    wid, me = make_travel_wheel(alice)
    bob = register(client, "Bob")
    bob.post("/api/wheels/join", json={"code": code_of(me, wid)})

    dest = a_dest(alice, wid)
    bob.post(f"/api/wheels/{wid}/round/veto", json={"dest_id": dest})
    resp = alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest})
    assert resp.status_code == 409


def test_three_members_need_everyone_to_confirm(client):
    alice = register(client, "Alice")
    wid, me = make_travel_wheel(alice)
    bob = register(client, "Bob")
    carol = register(client, "Carol")
    bob.post("/api/wheels/join", json={"code": code_of(me, wid)})
    carol.post("/api/wheels/join", json={"code": code_of(me, wid)})

    dest = a_dest(alice, wid)
    alice.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest})
    # Bob confirms but Carol hasn't -> still pending
    body = bob.post(f"/api/wheels/{wid}/round/confirm").get_json()
    assert body["final"] is False
    assert "Carol" in body["round"]["pending"]["waiting_names"]
    # Carol confirms -> final
    body = carol.post(f"/api/wheels/{wid}/round/confirm").get_json()
    assert body["final"] is True


def test_finalized_pick_clears_the_round(client):
    s = register(client, "Alice")
    wid, _ = make_travel_wheel(s)
    dest = a_dest(s, wid)
    s.post(f"/api/wheels/{wid}/round/pick", json={"dest_id": dest})
    rnd = s.get(f"/api/wheels/{wid}/round").get_json()
    assert rnd["pending"] is None
    assert rnd["vetoed_ids"] == []
