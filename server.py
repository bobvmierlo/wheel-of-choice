#!/usr/bin/env python3
"""Tiny Flask server for the Wheel of Wander static site.

Run directly for a quick start:

    python3 server.py            # serves on http://0.0.0.0:8000

Or via the bundled systemd unit for a permanent setup — see
deploy/holiday-picker.service and the README's deployment section.
"""
from pathlib import Path

from flask import Flask, send_from_directory

ROOT = Path(__file__).parent.resolve()
app = Flask(__name__)


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/<path:filename>")
def assets(filename):
    # send_from_directory refuses paths that escape ROOT
    return send_from_directory(ROOT, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
