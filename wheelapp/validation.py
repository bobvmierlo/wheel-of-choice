"""validation — part of the Wheel of Choice server (see server.py)."""
import uuid

from flask import abort

from .core import (
    BUDGETS,
    DISTANCES,
    MAX_TAGS,
    PARTIES,
    SEASONS,
    VIBES,
)
from . import storage


# ── Validation ───────────────────────────────────────────────────────
def clean_links(values, fallback):
    """User-supplied reading links; anything that isn't http(s) is dropped."""
    if not isinstance(values, list):
        return fallback
    links = []
    for item in values[:12]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()[:300]
        if not url.startswith(("http://", "https://")):
            continue
        label = str(item.get("label", "")).strip()[:60]
        links.append({"label": label or url, "url": url})
    return links


def clean_place(place):
    """Validate the structured Google place a restaurant may carry, or
    None. Only the fields we display or drop into a calendar are kept; a
    dict with nothing useful in it collapses to None. The URL must be a
    plain https link (it's rendered as an anchor)."""
    if not isinstance(place, dict):
        return None

    def text(key, cap):
        return str(place.get(key, "") or "").strip()[:cap]

    def coord(key):
        v = place.get(key)
        # reject bools (isinstance(True, int) is True) and non-numbers
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    url = text("url", 500)
    if not url.startswith("https://"):
        url = ""
    cleaned = {
        "id": text("id", 300),
        "name": text("name", 200),
        "address": text("address", 300),
        "lat": coord("lat"),
        "lng": coord("lng"),
        "url": url,
    }
    if not (cleaned["id"] or cleaned["address"] or cleaned["name"]):
        return None
    return cleaned


def dest_calendar_location(dest):
    """The best address for a restaurant's calendar entry and map link:
    the exact Google-resolved address when we have one, else the address
    the member typed. Empty string when neither is set."""
    if not dest:
        return ""
    place = dest.get("place") or {}
    return place.get("address") or dest.get("location") or ""


def clean_tags(values, fallback):
    """Free-form tags on a restaurant or custom entry ("pizza", "takeaway",
    "2 players"): trimmed, lower-cased, deduplicated, MAX_TAGS at most."""
    if not isinstance(values, list):
        return list(fallback or [])
    tags = []
    for value in values:
        tag = " ".join(str(value).split()).lower()[:24]
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= MAX_TAGS:
            break
    return tags


DEFAULT_FLAGS = {"restaurants": "🍽️", "custom": "🎯"}


def clean_destination(payload, existing=None, wheel_type="holidays"):
    """Normalise and validate a destination payload; raises ValueError.
    Restaurant and custom entries skip the travel-only fields — they're
    a name, an emoji, tags, notes and links (restaurants add a location)."""
    base = existing or {}
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")

    name = str(payload.get("name", base.get("name", ""))).strip()[:60]
    if not name:
        raise ValueError("name is required")

    travel = wheel_type not in DEFAULT_FLAGS
    default_flag = DEFAULT_FLAGS.get(wheel_type, "📍")
    dest = {
        "id": base.get("id") or "d-" + uuid.uuid4().hex[:10],
        "name": name,
        "flag": (str(payload.get("flag", base.get("flag", default_flag))).strip() or default_flag)[:28],
        "favorite": bool(payload.get("favorite", base.get("favorite", False))),
        # who starred it is only changed via the "starred" toggle in the
        # update endpoint, never straight from a payload
        "starred_by": [s for s in base.get("starred_by", []) if isinstance(s, str)],
        "enabled": bool(payload.get("enabled", base.get("enabled", True))),
        "notes": str(payload.get("notes", base.get("notes", ""))).strip()[:1000],
        "links": clean_links(payload.get("links", base.get("links", [])), base.get("links", [])),
    }
    if not travel:
        dest["tags"] = clean_tags(payload.get("tags", base.get("tags", [])), base.get("tags", []))
    if wheel_type == "custom":
        return dest
    if not travel:
        # the restaurant's address — free text, or the formatted address of
        # a Google place picked in the form; it rides into the calendar
        # event once a date locks
        dest["location"] = str(payload.get("location", base.get("location", ""))).strip()[:200]
        # the exact Google place behind that address, when one was chosen —
        # its precise address/link power the calendar buttons and map link
        dest["place"] = clean_place(payload.get("place", base.get("place")))
        return dest

    def subset(key, allowed, fallback):
        values = payload.get(key, base.get(key, fallback))
        if not isinstance(values, list):
            return fallback
        values = [v for v in values if v in allowed]
        return values or fallback

    budget = payload.get("budget", base.get("budget", "mid"))
    distance = payload.get("distance", base.get("distance", "europe"))
    dest.update({
        "budget": budget if budget in BUDGETS else "mid",
        "distance": distance if distance in DISTANCES else "europe",
        "vibes": subset("vibes", VIBES, ["nature"]),
        "seasons": subset("seasons", SEASONS, sorted(SEASONS)),
        "party": subset("party", PARTIES, sorted(PARTIES)),
    })
    return dest


def get_wheel(db, user, wheel_id):
    wheel = db["wheels"].get(wheel_id)
    if wheel is None or wheel_id not in user.get("wheels", []):
        abort(404, description="that wheel isn't in your list")
    # History entries predating trip statuses have no id — backfill (and
    # persist) so the status endpoint can address them.
    changed = False
    for entry in wheel["history"]:
        if not entry.get("id"):
            entry["id"] = "h-" + uuid.uuid4().hex[:10]
            changed = True
    if changed:
        storage.save_db(db)
    return wheel
