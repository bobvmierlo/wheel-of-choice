"""Security hardening: response headers and request-body limits."""
import server
from conftest import register


def test_security_headers_on_static_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "object-src 'none'" in resp.headers["Content-Security-Policy"]


def test_security_headers_on_api(client):
    resp = client.get("/api/version")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in resp.headers


def test_oversized_body_is_rejected(client):
    """A body past the configured cap gets a 413, not an OOM."""
    assert server.app.config["MAX_CONTENT_LENGTH"] == 16 * 1024 * 1024
    huge = "x" * (server.app.config["MAX_CONTENT_LENGTH"] + 1)
    resp = client.post("/api/auth/register",
                       data=huge, content_type="application/json")
    assert resp.status_code == 413


def test_normal_body_still_accepted(client):
    # sanity: a routine request is nowhere near the cap
    s = register(client, "Alice")
    assert s.get("/api/me").status_code == 200
