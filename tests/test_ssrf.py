"""Calendar-feed SSRF guard.

The server fetches user-supplied iCal URLs, so a feed must not be aimable
at the LAN, loopback, or the cloud-metadata endpoint. Literal IPs are used
throughout so these assertions never touch real DNS or the network.
"""
import server


def test_private_and_special_ips_are_blocked():
    for ip in ("10.0.0.1", "192.168.1.10", "172.16.5.4", "127.0.0.1",
               "169.254.169.254", "0.0.0.0", "::1", "fe80::1"):
        assert server._ip_is_private(ip) is True, ip


def test_public_ips_are_allowed():
    for ip in ("8.8.8.8", "1.1.1.1", "93.184.216.34"):
        assert server._ip_is_private(ip) is False, ip


def test_feed_host_public_rejects_private_targets():
    assert server.feed_host_is_public("https://10.0.0.1/cal.ics") is False
    assert server.feed_host_is_public("https://192.168.0.5/cal.ics") is False
    assert server.feed_host_is_public("https://169.254.169.254/latest/meta-data/") is False
    assert server.feed_host_is_public("https://127.0.0.1/private.ics") is False


def test_feed_host_public_allows_public_target():
    assert server.feed_host_is_public("https://8.8.8.8/cal.ics") is True


def test_localhost_http_dev_feed_still_allowed():
    # the intentional dev/test exemption survives the guard
    assert server.feed_host_is_public("http://localhost:8000/cal.ics") is True
    assert server.feed_host_is_public("http://127.0.0.1:8000/cal.ics") is True


def test_cached_busy_refuses_to_fetch_private_host():
    # the guard short-circuits before any network call, so this returns
    # None (no data) rather than hanging on a connection to the LAN
    assert server.cached_busy("https://169.254.169.254/latest/meta-data/") is None
    assert server.cached_busy("https://10.0.0.1/cal.ics") is None


def test_clean_feed_url_rejects_private_with_400():
    with server.app.test_request_context():
        from werkzeug.exceptions import BadRequest
        import pytest
        with pytest.raises(BadRequest):
            server.clean_feed_url("https://192.168.1.1/cal.ics")
