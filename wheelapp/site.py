"""site — part of the Wheel of Choice server (see server.py)."""

from flask import send_from_directory

from .core import (
    ROOT,
    app,
)


# ── Static site ──────────────────────────────────────────────────────
@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/<path:filename>")
def assets(filename):
    # send_from_directory refuses paths that escape ROOT
    return send_from_directory(ROOT, filename)
