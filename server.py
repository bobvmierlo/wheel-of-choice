#!/usr/bin/env python3
"""Flask server for Wheel of Choice.

Serves the static site plus a small JSON API. Since v3 the app is built
around stand-alone *wheels*: every wheel is its own little world — a
list of things to spin for, a spin history, and its own share code. A
user can have any number of wheels, and a wheel can be shared with any
number of accounts: joining with a wheel's code adds that one wheel to
your list, nothing else. Personal filter preferences are stored per
user per wheel, so partners sharing a wheel each keep their own filters.

There are three kinds of wheel:
  - "holidays"    : holiday destinations — countries and larger regions
                    (seeded from the catalogue via onboarding questions)
  - "citytrips"   : city trips in and around Europe (seeded the same way)
  - "restaurants" : a fully custom list — no seeding, you add every
                    entry yourself, and there are no vetoes: what the
                    wheel says, goes.

Travel wheels carry the current *round*: which destinations have been
vetoed (every member gets exactly one veto per round, tracked by user
id) and the pick that is waiting for the partner's thumbs-up. A pick
only lands in the history once everyone who could still veto has had
their say. The round is kept on the server so all members' devices see
the same thing. Restaurant wheels skip all of that — an accepted spin
goes straight into the history.

The first account ever registered becomes the admin; admins can promote
other users, delete accounts, and pull a user out of shared wheels (see
the /api/admin endpoints).

Storage: data/db.json next to this file by default; override the
directory with the WHEEL_DATA_DIR environment variable (the systemd
unit sets it to /var/lib/wheel-of-choice). Old databases are migrated
automatically: v2 *spaces* are split into independent wheels (the old
space code keeps working — it now joins the holidays wheel), and the
even older single-household layout becomes unclaimed wheels that the
first registered account can adopt.

The code lives in the wheelapp/ package, one module per area (storage,
accounts, oidc, push, calendars, wheels, rounds, entries, polls, admin,
backup, …); this file is just the entry point.

Run directly for a quick start:

    python3 server.py            # serves on http://0.0.0.0:8000
"""
import os

from wheelapp import app  # importing the package registers every route


def serve():
    """Run the server: waitress (a production-grade, pure-Python WSGI
    server) when it's installed, else Flask's built-in development server
    — fine for trying things out, not meant to face the internet.
    WHEEL_DEV_SERVER=1 forces the development server (auto-reload-free,
    but with Flask's debugging-friendly error output)."""
    from wheelapp import backup
    backup.start_backup_worker()  # daily backups into data/backups/
    # HOST/PORT default to 0.0.0.0:8000; overriding them via the
    # environment lets the container (and reverse proxies) place the app
    # wherever they like without editing this file.
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        waitress_serve = None
    if waitress_serve and os.environ.get("WHEEL_DEV_SERVER") != "1":
        print(f"Wheel of Choice on http://{host}:{port} (waitress)", flush=True)
        waitress_serve(app, host=host, port=port, threads=8, ident="wheel-of-choice")
    else:
        print("waitress isn't installed — using Flask's development server", flush=True)
        app.run(host=host, port=port)


if __name__ == "__main__":
    serve()
