"""Restaurant location search (Google Places) and the place data we keep."""
from conftest import register, make_restaurant_wheel, make_travel_wheel


def set_key(admin, key):
    resp = admin.put("/api/admin/settings", json={"google_maps_api_key": key})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# ── The admin API key & the maps_enabled flag ────────────────────────
def test_maps_disabled_by_default(client):
    s = register(client, "Alice")
    assert s.get("/api/me").get_json()["maps_enabled"] is False


def test_admin_sets_and_clears_the_key(client):
    admin = register(client, "Alice")
    view = set_key(admin, "AIzaTESTKEY")
    assert view["google_maps_api_key"] == "AIzaTESTKEY"
    assert admin.get("/api/me").get_json()["maps_enabled"] is True
    # the raw key comes back on the admin settings GET so it stays editable
    assert admin.get("/api/admin/settings").get_json()["google_maps_api_key"] == "AIzaTESTKEY"
    # an empty value switches search back off
    view = set_key(admin, "")
    assert view["google_maps_api_key"] == ""
    assert admin.get("/api/me").get_json()["maps_enabled"] is False


def test_key_never_leaks_to_non_admins(client):
    admin = register(client, "Alice")
    set_key(admin, "AIzaSECRET")
    bob = register(client, "Bob")
    me = bob.get("/api/me").get_json()
    assert me["maps_enabled"] is True          # Bob can tell search is available
    assert "google_maps_api_key" not in me     # …but never sees the key itself
    assert bob.get("/api/admin/settings").status_code == 403


# ── The search proxy ─────────────────────────────────────────────────
def test_search_requires_a_key(client):
    s = register(client, "Alice")
    assert s.get("/api/maps/search?q=pizza").status_code == 503


def test_short_query_returns_no_results(client):
    admin = register(client, "Alice")
    set_key(admin, "AIzaTEST")
    body = admin.get("/api/maps/search?q=a").get_json()
    assert body == {"results": []}


def test_search_proxies_places(client, monkeypatch):
    import server
    admin = register(client, "Alice")
    set_key(admin, "AIzaTEST")

    captured = {}

    def fake_search(query, api_key):
        captured["query"] = query
        captured["api_key"] = api_key
        return [{"id": "abc", "name": "Trattoria", "address": "Main St 1, Springfield",
                 "lat": 1.0, "lng": 2.0, "url": "https://maps.google.com/?cid=1"}]

    monkeypatch.setattr(server, "places_text_search", fake_search)
    body = admin.get("/api/maps/search?q=trattoria springfield").get_json()
    assert captured == {"query": "trattoria springfield", "api_key": "AIzaTEST"}
    assert body["results"][0]["name"] == "Trattoria"


def test_search_reports_upstream_failure(client, monkeypatch):
    import server
    admin = register(client, "Alice")
    set_key(admin, "AIzaTEST")

    def boom(query, api_key):
        raise RuntimeError("network down")

    monkeypatch.setattr(server, "places_text_search", boom)
    assert admin.get("/api/maps/search?q=pizza place").status_code == 502


# ── Persisting the chosen place on a restaurant ──────────────────────
def test_place_persisted_and_used_for_calendar(client):
    import server
    s = register(client, "Alice")
    wid, _ = make_restaurant_wheel(s)
    place = {"id": "abc", "name": "Trattoria", "address": "Main St 1, Springfield",
             "lat": 1.5, "lng": 2.5, "url": "https://maps.google.com/?cid=42"}
    created = s.post(f"/api/wheels/{wid}/destinations",
                     json={"name": "Trattoria", "location": "typed address", "place": place})
    assert created.status_code == 201
    dest = created.get_json()
    assert dest["place"]["id"] == "abc"
    assert dest["place"]["url"] == "https://maps.google.com/?cid=42"
    # the exact place address wins over the free-typed one for the calendar
    assert server.dest_calendar_location(dest) == "Main St 1, Springfield"


def test_clean_place_drops_junk():
    import server
    # a non-https url is dropped; bool "coords" are rejected; only useful keys survive
    cleaned = server.clean_place({"id": "x", "name": "P", "address": "A st",
                                  "url": "javascript:alert(1)", "lat": True, "lng": "nope"})
    assert cleaned["url"] == ""
    assert cleaned["lat"] is None and cleaned["lng"] is None
    assert cleaned["id"] == "x"
    # an empty-ish place collapses to None
    assert server.clean_place({"url": "https://x"}) is None
    assert server.clean_place("not a dict") is None


def test_travel_destinations_ignore_place(client):
    s = register(client, "Alice")
    wid, _ = make_travel_wheel(s)
    resp = s.post(f"/api/wheels/{wid}/destinations",
                  json={"name": "Lisbon", "place": {"id": "z", "address": "somewhere"}})
    assert resp.status_code == 201
    assert "place" not in resp.get_json()
