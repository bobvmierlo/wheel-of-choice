"""rounds — part of the Wheel of Choice server (see server.py)."""
import uuid
from datetime import datetime, timezone

from flask import abort, jsonify, request

from .core import (
    HISTORY_LIMIT,
    _lock,
    app,
)
from . import accounts, polls, push, storage, validation


# ── Rounds (shared vetoes + pending pick, travel wheels only) ────────
def round_data(wheel):
    """The wheel's current round; older databases don't have one yet."""
    rnd = wheel.setdefault("round", storage.empty_round())
    rnd.setdefault("vetoes", {})
    rnd.setdefault("pending", None)
    return rnd


def pending_blockers(db, wheel, rnd):
    """Members who can still stop the pending pick: everyone on the
    wheel except the picker who has neither confirmed it nor spent
    their veto. With more than two members the pick stays pending until
    this list is empty — every voice gets heard, not just the first to
    answer."""
    p = rnd["pending"]
    confirmed = set(p.get("confirmed", []))
    return [
        u for u in accounts.wheel_member_users(db, wheel["id"])
        if u["id"] != p["by"] and u["id"] not in confirmed and u["id"] not in rnd["vetoes"]
    ]


def round_payload(db, user, wheel):
    """Round state as one user sees it — 'my' fields are personalised.
    Restaurant wheels have no vetoes, so their round is always blank."""
    rnd = round_data(wheel)
    pending = None
    if rnd["pending"]:
        p = rnd["pending"]
        pending = {k: v for k, v in p.items() if k not in ("by", "confirmed")}
        pending["mine"] = p["by"] == user["id"]
        pending["i_confirmed"] = user["id"] in p.get("confirmed", [])
        pending["waiting_names"] = sorted(
            u["name"] for u in pending_blockers(db, wheel, rnd)
        )
    return {
        "vetoed_ids": sorted(set(rnd["vetoes"].values())),
        "my_veto_used": user["id"] in rnd["vetoes"],
        "vetoes_used": len(rnd["vetoes"]),
        "members": len(accounts.wheel_member_users(db, wheel["id"])),
        "pending": pending,
    }


def finalize_pick(wheel, dest_id, name, flag, by_name):
    entry = {
        "id": "h-" + uuid.uuid4().hex[:10],
        "dest_id": dest_id,  # lets the frontend link back to the destination's info
        "name": name,
        "flag": flag,
        "date": datetime.now(timezone.utc).isoformat(),
        "by": by_name,
    }
    wheel["history"] = ([entry] + wheel["history"])[:HISTORY_LIMIT]
    wheel["round"] = storage.empty_round()  # a decision closes the round


@app.get("/api/wheels/<wheel_id>/round")
def get_round(wheel_id):
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        return jsonify(round_payload(db, user, validation.get_wheel(db, user, wheel_id)))


@app.post("/api/wheels/<wheel_id>/round/veto")
def veto_destination(wheel_id):
    payload = request.get_json(force=True, silent=True) or {}
    dest_id = str(payload.get("dest_id", ""))
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        if not storage.is_travel(wheel):
            abort(400, description="this wheel has no vetoes — the wheel's word is final")
        rnd = round_data(wheel)
        if user["id"] in rnd["vetoes"]:
            abort(409, description="you've already used your veto this round")
        if not any(d["id"] == dest_id for d in wheel["destinations"]):
            abort(404, description="destination not found")
        rnd["vetoes"][user["id"]] = dest_id
        shot_down = rnd["pending"] if rnd["pending"] and rnd["pending"]["dest_id"] == dest_id else None
        if shot_down:
            rnd["pending"] = None  # the veto shoots down the waiting pick
        storage.save_db(db)
        if shot_down:
            push.push_to_users(
                [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
                f"🙅 {user['name']} vetoed {shot_down['flag']} {shot_down['name']}",
                f"Back to square one on {wheel['name']} — give it another spin!",
                wheel,
            )
        return jsonify(round_payload(db, user, wheel))


@app.post("/api/wheels/<wheel_id>/round/pick")
def propose_pick(wheel_id):
    """The spinner accepted a result. On travel wheels it only becomes
    history once every member who could still veto has had the chance —
    until then it is the round's pending pick. Restaurant wheels have
    no vetoes, so the pick lands in the history straight away."""
    payload = request.get_json(force=True, silent=True) or {}
    dest_id = str(payload.get("dest_id", ""))
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        rnd = round_data(wheel)
        dest = next((d for d in wheel["destinations"] if d["id"] == dest_id), None)
        if dest is None:
            abort(404, description="destination not found")
        if storage.is_travel(wheel):
            if dest_id in rnd["vetoes"].values():
                abort(409, description="that destination was vetoed this round")
            can_still_veto = any(
                u["id"] != user["id"] and u["id"] not in rnd["vetoes"]
                for u in accounts.wheel_member_users(db, wheel["id"])
            )
            if can_still_veto:
                rnd["pending"] = {
                    "dest_id": dest["id"],
                    "name": dest["name"],
                    "flag": dest["flag"],
                    "by": user["id"],
                    "by_name": user["name"],
                    "confirmed": [],
                    "date": datetime.now(timezone.utc).isoformat(),
                }
                storage.save_db(db)
                push.push_to_users(
                    pending_blockers(db, wheel, rnd),
                    f"🎡 {user['name']} spun {dest['flag']} {dest['name']}",
                    f"{wheel['name']}: are you in — or is this your veto?",
                    wheel,
                )
                return jsonify({"final": False, "round": round_payload(db, user, wheel)})
        finalize_pick(wheel, dest["id"], dest["name"], dest["flag"], user["name"])
        storage.save_db(db)
        push.push_to_users(
            [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            f"🎉 {dest['flag']} {dest['name']} it is!",
            f"{user['name']} spun {wheel['name']} — time to book! 🧳" if storage.is_travel(wheel)
            else f"{user['name']} spun {wheel['name']} — enjoy your meal! 🍽️",
            wheel,
        )
        return jsonify({
            "final": True,
            "history": polls.history_view(db, wheel, user),
            "round": round_payload(db, user, wheel),
        })


@app.post("/api/wheels/<wheel_id>/round/confirm")
def confirm_pick(wheel_id):
    """A member okays the pending pick. With three or four people on the
    wheel, the pick only becomes history once *everyone* who could
    still veto has given their thumbs-up."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        rnd = round_data(wheel)
        pending = rnd["pending"]
        if not pending:
            abort(404, description="no pick is waiting for a thumbs-up")
        if pending["by"] == user["id"]:
            abort(400, description="the others have to confirm this one")
        confirmed = pending.setdefault("confirmed", [])
        if user["id"] not in confirmed:
            confirmed.append(user["id"])
        blockers = pending_blockers(db, wheel, rnd)
        if blockers:
            storage.save_db(db)
            # only the spinner needs the play-by-play — the others get
            # pinged once the pick is final
            spinner = db["users"].get(pending["by"])
            push.push_to_users(
                [spinner] if spinner else [],
                f"👍 {user['name']} is in for {pending['flag']} {pending['name']}",
                f"Still waiting for {' & '.join(sorted(u['name'] for u in blockers))} · {wheel['name']}",
                wheel,
            )
            return jsonify({"final": False, "round": round_payload(db, user, wheel)})
        finalize_pick(wheel, pending["dest_id"], pending["name"], pending["flag"], pending["by_name"])
        storage.save_db(db)
        push.push_to_users(
            [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            f"🎉 {pending['flag']} {pending['name']} it is!",
            f"Everyone's in on {wheel['name']} — time to book! 🧳",
            wheel,
        )
        return jsonify({
            "final": True,
            "history": polls.history_view(db, wheel, user),
            "round": round_payload(db, user, wheel),
        })
