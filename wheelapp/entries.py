"""entries — part of the Wheel of Choice server (see server.py)."""
import csv
import io
import json
import re
from datetime import date, datetime, timezone

from flask import Response, abort, jsonify, request

from .core import (
    HISTORY_STATUSES,
    MAX_BULK_ADD,
    ROAM_DISTANCES,
    _lock,
    app,
)
from . import i18n, accounts, polls, push, seeding, storage, validation


def name_key(name):
    """How two entry names are compared for duplicates: case and spacing
    don't count, so "Pho 88" and " pho  88" are the same place."""
    return " ".join(str(name).split()).casefold()


def entry_label(dest):
    return " ".join(part for part in (dest.get("flag", ""), dest["name"]) if part)


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
                request.get_json(force=True, silent=True), wheel_type=wheel["type"]
            )
        except ValueError as err:
            abort(400, description=i18n.reason(err))
        if any(name_key(d["name"]) == name_key(dest["name"]) for d in wheel["destinations"]):
            abort(409, description=i18n.msg('{name} is already on this wheel', name=dest["name"]))
        wheel["destinations"].append(dest)
        storage.save_db(db)
        label = entry_label(dest)
        push.push_to_users(
            [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            i18n.msg('➕ {user} added {label}', user=user['name'], label=label),
            i18n.msg('Fresh on {wheel} — worth a star? ⭐', wheel=wheel['name']),
            wheel,
            tag=f"{wheel['id']}-add",  # don't overwrite a spin waiting for a thumbs-up
        )
    return jsonify(dest), 201


# A line of a pasted list: an optional leading emoji, then the name.
LEADING_EMOJI = re.compile(r"^((?:[^\w\s\d]|\uFE0F|\u200D)+)\s+(.+)$")


def parse_bulk_line(line):
    """'🍕 Luigi's' → ('🍕', "Luigi's"); 'Luigi's' → ('', "Luigi's")."""
    line = " ".join(str(line).split())
    line = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", line)  # a pasted bullet or numbered list
    m = LEADING_EMOJI.match(line)
    # only a real emoji counts as a flag — "& Co" or "- item" stay names
    if m and any(ord(ch) > 0x2000 for ch in m.group(1)) and not any(ch.isalnum() for ch in m.group(1)):
        return m.group(1), m.group(2)
    return "", line


@app.post("/api/wheels/<wheel_id>/destinations/bulk")
def bulk_add(wheel_id):
    """Add a whole pasted list at once — one entry per line, optionally
    starting with an emoji. Blank lines and names already on the wheel
    (or twice in the paste) are skipped; one notification covers it all."""
    payload = request.get_json(force=True, silent=True) or {}
    lines = payload.get("lines")
    if isinstance(lines, str):
        lines = lines.splitlines()
    if not isinstance(lines, list):
        abort(400, description="paste at least one name")
    tags = payload.get("tags", [])
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        seen = {name_key(d["name"]) for d in wheel["destinations"]}
        added, skipped = [], []
        for line in lines:
            flag, name = parse_bulk_line(line)
            if not name:
                continue
            if name_key(name[:60]) in seen:
                skipped.append(name[:60])
                continue
            if len(added) >= MAX_BULK_ADD:
                abort(400, description=i18n.msg("that's a long list — {max} at a time, please", max=MAX_BULK_ADD))
            item = {"name": name}
            if flag:
                item["flag"] = flag
            if tags and not storage.is_travel(wheel):
                item["tags"] = tags
            try:
                dest = validation.clean_destination(item, wheel_type=wheel["type"])
            except ValueError as err:
                abort(400, description=i18n.reason(err))
            seen.add(name_key(dest["name"]))
            added.append(dest)
        if not added and not skipped:
            abort(400, description="paste at least one name")
        if added:
            wheel["destinations"].extend(added)
            storage.save_db(db)
            push.push_to_users(
                [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
                i18n.msg('➕ {user} added {count} entries to {wheel}', user=user['name'], count=len(added), wheel=wheel['name'])
                if len(added) > 1 else i18n.msg('➕ {user} added {label}', user=user['name'], label=entry_label(added[0])),
                i18n.msg('Fresh on {wheel} — worth a star? ⭐', wheel=wheel['name']),
                wheel,
                tag=f"{wheel['id']}-add",
            )
        return jsonify({"added": added, "skipped": skipped, "destinations": wheel["destinations"]}), 201


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
                i18n.msg('🌍 {user} added {count} new place(s) to {wheel}', user=user['name'], count=len(added), wheel=wheel['name']),
                i18n.msg("Fresh from the catalogue — {count} already on the wheel ⭐", count=on)
                if on else "Fresh from the catalogue ⭐",
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
                    updated = validation.clean_destination(payload, existing=dest, wheel_type=wheel["type"])
                except ValueError as err:
                    abort(400, description=i18n.reason(err))
                if any(name_key(d["name"]) == name_key(updated["name"])
                       for d in destinations if d["id"] != dest_id):
                    abort(409, description=i18n.msg('{name} is already on this wheel', name=updated["name"]))
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


def clean_iso_date(value, field):
    """'' (clears it) or a real YYYY-MM-DD date."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        abort(400, description=i18n.msg("that {field} isn't a date I understand", field=i18n.msg(field)))


@app.put("/api/wheels/<wheel_id>/history/<entry_id>")
def update_history(wheel_id, entry_id):
    """Life after the spin: mark a past pick as booked or been-there and
    note the trip dates. Marking it visited also takes the destination off
    the wheel (it stays in the manage list, just unticked) — and taking
    "been there" back puts it on again, if that's what took it off."""
    payload = request.get_json(force=True, silent=True) or {}
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        entry = next((e for e in wheel["history"] if e.get("id") == entry_id), None)
        if entry is None:
            abort(404, description="that history entry no longer exists")
        was_visited = entry.get("status") == "visited"
        if "status" in payload:
            status = str(payload["status"] or "")
            if status and status not in HISTORY_STATUSES:
                abort(400, description="unknown trip status")
            if status:
                entry["status"] = status
            else:
                entry.pop("status", None)
        for field, label in (("trip_date", "trip date"), ("trip_end", "end date")):
            if field in payload:
                value = clean_iso_date(payload[field], label)
                if value:
                    entry[field] = value
                else:
                    entry.pop(field, None)
        if entry.get("trip_end") and (not entry.get("trip_date") or entry["trip_end"] < entry["trip_date"]):
            abort(400, description="the trip can't end before it starts")
        destination = next(
            (d for d in wheel["destinations"] if d["id"] == entry.get("dest_id")), None
        )
        visited = entry.get("status") == "visited"
        changed_dest = None
        if destination is not None:
            if visited and not was_visited and destination["enabled"]:
                destination["enabled"] = False
                entry["took_off_wheel"] = True  # remember, so un-marking undoes it
                changed_dest = destination
            elif was_visited and not visited and entry.pop("took_off_wheel", False):
                if not destination["enabled"]:
                    destination["enabled"] = True
                    changed_dest = destination
        if not visited:
            entry.pop("took_off_wheel", None)
        storage.save_db(db)
        return jsonify({"history": polls.history_view(db, wheel, user), "destination": changed_dest})


@app.delete("/api/wheels/<wheel_id>/history/<entry_id>")
def delete_history_entry(wheel_id, entry_id):
    """Drop one pick from the history (a mis-tap, a test spin) — its date
    poll goes with it. The rest of the history and the round are untouched."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        before = len(wheel["history"])
        wheel["history"] = [e for e in wheel["history"] if e.get("id") != entry_id]
        if len(wheel["history"]) == before:
            abort(404, description="that history entry no longer exists")
        storage.save_db(db)
        return jsonify({"history": polls.history_view(db, wheel, user)})


@app.delete("/api/wheels/<wheel_id>/history")
def clear_history(wheel_id):
    with _lock:
        db = storage.load_db()
        wheel = validation.get_wheel(db, accounts.current_user(db), wheel_id)
        wheel["history"] = []
        wheel["round"] = storage.empty_round()  # clean slate all round
        storage.save_db(db)
    return "", 204


# ── Exports ──────────────────────────────────────────────────────────
def safe_filename(text):
    return re.sub(r"[^\w-]+", "-", text.lower()).strip("-") or "wheel"


def history_rows(db, wheel):
    for e in wheel["history"]:
        poll = e.get("poll") or {}
        yield {
            "picked": (e.get("date") or "")[:10],
            "flag": e.get("flag", ""),
            "name": e.get("name", ""),
            "picked_by": e.get("by", ""),
            "status": e.get("status", ""),
            "date": e.get("trip_date", "") or poll.get("locked_date") or "",
            "end": e.get("trip_end", ""),
        }


@app.get("/api/wheels/<wheel_id>/export/history.csv")
def export_history_csv(wheel_id):
    """The spin history as a spreadsheet — your travel log or dinner diary."""
    with _lock:
        db = storage.load_db()
        wheel = validation.get_wheel(db, accounts.current_user(db), wheel_id)
        rows = list(history_rows(db, wheel))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["picked", "flag", "name", "picked_by", "status", "date", "end"])
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        "\ufeff" + out.getvalue(),  # BOM: lets Excel read the emoji/accents right
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename(wheel["name"])}-history.csv"'},
    )


def ics_escape(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


@app.get("/api/wheels/<wheel_id>/export/history.ics")
def export_history_ics(wheel_id):
    """Every dated pick as a calendar: trips as all-day events (start to
    end), locked dinners as an evening. Undated picks are left out."""
    with _lock:
        db = storage.load_db()
        wheel = validation.get_wheel(db, accounts.current_user(db), wheel_id)
        entries = list(wheel["history"])
        travel = storage.is_travel(wheel)
        locations = {d["id"]: validation.dest_calendar_location(d) for d in wheel["destinations"]}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Wheel of Choice//EN",
             f"X-WR-CALNAME:{ics_escape(wheel['name'])}"]
    for e in entries:
        start = e.get("trip_date") or (e.get("poll") or {}).get("locked_date")
        if not start:
            continue
        try:
            first = date.fromisoformat(start)
        except ValueError:
            continue
        lines += ["BEGIN:VEVENT", f"UID:{e['id']}@wheel-of-choice", f"DTSTAMP:{stamp}",
                  f"SUMMARY:{ics_escape(' '.join(p for p in (e.get('flag', ''), e.get('name', '')) if p))}"]
        if travel or not storage.is_food(wheel):
            try:
                last = date.fromisoformat(e.get("trip_end") or start)
            except ValueError:
                last = first
            end = date.fromordinal(max(last, first).toordinal() + 1)  # DTEND is exclusive
            lines += [f"DTSTART;VALUE=DATE:{first:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
        else:
            lines += [f"DTSTART:{first:%Y%m%d}T190000", f"DTEND:{first:%Y%m%d}T210000"]
            spot = locations.get(e.get("dest_id"), "")
            if spot:
                lines.append(f"LOCATION:{ics_escape(e.get('name', '') + ', ' + spot)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return Response(
        "\r\n".join(lines) + "\r\n",
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename(wheel["name"])}.ics"'},
    )


@app.get("/api/wheels/<wheel_id>/export/wheel.json")
def export_wheel(wheel_id):
    """One wheel — its entries and history — as a JSON file any member can
    keep. Unlike the admin backup it holds no accounts, codes or secrets."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel = validation.get_wheel(db, user, wheel_id)
        names = {u["id"]: u["name"] for u in db["users"].values()}

        def public_dest(d):
            out = {k: v for k, v in d.items() if k not in ("starred_by", "favorite")}
            out["starred_by"] = sorted(names[s] for s in d.get("starred_by", []) if s in names)
            return out

        data = {
            "format": "wheel-of-choice/wheel",
            "exported": datetime.now(timezone.utc).isoformat(),
            "name": wheel["name"],
            "type": wheel["type"],
            "members": accounts.wheel_members(db, wheel["id"]),
            "destinations": [public_dest(d) for d in wheel["destinations"]],
            "history": [
                {k: v for k, v in e.items() if k not in ("poll", "took_off_wheel")}
                for e in wheel["history"]
            ],
        }
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename(wheel["name"])}.json"'},
    )
