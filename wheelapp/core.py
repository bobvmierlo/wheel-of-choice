"""Shared configuration, the Flask app and runtime settings (see server.py)."""
import os
import threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

try:  # push notifications are optional — everything else works without
    from pywebpush import WebPushException, webpush
except ImportError:
    webpush = None
    WebPushException = Exception

try:  # calendar availability is optional — date polls work fully without
    from icalendar import Calendar as ICalendar
    import recurring_ical_events
except ImportError:
    ICalendar = None
    recurring_ical_events = None


ROOT = Path(__file__).parent.parent.resolve()

# Version number — the single source of truth lives in __version__.py, which
# the release workflow bumps on every published release.
try:
    from __version__ import __version__ as APP_VERSION
except Exception:  # pragma: no cover — missing/corrupt file shouldn't crash boot
    APP_VERSION = "unknown"


def deployment_type():
    """How this instance was deployed, which decides how it updates:

      "docker" — the published container image; updates by pulling a newer
                 image, so the in-app git updater is hidden.
      "source" — a plain git/systemd checkout; the "Update & restart" button
                 pulls the branch and restarts (see deploy/).

    An explicit WHEEL_DEPLOYMENT env var wins; otherwise we sniff the two
    signals the container image leaves behind (Docker's /.dockerenv, and the
    image pinning WHEEL_DATA_DIR to /data)."""
    forced = os.environ.get("WHEEL_DEPLOYMENT")
    if forced:
        return forced
    if os.path.exists("/.dockerenv") or os.environ.get("WHEEL_DATA_DIR") == "/data":
        return "docker"
    return "source"


DEPLOYMENT = deployment_type()

# HOLIDAY_DATA_DIR is the pre-rename name — still honoured so a git pull
# under an old unit file can't silently start with an empty database
DATA_DIR = Path(
    os.environ.get("WHEEL_DATA_DIR")
    or os.environ.get("HOLIDAY_DATA_DIR", str(ROOT / "data"))
)
DB_FILE = DATA_DIR / "db.json"
UPDATE_FLAG = DATA_DIR / "update-requested"

# Web Push (see the README's notifications section). The VAPID keypair
# identifies this server to the browsers' push relays; it's generated on
# first use and lives next to the database — losing it silently breaks
# every existing subscription, so it's part of what a backup should hold.
VAPID_KEY_FILE = DATA_DIR / "vapid-private-key.pem"
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")
PUSH_TTL = 12 * 3600  # seconds the relay keeps an undelivered push — a spin is stale news after that
MAX_PUSH_SUBS = 10  # devices per user; the oldest subscription falls off
MAX_INVITE_BATCH = 20  # people you can share a wheel with in one go

WHEEL_TYPES = {
    "holidays": {"label": "Holidays", "travel": True, "seed": ROOT / "seed-destinations.json"},
    "citytrips": {"label": "City trips", "travel": True, "seed": ROOT / "seed-citytrips.json"},
    "restaurants": {"label": "Restaurants", "travel": False, "seed": None},
}
TRAVEL_TYPES = [t for t, meta in WHEEL_TYPES.items() if meta["travel"]]

BUDGETS = {"low", "mid", "high"}
DISTANCES = {"regional", "europe", "longhaul"}
VIBES = {"nature", "culture", "food", "beach", "nightlife", "adventure", "wellness", "winter"}
SEASONS = {"spring", "summer", "autumn", "winter"}
PARTIES = {"couple", "group"}

# Onboarding vocabulary
HOME_REGIONS = {"northwest", "british", "nordic", "south", "central"}
ROAM_DISTANCES = {
    "close": {"regional"},
    "europe": {"regional", "europe"},
    "anywhere": set(DISTANCES),
}
BUDGET_STYLES = BUDGETS | {"mix"}

HISTORY_LIMIT = 50
MAX_SEED_FAVORITES = 8  # per wheel, when onboarding picks favourites
HISTORY_STATUSES = {"booked", "visited"}  # a pick's life after the spin
SESSION_TTL_DAYS = 90  # log-ins older than this expire

# Date polls (restaurant wheels): once a pick lands, members coordinate an
# evening. A poll lives on the history entry, so it syncs, dies and clears
# with the entry. The evening window is what "busy" means for a dinner.
POLL_MAX_DATES = 10  # candidate dates a proposer may put up

# Three knobs are admin-tunable at runtime (Admin → Date polls), stored in
# the db under "settings": the timezone busy evenings are judged in, the
# hour an evening starts, and how far ahead a poll reaches. The env vars
# below are only the *defaults* used until an admin overrides them — an
# admin change wins and persists.
EVENING_FROM_MIN, EVENING_FROM_MAX = 0, 23  # a local hour
HORIZON_MIN, HORIZON_MAX = 7, 365  # days a poll can reach ahead


def clamp_evening_from(value, fallback):
    """Local hour (0–23) a dinner evening starts — everything from here to
    midnight counts as busy. A missing or bogus value falls back."""
    try:
        return min(EVENING_FROM_MAX, max(EVENING_FROM_MIN, int(value)))
    except (TypeError, ValueError):
        return fallback


def clamp_horizon(value, fallback):
    """How far ahead (in days) a poll and the calendar lookahead reach.
    A missing or bogus value falls back."""
    try:
        return min(HORIZON_MAX, max(HORIZON_MIN, int(value)))
    except (TypeError, ValueError):
        return fallback


def clean_timezone(value, fallback):
    """A valid IANA zone name (e.g. 'Europe/London'); anything unknown or
    malformed falls back. Busy evenings and poll dates are judged in it."""
    if not value:
        return fallback
    try:
        ZoneInfo(str(value))  # raises unless it names a real zone
        return str(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return fallback


# Env-var defaults (WHEEL_TZ, WHEEL_EVENING_FROM, WHEEL_POLL_HORIZON_DAYS)
# seed the settings; the built-in fallbacks are Amsterdam, 17:00 and 60 days.
DEFAULT_TZ = clean_timezone(os.environ.get("WHEEL_TZ"), "Europe/Amsterdam")
DEFAULT_EVENING_FROM = clamp_evening_from(os.environ.get("WHEEL_EVENING_FROM"), 17)
DEFAULT_POLL_HORIZON_DAYS = clamp_horizon(os.environ.get("WHEEL_POLL_HORIZON_DAYS"), 60)

# Effective values in force right now. load_db() refreshes these from the
# stored settings on every request, so they always mirror what the admin
# last saved (and survive restarts and restores).
LOCAL_TZ = ZoneInfo(DEFAULT_TZ)
EVENING_FROM = DEFAULT_EVENING_FROM
POLL_HORIZON_DAYS = DEFAULT_POLL_HORIZON_DAYS

# Every IANA zone name, computed once — the admin timezone picker's options.
ALL_ZONES = sorted(available_timezones())


def settings_view(db):
    """The admin-tunable poll settings, plus the defaults to reset to and
    the bounds the admin UI needs. Kept lean — apply_runtime_settings runs
    it on every db load, so the full zone list lives in the GET endpoint."""
    stored = db.get("settings") or {}
    return {
        "timezone": clean_timezone(stored.get("timezone"), DEFAULT_TZ),
        "evening_from": clamp_evening_from(stored.get("evening_from"), DEFAULT_EVENING_FROM),
        "poll_horizon_days": clamp_horizon(stored.get("poll_horizon_days"), DEFAULT_POLL_HORIZON_DAYS),
        # Open by default so existing servers behave exactly as before.
        "registration_open": bool(stored.get("registration_open", True)),
        "defaults": {
            "timezone": DEFAULT_TZ,
            "evening_from": DEFAULT_EVENING_FROM,
            "poll_horizon_days": DEFAULT_POLL_HORIZON_DAYS,
        },
        "bounds": {
            "evening_from": [EVENING_FROM_MIN, EVENING_FROM_MAX],
            "poll_horizon_days": [HORIZON_MIN, HORIZON_MAX],
        },
    }


def apply_runtime_settings(db):
    """Mirror the stored poll settings into the module-level globals the
    rest of the server reads. Cheap enough to run on every db load
    (ZoneInfo instances are cached, so rebuilding LOCAL_TZ is nearly free)."""
    global LOCAL_TZ, EVENING_FROM, POLL_HORIZON_DAYS
    view = settings_view(db)
    LOCAL_TZ = ZoneInfo(view["timezone"])
    EVENING_FROM = view["evening_from"]
    POLL_HORIZON_DAYS = view["poll_horizon_days"]

# Personal calendar feeds (secret ICS URLs) power the poll's busy/free hints.
# Read-only, per user, never shown to anyone else — see the README.
MAX_ICS_FEEDS = 4  # feeds per account
ICS_TTL = 20 * 60  # seconds a fetched calendar's busy-set is cached
ICS_TIMEOUT = 8  # seconds to wait on a feed before giving up
ICS_MAX_BYTES = 2_000_000  # refuse calendars larger than this

app = Flask(__name__)
_lock = threading.Lock()

# Cap request bodies so a malformed or malicious client can't make the
# server buffer an unbounded payload in memory. The only legitimately
# large body is an admin database restore, and a db.json anywhere near
# this size would be an enormous instance — real requests stay far under.
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

# A tiny, self-contained page: everything it loads comes from its own
# origin (bar the emoji favicon, a data: URI). The policy below says
# exactly that, so an injected <script> or framing attempt has nowhere
# to go. Kept in one place; applied to every response below.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
))


@app.after_request
def security_headers(response):
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.errorhandler(HTTPException)
def as_json(err):
    """The frontend shows API error messages to the user — send JSON."""
    return jsonify(error=err.description), err.code
