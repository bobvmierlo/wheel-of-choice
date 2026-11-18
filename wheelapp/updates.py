"""updates — part of the Wheel of Choice server (see server.py)."""
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from flask import abort, jsonify, request

from .core import (
    APP_VERSION,
    DATA_DIR,
    DEPLOYMENT,
    ROOT,
    UPDATE_FLAG,
    _lock,
    app,
)
from . import admin_api, storage


SERVER_STARTED = datetime.now(timezone.utc).isoformat()


def git_version():
    """Short hash, date and subject of the checked-out commit. All None
    when git or the repo isn't available (e.g. a tarball deploy). The
    safe.directory override is needed under systemd: the repo is owned
    by root while the service runs as an unprivileged DynamicUser."""
    none = {"commit": None, "commit_date": None, "commit_subject": None}
    try:
        out = subprocess.run(
            ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT),
             "log", "-1", "--format=%h%n%cI%n%s"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return none
    lines = out.stdout.strip().split("\n") if out.returncode == 0 else []
    if len(lines) < 3:
        return none
    return {"commit": lines[0], "commit_date": lines[1], "commit_subject": lines[2]}


GIT_VERSION = git_version()

# Update check: ask the git remote whether the tracked branch has moved past
# the commit we're running. Cached briefly so the admin panel can poll often
# without hammering GitHub (or blocking on the network every time).
UPDATE_CHECK_TTL = 5 * 60  # seconds a remote-HEAD lookup is reused
_update_check_lock = threading.Lock()
_update_check_cache = {"at": 0.0, "data": None}


def _git(*args, timeout=10):
    """Run a git command in the repo, returning stripped stdout or None."""
    try:
        out = subprocess.run(
            ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


GITHUB_REPO = "bobvmierlo/wheel-of-choice"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"


def _parse_semver(v):
    """'v3.2.0' → (3, 2, 0). Pads to three parts; () on parse failure so a
    malformed tag sorts below any real version instead of raising."""
    parts_str = (v or "").lstrip("v").strip().split(".")
    try:
        parts = [int(x) for x in parts_str]
    except ValueError:
        return ()
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _release_update_check():
    """Is a newer GitHub *release* available than the version we're running?
    Used for container deployments, which have no git checkout to diff — we
    compare our baked-in APP_VERSION to the latest release tag."""
    off = {"checked": False, "update_available": False, "deployment": DEPLOYMENT}
    req = urllib.request.Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"wheel-of-choice/{APP_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return off  # offline, rate-limited, or no releases yet
    tag = (data.get("tag_name") or "").strip()
    latest = tag.lstrip("v") or APP_VERSION
    return {
        "checked": True,
        "update_available": _parse_semver(latest) > _parse_semver(APP_VERSION),
        "current": APP_VERSION,
        "latest": latest,
        "release_url": data.get("html_url") or RELEASES_PAGE_URL,
        "deployment": DEPLOYMENT,
    }


def _git_update_check():
    """Is the tracked branch on the remote ahead of what we're running?
    Compares the local HEAD sha to the remote branch's sha via ls-remote
    (a lightweight network call — no fetch, nothing written to the repo).
    Used for source/systemd checkouts, where the update button pulls it."""
    off = {"checked": False, "update_available": False, "deployment": DEPLOYMENT}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    local = _git("rev-parse", "HEAD")
    if not branch or not local or branch == "HEAD":
        return off  # no git, or a detached checkout we can't track
    remote = _git("ls-remote", "origin", branch, timeout=15)
    if remote is None:
        return off  # remote unreachable — offline, or no 'origin'
    remote_sha = remote.split()[0] if remote.split() else ""
    if not remote_sha:
        return off
    return {
        "checked": True,
        "update_available": remote_sha != local,
        "branch": branch,
        "current": local[:9],
        "latest": remote_sha[:9],
        "deployment": DEPLOYMENT,
    }


def compute_update_check():
    """Whether a newer version is waiting. Containers can't git-pull, so they
    diff against GitHub releases; source checkouts track their branch."""
    if DEPLOYMENT == "docker":
        return _release_update_check()
    return _git_update_check()


def update_check(force=False):
    """compute_update_check() behind a short TTL cache, so frequent admin
    polls collapse into at most one remote lookup per UPDATE_CHECK_TTL."""
    now = time.time()
    with _update_check_lock:
        cached = _update_check_cache["data"]
        if not force and cached and now - _update_check_cache["at"] < UPDATE_CHECK_TTL:
            return cached
    data = compute_update_check()  # network I/O, no lock held
    with _update_check_lock:
        _update_check_cache["at"] = time.time()
        _update_check_cache["data"] = data
    return data


@app.post("/api/admin/update")
def admin_update():
    """Ask the host to update to the latest version. The Flask process is
    sandboxed and unprivileged (see deploy/wheel-of-choice.service), so it
    can't run `sudo git pull` itself — it drops a flag file in the state
    directory instead, and the root-level wheel-of-choice-update.path unit
    (see deploy/) picks it up, pulls, and restarts this service."""
    with _lock:
        db = storage.load_db()
        admin_api.require_admin(db)
        if DEPLOYMENT == "docker":
            # No git checkout and no systemd updater in a container — updating
            # means pulling a newer image. The UI hides the button here, so
            # this only guards a hand-rolled request.
            abort(400, description="This is a container deployment — update by "
                  "pulling a newer image (docker compose pull && docker compose up -d).")
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            UPDATE_FLAG.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        except OSError:
            abort(500, description="could not write the update request")
    with _update_check_lock:  # a pull is coming — don't keep flagging it stale
        _update_check_cache["at"] = 0.0
        _update_check_cache["data"] = None
    return jsonify({"requested": True}), 202


@app.get("/api/admin/update-check")
def admin_update_check():
    """Whether the git remote's tracked branch is ahead of what we run.
    Admin-only and polled by the panel; the result is cached server-side
    (UPDATE_CHECK_TTL) so frequent polls don't hit the network each time.
    Pass ?force=1 to bypass the cache for an on-demand recheck."""
    with _lock:
        db = storage.load_db()
        admin_api.require_admin(db)
    force = request.args.get("force") in ("1", "true", "yes")
    return jsonify(update_check(force=force))


@app.get("/api/version")
def version():
    """What's running right now. The frontend shows the commit + date in
    the UI and polls this after an update request: server_started changes
    once the updater has restarted us, and the commit moves when the pull
    actually brought something new. update_pending reports whether the
    flag file is still waiting to be picked up."""
    return jsonify({
        **GIT_VERSION,
        "version": APP_VERSION,
        "deployment": DEPLOYMENT,
        "server_started": SERVER_STARTED,
        "update_pending": UPDATE_FLAG.exists(),
    })
