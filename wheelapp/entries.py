"""entries — part of the Wheel of Choice server (see server.py)."""

from flask import abort, jsonify, request

from .core import (
    HISTORY_STATUSES,
    ROAM_DISTANCES,
    _lock,
    app,
)
from . import accounts, polls, push, seeding, storage, validation


# ── Destinations API ─────────────────────────────────────────────────
@app.get("/api/wheels/<wheel_id>/destinations")
def list_destinations(wheel_id):
    with _lock:
        db = storage.load_db()
        return jsonify(validation.get_wheel(db, accounts.current_user(db), wheel_id)["destinations"])


@app.post("/api/wheels/<wheel_id>/destinations")
def create_destination(wheel_id):
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        try:
            dest = validation.clean_destination(
                request.get_json(force=True, silent=True), travel=storage.is_travel(wheel)
            )
        except ValueError as err:
            abort(400, description=str(err))
        wheel["destinations"].append(dest)
        storage.save_db(db)
        label = " ".join(part for part in (dest.get("flag", ""), dest["name"]) if part)
        push.push_to_users(
            [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            f"➕ {user['name']} added {label}",
            f"Fresh on {wheel['name']} — worth a star? ⭐",
            wheel,
            tag=f"{wheel['id']}-add",  # don't overwrite a spin waiting for a thumbs-up
        )
    return jsonify(dest), 201


@app.post("/api/wheels/<wheel_id>/destinations/sync")
def sync_catalog(wheel_id):
    """Top the wheel up with catalogue places it doesn't have yet — the
    ones added to the seed since this wheel was created. New rows follow
    the same rules as first-time seeding: island/regional gems land *on*
    for this wheel's home, niche picks come in discoverable but off, and
    everything already on the wheel (including hand-added entries and
    edits) is left untouched."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        if not storage.is_travel(wheel):
            abort(400, description="only travel wheels are seeded from a catalogue")
        home, roam = seeding.wheel_home_roam(wheel)
        stamped = seeding.stamp_home_roam(wheel)  # make the wheel self-describing
        allowed = ROAM_DISTANCES[roam]
        have = {d["id"] for d in wheel["destinations"]}
        added = []
        for entry in storage.catalog(wheel["type"]):
            if entry["id"] in have:
                continue
            distance = seeding.catalog_distance(entry, home)
            added.append(seeding.build_catalog_destination(entry, distance, allowed))
        if added:
            wheel["destinations"].extend(added)
        if added or stamped:
            storage.save_db(db)
        if added:
            on = sum(1 for d in added if d["enabled"])
            push.push_to_users(
                [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
                f"🌍 {user['name']} added {len(added)} new place(s) to {wheel['name']}",
                f"Fresh from the catalogue{f' — {on} already on the wheel' if on else ''} ⭐",
                wheel,
                tag=f"{wheel['id']}-add",
            )
    return jsonify({"added": len(added), "destinations": wheel["destinations"]})


@app.put("/api/wheels/<wheel_id>/destinations/<dest_id>")
def update_destination(wheel_id, dest_id):
    payload = request.get_json(force=True, silent=True)
    # "starred" toggles *this user's* star — handled here because
    # clean_destination doesn't know who is asking
    starred = payload.pop("starred", None) if isinstance(payload, dict) else None
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        destinations = wheel["destinations"]
        for i, dest in enumerate(destinations):
            if dest["id"] == dest_id:
                try:
                    updated = validation.clean_destination(payload, existing=dest, travel=storage.is_travel(wheel))
                except ValueError as err:
                    abort(400, description=str(err))
                if starred is not None:
                    stars = set(updated["starred_by"])
                    stars.add(user["id"]) if starred else stars.discard(user["id"])
                    updated["starred_by"] = sorted(stars)
                    # touching the star replaces any legacy shared star
                    updated["favorite"] = bool(stars)
                destinations[i] = updated
                storage.save_db(db)
                return jsonify(updated)
    abort(404, description="destination not found")


@app.delete("/api/wheels/<wheel_id>/destinations/<dest_id>")
def delete_destination(wheel_id, dest_id):
    with _lock:
        db = storage.load_db()
        wheel = validation.get_wheel(db, accounts.current_user(db), wheel_id)
        before = len(wheel["destinations"])
        wheel["destinations"] = [d for d in wheel["destinations"] if d["id"] != dest_id]
        if len(wheel["destinations"]) == before:
            abort(404, description="destination not found")
        storage.save_db(db)
    return "", 204


# ── History API ──────────────────────────────────────────────────────
@app.get("/api/wheels/<wheel_id>/history")
def list_history(wheel_id):
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        return jsonify(polls.history_view(db, validation.get_wheel(db, user, wheel_id), user))


@app.put("/api/wheels/<wheel_id>/history/<entry_id>")
def update_history(wheel_id, entry_id):
    """Life after the spin: mark a past pick as booked or been-there and
    note the trip date. Marking it visited also takes the destination off
    the wheel (it stays in the manage list, just unticked)."""
    payload = request.get_json(force=True, silent=True) or {}
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        entry = next((e for e in wheel["history"] if e.get("id") == entry_id), None)
        if entry is None:
            abort(404, description="that history entry no longer exists")
        if "status" in payload:
            status = str(payload["status"] or "")
            if status and status not in HISTORY_STATUSES:
                abort(400, description="unknown trip status")
            if status:
                entry["status"] = status
            else:
                entry.pop("status", None)
        if "trip_date" in payload:
            trip_date = str(payload["trip_date"] or "").strip()[:20]
            if trip_date:
                entry["trip_date"] = trip_date
            else:
                entry.pop("trip_date", None)
        destination = None
        if entry.get("status") == "visited":
            destination = next(
                (d for d in wheel["destinations"] if d["id"] == entry.get("dest_id")), None
            )
            if destination is not None and destination["enabled"]:
                destination["enabled"] = False
        storage.save_db(db)
        return jsonify({"history": polls.history_view(db, wheel, user), "destination": destination})


@app.delete("/api/wheels/<wheel_id>/history")
def clear_history(wheel_id):
    with _lock:
        db = storage.load_db()
        wheel = validation.get_wheel(db, accounts.current_user(db), wheel_id)
        wheel["history"] = []
        wheel["round"] = storage.empty_round()  # clean slate all round
        storage.save_db(db)
    return "", 204
