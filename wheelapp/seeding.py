"""seeding — part of the Wheel of Choice server (see server.py)."""

from flask import jsonify

from .core import (
    HOME_REGIONS,
    MAX_SEED_FAVORITES,
    ROAM_DISTANCES,
    TRAVEL_TYPES,
    _lock,
    app,
)
from . import accounts, storage


# ── Wheel seeding (travel wheels only) ───────────────────────────────
def catalog_distance(entry, home):
    """A catalogue entry's distance relative to a home region: long-haul
    stays put, an entry that is 'near' the home is regional, the rest is
    Europe."""
    if entry["distance"] == "longhaul":
        return "longhaul"
    if home in entry.get("near", []):
        return "regional"
    return "europe"


def build_catalog_destination(entry, distance, allowed):
    """One wheel destination built from a catalogue entry — shared by
    first-time seeding and the later 'add new catalogue places' sync so
    both land identical rows (regional gems on, niche picks discoverable
    but off)."""
    return {
        "id": entry["id"],
        "name": entry["name"],
        "flag": entry["flag"],
        "budget": entry["budget"],
        "distance": distance,
        "vibes": entry["vibes"],
        "seasons": entry["seasons"],
        "party": entry["party"],
        "favorite": False,
        "starred_by": [],
        "enabled": distance in allowed
        and (entry.get("enabled", True) or distance == "regional"),
        "notes": entry.get("notes", ""),
        "links": entry.get("links") or storage.default_links(entry["name"]),
    }


def wheel_home_roam(wheel):
    """Best guess of the onboarding home region and roam range for a
    travel wheel, so a catalogue sync can promote regional gems the same
    way creation did. Wheels created since this feature store the answers
    outright; older ones are reverse-engineered from the destinations
    already on the wheel — distance is a clean signal (favourites force a
    place *on* but never change its distance)."""
    if wheel.get("home") in HOME_REGIONS and wheel.get("roam") in ROAM_DISTANCES:
        return wheel["home"], wheel["roam"]
    index = {e["id"]: e for e in storage.catalog(wheel["type"])}
    seeded = [(d, index[d["id"]]) for d in wheel["destinations"] if d["id"] in index]
    # home: the region whose recomputed distances best fit what's stored
    home, best = None, 0
    for candidate in HOME_REGIONS:
        score = sum(1 for d, e in seeded
                    if d.get("distance") == catalog_distance(e, candidate))
        if score > best:
            home, best = candidate, score
    # roam: the widest band that is live on the wheel, favourites aside
    live = {d.get("distance") for d, _ in seeded
            if d.get("enabled") and not d.get("starred_by") and not d.get("favorite")}
    if "longhaul" in live:
        roam = "anywhere"
    elif "europe" in live:
        roam = "europe"
    else:
        roam = "close"
    return home, roam


def stamp_home_roam(wheel):
    """Persist the inferred home/roam onto a travel wheel so it stops
    relying on inference. Returns True if anything changed. Home is only
    written when it can be inferred confidently (roam always can be)."""
    home, roam = wheel_home_roam(wheel)
    changed = False
    if home in HOME_REGIONS and wheel.get("home") != home:
        wheel["home"] = home
        changed = True
    if roam in ROAM_DISTANCES and wheel.get("roam") != roam:
        wheel["roam"] = roam
        changed = True
    return changed


def backfill_home_roam(db):
    """One-time pass that stamps home/roam onto travel wheels created
    before we stored them. Runs once (guarded by a db marker) — wheels
    made since always carry the answers from creation."""
    for wheel in db.get("wheels", {}).values():
        if storage.is_travel(wheel):
            stamp_home_roam(wheel)
    db["home_roam_backfilled"] = True


def seed_destinations(wheel_type, home, roam, vibes, budget, favorites, user_id):
    """Build one travel wheel from its catalogue, tailored to the answers.

    - distance is recomputed relative to `home` (each catalogue entry
      lists the home regions it is "regional" for; long-haul stays put)
    - destinations beyond the chosen roam range are kept but disabled,
      so they stay discoverable in the manage panel
    - catalogue entries marked "enabled": false are niche picks that also
      start off the wheel (a full catalogue would drown it in segments) —
      *unless* they're regional from this home: a local gem like
      Maastricht belongs on a Benelux wheel but not on a Spanish one
    - entries matching the chosen vibes (and budget) get pre-starred
    - `favorites` (a list of catalogue ids) are places named during
      creation: starred *by this user* and enabled even beyond the roam
      range — they asked for it. Stars are per user (starred_by); a
      place starred by several members gets an even bigger segment.
    """
    allowed = ROAM_DISTANCES[roam]
    destinations = []
    for entry in storage.catalog(wheel_type):
        distance = catalog_distance(entry, home)
        destinations.append(build_catalog_destination(entry, distance, allowed))
    if vibes:
        scored = []
        for dest in destinations:
            if not dest["enabled"]:
                continue
            matches = len(set(dest["vibes"]) & set(vibes))
            score = matches * 2 + (1 if budget in ("mix", dest["budget"]) else 0)
            # at least two vibe matches, or one match plus a budget fit
            if matches and score >= 3:
                scored.append((score, dest))
        scored.sort(key=lambda pair: -pair[0])
        for _, dest in scored[:MAX_SEED_FAVORITES]:
            dest["favorite"] = True
    picked = set(favorites or [])
    for dest in destinations:
        if dest["id"] in picked:
            dest["favorite"] = True
            dest["enabled"] = True
            if user_id:
                dest["starred_by"] = [user_id]
    return destinations


@app.get("/api/catalog")
def catalog_overview():
    """Names of everything in the seed catalogues, per travel wheel type
    — lets the wheel-creation screen offer a favourites picker. Unknown
    ids sent back with the answers are simply ignored."""
    with _lock:
        db = storage.load_db()
        accounts.current_user(db)
    return jsonify({
        wheel_type: [{"id": e["id"], "name": e["name"], "flag": e["flag"]}
                     for e in storage.catalog(wheel_type)]
        for wheel_type in TRAVEL_TYPES
    })
