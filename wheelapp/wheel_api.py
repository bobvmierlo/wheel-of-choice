"""wheel_api — part of the Wheel of Choice server (see server.py)."""
from datetime import datetime, timezone

from flask import abort, jsonify, request

from .core import (
    BUDGET_STYLES,
    HOME_REGIONS,
    MAX_INVITE_BATCH,
    ROAM_DISTANCES,
    VIBES,
    WHEEL_TYPES,
    _lock,
    app,
)
from . import i18n, accounts, push, rounds, seeding, storage, validation


# ── Wheels (create / join / leave / claim) ───────────────────────────
@app.post("/api/wheels")
def create_wheel():
    """Create a wheel of your own. Travel wheels are seeded from the
    catalogue using the onboarding answers; a restaurant wheel starts
    empty — every entry on it is added by hand."""
    payload = request.get_json(force=True, silent=True) or {}
    wheel_type = payload.get("type")
    if wheel_type not in WHEEL_TYPES:
        abort(400, description="unknown wheel type")
    name = str(payload.get("name", "")).strip()[:40]
    travel = WHEEL_TYPES[wheel_type]["travel"]
    if travel:
        home = payload.get("home")
        roam = payload.get("roam", "europe")
        budget = payload.get("budget", "mix")
        vibes = payload.get("vibes", [])
        favorites = payload.get("favorites", [])
        if home not in HOME_REGIONS:
            abort(400, description="tell us where home is first")
        if roam not in ROAM_DISTANCES:
            abort(400, description="unknown roam range")
        if budget not in BUDGET_STYLES:
            abort(400, description="unknown budget style")
        vibes = [v for v in vibes if v in VIBES] if isinstance(vibes, list) else []
        favorites = [f for f in favorites if isinstance(f, str)] if isinstance(favorites, list) else []
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = storage.new_wheel(wheel_type, name)
        if travel:
            # keep the answers that shape seeding, so a later catalogue
            # sync can promote regional gems exactly as creation did
            wheel["home"] = home
            wheel["roam"] = roam
            wheel["destinations"] = seeding.seed_destinations(
                wheel_type, home, roam, vibes, budget, favorites, user["id"]
            )
        db["wheels"][wheel["id"]] = wheel
        user.setdefault("wheels", []).append(wheel["id"])
        storage.save_db(db)
        me = accounts.me_payload(db, user)
        me["created"] = wheel["id"]
        return jsonify(me), 201


@app.post("/api/wheels/join")
def join_wheel():
    """Add someone else's wheel to your list via its share code. Your
    other wheels are untouched — sharing is strictly per wheel."""
    payload = request.get_json(force=True, silent=True) or {}
    code = str(payload.get("code", "")).strip().upper().replace(" ", "")
    if not code:
        abort(400, description="enter a share code")
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        target = next((w for w in db["wheels"].values() if w["code"] == code), None)
        if target is None:
            abort(404, description="no wheel found for that code — double-check it with whoever shared it")
        if target["id"] in user.get("wheels", []):
            abort(400, description="that wheel is already in your list")
        user.setdefault("wheels", []).append(target["id"])
        # joining by code settles any pending invitation to the same wheel
        user["invites"] = [i for i in user.get("invites", []) if i["wheel_id"] != target["id"]]
        storage.save_db(db)
        me = accounts.me_payload(db, user)
        me["joined"] = target["id"]
        return jsonify(me)


@app.post("/api/wheels/claim")
def claim_wheels():
    """Adopt the wheels migrated from a pre-account database."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        ids = accounts.unclaimed_wheel_ids(db)
        if not ids:
            abort(404, description="no unclaimed wheels found — they may already be claimed")
        user.setdefault("wheels", []).extend(ids)
        storage.save_db(db)
        return jsonify(accounts.me_payload(db, user))


@app.post("/api/wheels/<wheel_id>/leave")
def leave_wheel(wheel_id):
    """Take a wheel off your list. Other members keep spinning it; a
    wheel nobody is left on is deleted for good."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        if wheel_id not in user.get("wheels", []):
            abort(404, description="that wheel isn't in your list")
        user["wheels"].remove(wheel_id)
        user.get("prefs", {}).pop(wheel_id, None)
        accounts.drop_wheel_if_empty(db, wheel_id)
        storage.save_db(db)
        # if the leaver was the last one a waiting pick needed, it's decided
        rounds.settle_wheels(db, [wheel_id])
        return jsonify(accounts.me_payload(db, user))


# ── Wheel settings (rename / new code / members / tab order) ─────────
@app.put("/api/wheels/<wheel_id>")
def rename_wheel(wheel_id):
    """Rename a wheel — for everyone on it, like every other wheel edit."""
    payload = request.get_json(force=True, silent=True) or {}
    name = " ".join(str(payload.get("name", "")).split())[:40]
    if not name:
        abort(400, description="give the wheel a name")
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        wheel["name"] = name
        storage.save_db(db)
        return jsonify(accounts.me_payload(db, user))


@app.post("/api/wheels/<wheel_id>/code")
def renew_code(wheel_id):
    """Swap the wheel's share code for a fresh one. The old code and every
    invite link built from it stop working at once; members stay on."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        taken = {w["code"] for w in db["wheels"].values()}
        taken |= set(db.get("invites") or {})  # admin account-invite codes
        code = storage.new_invite_code()
        while code in taken:
            code = storage.new_invite_code()
        wheel["code"] = code
        storage.save_db(db)
        return jsonify(accounts.me_payload(db, user))


@app.post("/api/wheels/<wheel_id>/members/<member_id>/remove")
def remove_member(wheel_id, member_id):
    """Take someone off this one wheel — or withdraw an invitation that's
    still waiting for them. Any member may (the same household trust as
    clearing the history); the removed person keeps their other wheels."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        if member_id == user["id"]:
            abort(400, description="to take yourself off, leave the wheel instead")
        target = db["users"].get(member_id)
        if target is None:
            abort(404, description="no such person on this wheel")
        invites = target.get("invites", [])
        if wheel_id in target.get("wheels", []):
            target["wheels"].remove(wheel_id)
            target.get("prefs", {}).pop(wheel_id, None)
            storage.save_db(db)
            push.push_to_users(
                [target],
                i18n.msg('👋 {user} took you off {wheel}', user=user['name'], wheel=wheel['name']),
                "Ask them for the code if that was a mistake.",
                wheel,
                tag=f"{wheel_id}-removed",
                url="/",
            )
            rounds.settle_wheels(db, [wheel_id])
        elif any(i["wheel_id"] == wheel_id for i in invites):
            target["invites"] = [i for i in invites if i["wheel_id"] != wheel_id]
            storage.save_db(db)
        else:
            abort(404, description="no such person on this wheel")
        return jsonify(accounts.me_payload(db, user))


@app.put("/api/me/wheel-order")
def reorder_wheels():
    """Put the wheel tabs in the order given — a reshuffle of your own list."""
    order = (request.get_json(force=True, silent=True) or {}).get("order")
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        mine = user.get("wheels", [])
        if not isinstance(order, list) or sorted(map(str, order)) != sorted(mine):
            abort(400, description="send every one of your wheels, in the new order")
        user["wheels"] = [str(w) for w in order]
        storage.save_db(db)
        return jsonify(accounts.me_payload(db, user))


# ── Sharing to existing accounts (invite / accept / decline) ─────────
def wheel_i_share(db, user, wheel_id):
    """A wheel the current user is on — the gate for inviting others.
    You can only share a wheel you're a member of yourself."""
    wheel = db["wheels"].get(wheel_id)
    if wheel is None or wheel_id not in user.get("wheels", []):
        abort(404, description="that wheel isn't in your list")
    return wheel


@app.get("/api/wheels/<wheel_id>/invitable")
def wheel_invitable(wheel_id):
    """People you could share this wheel with: every other account on the
    server that isn't already on it or already invited. Lets the share
    screen offer a pick-from-a-list instead of passing a code around."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel_i_share(db, user, wheel_id)
        out = []
        for u in sorted(db["users"].values(), key=lambda x: x["name"].lower()):
            if u["id"] == user["id"] or wheel_id in u.get("wheels", []):
                continue
            if any(inv["wheel_id"] == wheel_id for inv in u.get("invites", [])):
                continue  # already has a pending invite — see the wheel's "pending"
            out.append({"id": u["id"], "name": u["name"]})
        return jsonify(out)


@app.post("/api/wheels/<wheel_id>/invite")
def invite_to_wheel(wheel_id):
    """Share this wheel with existing accounts by id. Each gets a pending
    invitation (and a push) to accept or decline — the wheel only lands
    on their list once they say yes. You must be on the wheel to share
    it. Ids that are unknown, already a member, or already invited are
    skipped quietly, so re-sending is safe."""
    payload = request.get_json(force=True, silent=True) or {}
    ids = payload.get("user_ids")
    if not isinstance(ids, list) or not ids:
        abort(400, description="pick at least one person to share with")
    ids = [str(x) for x in ids][:MAX_INVITE_BATCH]
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = wheel_i_share(db, user, wheel_id)
        now = datetime.now(timezone.utc).isoformat()
        invited = []
        for uid in ids:
            target = db["users"].get(uid)
            if target is None or target["id"] == user["id"]:
                continue
            if wheel_id in target.get("wheels", []):
                continue  # already spinning this one
            pending = target.setdefault("invites", [])
            if any(inv["wheel_id"] == wheel_id for inv in pending):
                continue  # don't stack a second invite (or a second push)
            pending.append({
                "wheel_id": wheel_id,
                "from": user["id"],
                "from_name": user["name"],
                "at": now,
            })
            invited.append(target)
        if invited:
            storage.save_db(db)
            for target in invited:
                push.push_to_users(
                    [target],
                    i18n.msg('🎡 {user} shared {wheel} with you', user=user['name'], wheel=wheel['name']),
                    "Tap to accept and start spinning together!",
                    wheel,
                    tag=f"{wheel_id}-invite",
                    # land on the app root — the invitation banner sits at the
                    # top of every view, so it's right there to accept. (Not a
                    # /#wheel deep link: they aren't a member of it yet.)
                    url="/",
                )
        me = accounts.me_payload(db, user)
        me["invited"] = len(invited)
        return jsonify(me)


@app.post("/api/wheels/<wheel_id>/accept")
def accept_invite(wheel_id):
    """Accept a wheel someone shared with you — it joins your list just
    like a share code, and the invitation is spent."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        invites = user.get("invites", [])
        has_invite = any(i["wheel_id"] == wheel_id for i in invites)
        wheel = db["wheels"].get(wheel_id)
        # the invite could have been withdrawn, or the wheel emptied out,
        # between the banner rendering and the tap
        if not has_invite or wheel is None:
            user["invites"] = [i for i in invites if i["wheel_id"] != wheel_id]
            storage.save_db(db)
            abort(404, description="that invitation isn't waiting for you anymore")
        user["invites"] = [i for i in invites if i["wheel_id"] != wheel_id]
        if wheel_id not in user.get("wheels", []):
            user.setdefault("wheels", []).append(wheel_id)
        storage.save_db(db)
        push.push_to_users(
            [u for u in accounts.wheel_member_users(db, wheel_id) if u["id"] != user["id"]],
            i18n.msg('🎉 {user} joined {wheel}', user=user['name'], wheel=wheel['name']),
            "Say hi — and give the wheel a spin together!",
            wheel,
            tag=f"{wheel_id}-joined",
        )
        me = accounts.me_payload(db, user)
        me["joined"] = wheel_id  # land them on the welcome screen, like a code-join
        return jsonify(me)


@app.post("/api/wheels/<wheel_id>/decline")
def decline_invite(wheel_id):
    """Turn down a shared wheel — the invitation is dropped and the wheel
    never touches your list."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        invites = user.get("invites", [])
        if not any(i["wheel_id"] == wheel_id for i in invites):
            abort(404, description="that invitation isn't waiting for you anymore")
        user["invites"] = [i for i in invites if i["wheel_id"] != wheel_id]
        storage.save_db(db)
        return jsonify(accounts.me_payload(db, user))
