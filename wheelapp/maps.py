"""maps — part of the Wheel of Choice server (see server.py)."""
import json
import urllib.error
import urllib.request

from flask import abort, jsonify, request

from .core import (
    _lock,
    app,
)
from . import accounts, storage


# ── Restaurant location lookup (Google Places, optional) ─────────────
# With an admin-supplied Google Maps Platform key (Places API enabled),
# the restaurant add/edit form can search for a place by name or address
# and pin the exact one. We store that place on the destination so its
# formatted address rides straight into calendar invites. All calls are
# proxied here so the key never reaches the browser. The host is fixed
# (Google), so there's no SSRF surface as there is with user ICS URLs.
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = ("places.id,places.displayName,places.formattedAddress,"
                     "places.location,places.googleMapsUri")
PLACES_TIMEOUT = 8  # seconds to wait on Google before giving up
PLACES_MAX_RESULTS = 6


def places_text_search(query, api_key):
    """Google Places (New) text search → a trimmed list of candidate
    places. Raises on transport/HTTP errors for the caller to translate."""
    body = json.dumps({"textQuery": query,
                       "maxResultCount": PLACES_MAX_RESULTS}).encode("utf-8")
    req = urllib.request.Request(
        PLACES_SEARCH_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": PLACES_FIELD_MASK,
            "User-Agent": "wheel-of-choice",
        },
    )
    with urllib.request.urlopen(req, timeout=PLACES_TIMEOUT) as resp:  # noqa: S310 (fixed Google host)
        data = json.loads(resp.read(1_000_000).decode("utf-8"))
    results = []
    for p in data.get("places", [])[:PLACES_MAX_RESULTS]:
        loc = p.get("location") or {}
        results.append({
            "id": str(p.get("id", "")),
            "name": (p.get("displayName") or {}).get("text", ""),
            "address": p.get("formattedAddress", ""),
            "lat": loc.get("latitude"),
            "lng": loc.get("longitude"),
            "url": p.get("googleMapsUri", ""),
        })
    return results


@app.get("/api/maps/search")
def maps_search():
    """Proxy a place search for the add/edit form. Any signed-in member may
    use it; the key stays server-side. 503 when no key is configured."""
    with _lock:
        db = storage.load_db()
        accounts.current_user(db)
        key = accounts.maps_api_key(db)
    if not key:
        abort(503, description="restaurant search isn't set up on this server")
    query = (request.args.get("q") or "").strip()[:200]
    if len(query) < 2:
        return jsonify({"results": []})
    try:
        results = places_text_search(query, key)
    except urllib.error.HTTPError as err:
        app.logger.info("places search HTTP %s", err.code)
        abort(502, description="maps search failed — the server's API key may be "
                               "invalid, or the Places API isn't enabled for it")
    except Exception as err:  # noqa: BLE001 — network/parse errors all become one message
        app.logger.info("places search failed: %s", err)
        abort(502, description="couldn't reach Google Maps just now — try again")
    return jsonify({"results": results})
