"""calendars — part of the Wheel of Choice server (see server.py)."""
import ipaddress
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import abort, jsonify, request

from .core import (
    ICS_MAX_BYTES,
    ICS_TIMEOUT,
    ICS_TTL,
    ICalendar,
    MAX_ICS_FEEDS,
    _lock,
    app,
    recurring_ical_events,
)
from . import i18n, accounts, core, storage


# ── Calendar availability (secret ICS feeds) ─────────────────────────
# A personal aid for date polls: each member may link their own calendar
# by its secret iCal URL (Google/Outlook/iCloud/Nextcloud all offer one).
# We fetch it read-only and work out which *evenings* are busy — shown
# only to that member, never to the rest of the wheel. Feed URLs are
# secrets: stored per user, never returned in full by any endpoint.
#
# The cache holds each feed's *digested* busy-date set (not raw events),
# keyed by URL, guarded by its own lock that is NEVER nested inside the
# db lock — network fetches happen with no lock held, like push delivery.
_cal_lock = threading.Lock()
_cal_cache = {}  # url -> {"at": epoch, "busy": set[str] | None}


def to_local(dt):
    """A VEVENT datetime in LOCAL_TZ. Floating (naive) times are read as
    already-local — the pragmatic choice for a home calendar."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=core.LOCAL_TZ)
    return dt.astimezone(core.LOCAL_TZ)


def evening_dates(start_dt, end_dt):
    """Local dates whose EVENING_FROM–midnight window the event [start,
    end) overlaps. Bounded to the poll horizon so a stray multi-day timed
    event can't blow up the loop."""
    days = set()
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=1)  # zero-length event → its start evening
    day = start_dt.date()
    last = min((end_dt - timedelta(seconds=1)).date(),
               day + timedelta(days=core.POLL_HORIZON_DAYS + 1))
    while day <= last:
        window_start = datetime.combine(day, datetime.min.time(),
                                        tzinfo=core.LOCAL_TZ).replace(hour=core.EVENING_FROM)
        window_end = datetime.combine(day, datetime.max.time(), tzinfo=core.LOCAL_TZ)
        if start_dt < window_end and end_dt > window_start:
            days.add(day.isoformat())
        day += timedelta(days=1)
    return days


def compute_busy(raw):
    """Busy evening dates (ISO strings) from raw ICS bytes, over today →
    horizon. All-day events are skipped on purpose — birthday and
    public-holiday calendars would otherwise mark whole weeks busy — as
    are CANCELLED and free/TRANSPARENT events."""
    cal = ICalendar.from_ical(raw)
    today = datetime.now(core.LOCAL_TZ).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=core.LOCAL_TZ)
    end = start + timedelta(days=core.POLL_HORIZON_DAYS + 1)
    busy = set()
    for event in recurring_ical_events.of(cal).between(start, end):
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        dtstart = event.get("DTSTART")
        if dtstart is None:
            continue
        begin = dtstart.dt
        if not isinstance(begin, datetime):
            continue  # date-typed DTSTART = all-day → skip
        dtend = event.get("DTEND")
        finish = dtend.dt if dtend is not None and isinstance(dtend.dt, datetime) else begin
        busy |= evening_dates(to_local(begin), to_local(finish))
    return busy


def cached_busy(url, fresh=False):
    """This feed's busy-date set, fetched-and-cached. Returns None only
    when the feed has never yielded readable data; a fetch failure with a
    prior good result keeps serving the stale set (better than blank for
    a home dinner poll). `fresh=True` bypasses the TTL and refetches now —
    used when a member opens a poll and wants the latest calendar."""
    now = time.time()
    if not fresh:
        with _cal_lock:
            entry = _cal_cache.get(url)
            if entry and now - entry["at"] < ICS_TTL:
                return entry["busy"]
    busy = None
    try:
        if not feed_host_is_public(url):
            # re-checked here (not just at add time) so a host that has since
            # come to resolve to a private/link-local IP can't be reached
            raise ValueError("host resolves to a non-public address")
        req = urllib.request.Request(url, headers={"User-Agent": "wheel-of-choice"})
        with urllib.request.urlopen(req, timeout=ICS_TIMEOUT) as resp:  # noqa: S310 (scheme vetted)
            raw = resp.read(ICS_MAX_BYTES + 1)
        if len(raw) > ICS_MAX_BYTES:
            raise ValueError("calendar too large")
        busy = compute_busy(raw)
    except Exception as err:
        app.logger.info("calendar fetch failed for %s…: %s", urlparse(url).hostname, err)
    with _cal_lock:
        prev = _cal_cache.get(url)
        if busy is None and prev and prev.get("busy") is not None:
            busy = prev["busy"]  # stale beats blank
        _cal_cache[url] = {"at": now, "busy": busy}
    return busy


def busy_evenings_for(feeds, fresh=False):
    """(busy date-set, readable?) across a user's feeds. `readable` is
    False only when not one feed yielded data — callers then show
    'unknown' rather than a confident 'free'. `fresh` forces a refetch,
    bypassing the per-feed TTL cache."""
    busy = set()
    readable = False
    for feed in feeds:
        result = cached_busy(feed["url"], fresh=fresh)
        if result is not None:
            busy |= result
            readable = True
    return busy, readable


def _ip_is_private(ip_str):
    """True for addresses no outbound calendar fetch has any business
    reaching — loopback, private ranges, link-local (incl. the cloud
    metadata endpoint 169.254.169.254), reserved and multicast."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → treat as unsafe
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def is_localhost_dev_feed(url):
    """The one deliberately-allowed inward feed: plain http on localhost,
    so the calendar feature stays testable on a dev box."""
    parsed = urlparse(url)
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in ("localhost", "127.0.0.1")


def feed_host_is_public(url):
    """Whether this feed URL is safe to fetch: its host must resolve to
    public address(es) only. Checked at *fetch* time (not just when the
    feed is added) so a host that later points at a private/link-local IP
    — the classic SSRF-via-DNS move — still can't be reached. The
    localhost dev feed is the one intended exception."""
    if is_localhost_dev_feed(url):
        return True
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # unresolvable → nothing to fetch anyway
    ips = {info[4][0] for info in infos}
    return bool(ips) and all(not _ip_is_private(ip) for ip in ips)


def clean_feed_url(url):
    """Normalize + vet a pasted calendar URL. webcal→https; only https
    (plus http on localhost, so the feature is testable) is allowed. SSRF
    defences stay proportionate for a home server: an https-only scheme
    allow-list, a public-host check (so it can't be aimed at the LAN or
    the cloud metadata endpoint), a size cap and a timeout."""
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not 0 < len(url) <= 500:
        abort(400, description="paste your calendar's secret iCal address")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (parsed.scheme == "https" or (parsed.scheme == "http" and host in ("localhost", "127.0.0.1"))):
        abort(400, description="that needs to be an https:// (or webcal://) calendar address")
    if not feed_host_is_public(url):
        abort(400, description="that address points at a private or local network "
                               "address I won't fetch — use a public calendar URL")
    return url


def feed_summary(feed):
    """Safe-to-share view of a feed — never the secret URL itself."""
    return {"id": feed["id"], "label": feed["label"], "host": urlparse(feed["url"]).hostname or ""}


@app.get("/api/me/calendars")
def list_calendars():
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        feeds = [feed_summary(f) for f in user.get("ics_feeds", [])]
    return jsonify({"enabled": ICalendar is not None, "feeds": feeds})


@app.post("/api/me/calendars")
def add_calendar():
    if ICalendar is None:
        abort(503, description="this server can't read calendars yet — install icalendar "
                               "and recurring-ical-events and restart (see the README)")
    payload = request.get_json(force=True, silent=True) or {}
    url = clean_feed_url(str(payload.get("url", "")))
    label = str(payload.get("label", "")).strip()[:40] or urlparse(url).hostname or "Calendar"
    # test-fetch outside the db lock — proves the URL works and warms the
    # cache, and network I/O must never block other requests on _lock
    try:
        busy = cached_busy_fresh(url)
    except Exception as err:
        abort(400, description=i18n.msg("couldn't read that calendar — {reason}", reason=i18n.reason(err)))
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        feeds = user.setdefault("ics_feeds", [])
        if any(f["url"] == url for f in feeds):
            abort(409, description="you've already linked that calendar")
        if len(feeds) >= MAX_ICS_FEEDS:
            abort(400, description=i18n.msg('{max} calendars is the limit — remove one first', max=MAX_ICS_FEEDS))
        feed = {"id": "c-" + uuid.uuid4().hex[:8], "label": label, "url": url}
        feeds.append(feed)
        storage.save_db(db)
    return jsonify({"feed": feed_summary(feed), "busy_days": len(busy)})


def cached_busy_fresh(url):
    """Fetch + parse now (bypassing the TTL), storing the result. Raises
    on failure so the add-calendar route can report why."""
    req = urllib.request.Request(url, headers={"User-Agent": "wheel-of-choice"})
    with urllib.request.urlopen(req, timeout=ICS_TIMEOUT) as resp:  # noqa: S310 (scheme vetted)
        raw = resp.read(ICS_MAX_BYTES + 1)
    if len(raw) > ICS_MAX_BYTES:
        raise ValueError("that calendar is too big")
    busy = compute_busy(raw)
    with _cal_lock:
        _cal_cache[url] = {"at": time.time(), "busy": busy}
    return busy


@app.delete("/api/me/calendars/<feed_id>")
def remove_calendar(feed_id):
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        feeds = user.get("ics_feeds", [])
        removed = next((f for f in feeds if f["id"] == feed_id), None)
        if removed:
            user["ics_feeds"] = [f for f in feeds if f["id"] != feed_id]
            storage.save_db(db)
    if removed:
        with _cal_lock:
            _cal_cache.pop(removed["url"], None)
    return "", 204


@app.get("/api/me/availability")
def availability():
    """Busy/free per date for the requesting user's own calendars, over a
    window. Only ever the caller's own feeds — a poll's other members see
    nothing but the ticks people choose to place. The poll horizon and
    evening hour ride along so the date grid can lay itself out to match
    whatever the admin has configured. `?fresh=1` bypasses the feed cache
    for an up-to-the-minute pull — the frontend sends it when a member
    opens a poll to pick evenings."""
    with _lock:
        db = storage.load_db()  # refreshes the effective poll knobs from settings
        user = accounts.current_user(db)
        feeds = [dict(f) for f in user.get("ics_feeds", [])]  # copy, then drop the lock
    # the two knobs the propose grid needs, echoed in every branch
    config = {"horizon_days": core.POLL_HORIZON_DAYS, "evening_from": core.EVENING_FROM}
    if ICalendar is None:
        return jsonify({**config, "enabled": False, "linked": False, "days": {}})
    try:
        start = datetime.strptime(request.args.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        start = datetime.now(core.LOCAL_TZ).date()
    try:
        span = max(1, min(core.POLL_HORIZON_DAYS, int(request.args.get("days", 28))))
    except (TypeError, ValueError):
        span = 28
    if not feeds:
        return jsonify({**config, "enabled": True, "linked": False, "days": {}})
    fresh = request.args.get("fresh") in ("1", "true", "yes")
    busy, readable = busy_evenings_for(feeds, fresh=fresh)  # network I/O, no _lock held
    days = {}
    for i in range(span):
        iso = (start + timedelta(days=i)).isoformat()
        days[iso] = "busy" if iso in busy else ("free" if readable else "unknown")
    return jsonify({**config, "enabled": True, "linked": True, "days": days})
