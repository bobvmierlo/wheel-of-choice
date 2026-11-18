"""Translations — one JSON file per language in locales/.

English is the source language: every user-facing text in the code *is*
its own translation key, written in English with {named} placeholders.
A language file maps those English texts to its own:

    locales/nl.json
    {
      "_language": "Nederlands",
      "messages": {
        "please log in": "log eerst in",
        "{name} joined {wheel}": "{name} doet mee met {wheel}"
      }
    }

Adding a language is dropping one such file into locales/ — the server
lists it automatically and the language menu offers it. Anything missing
from a file simply shows in English. `python3 -m wheelapp.i18n_keys check xx`
lists what a file still lacks; the test suite keeps nl.json complete.

The same files serve the browser (i18n.js fetches /locales/<code>.json)
and the server, which translates its error messages into the language of
the request, and push notifications into each recipient's language.
"""
import json
import re
import threading

from flask import jsonify, request
from werkzeug.exceptions import HTTPException

from .core import ROOT, app

LOCALES_DIR = ROOT / "locales"
SOURCE_LANG = "en"
LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
LANG_COOKIE = "wheel_lang"
_cache = {}  # code -> (mtime, {"name": ..., "messages": {...}})
_cache_lock = threading.Lock()


class Msg(str):
    """A user-facing text with placeholders still attached, so it can be
    translated later (when we know who reads it). Behaves as the English
    string everywhere else."""

    def __new__(cls, template, **values):
        text = template.format(**{k: str(v) for k, v in values.items()}) if values else template
        obj = super().__new__(cls, text)
        obj.template = template
        obj.values = values
        return obj


def msg(template, **values):
    return Msg(template, **values)


def _load(code):
    path = LOCALES_DIR / f"{code}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _cache_lock:
        hit = _cache.get(code)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = {"name": str(raw.get("_language") or code),
                "messages": {str(k): str(v) for k, v in (raw.get("messages") or {}).items()}}
    except (ValueError, AttributeError):
        return None
    with _cache_lock:
        _cache[code] = (mtime, data)
    return data


def languages():
    """Every language on offer: English plus one per locale file."""
    out = [{"code": SOURCE_LANG, "name": "English"}]
    if LOCALES_DIR.is_dir():
        for path in sorted(LOCALES_DIR.glob("*.json")):
            code = path.stem
            if LANG_RE.match(code) and code != SOURCE_LANG:
                data = _load(code)
                if data:
                    out.append({"code": code, "name": data["name"]})
    return out


def clean_lang(code):
    """A supported language code, or '' for anything else."""
    code = str(code or "").strip()
    if code == SOURCE_LANG:
        return code
    if not LANG_RE.match(code) or _load(code) is None:
        return ""
    return code


def tr(text, lang):
    """`text` (a plain string or a Msg) in language `lang`. Unknown texts
    and languages fall back to English."""
    template = getattr(text, "template", str(text))
    values = getattr(text, "values", {})
    catalog = _load(lang) if lang and lang != SOURCE_LANG else None
    translated = (catalog or {}).get("messages", {}).get(template, template)
    if not values:
        return translated
    filled = {k: tr(v, lang) if isinstance(v, Msg) else str(v) for k, v in values.items()}
    try:
        return translated.format(**filled)
    except (KeyError, IndexError, ValueError):  # a broken translation — fall back
        return template.format(**filled)


def request_lang():
    """The language the current request wants: the app sends X-Lang on
    API calls; plain navigations (the SSO bounce) carry it in a cookie."""
    return clean_lang(request.headers.get("X-Lang")) or clean_lang(request.cookies.get(LANG_COOKIE))


def t(text):
    """`text` in the current request's language."""
    return tr(text, request_lang())


def reason(err):
    """The message of an exception raised with a Msg, still translatable."""
    return err.args[0] if err.args and isinstance(err.args[0], str) else str(err)


@app.errorhandler(HTTPException)
def as_json(err):
    """The frontend shows API error messages to the user — send JSON, in
    the user's language."""
    return jsonify(error=t(err.description)), err.code


@app.get("/api/i18n")
def list_languages():
    return jsonify({"languages": languages()})


# ── Checking a language file ─────────────────────────────────────────
def placeholders(text):
    return sorted(set(re.findall(r"\{(\w+)\}", text)))


def check(code, keys):
    """(missing, broken) for language `code` against the English `keys`:
    texts without a translation, and translations whose {placeholders}
    differ from the English."""
    messages = (_load(code) or {}).get("messages", {})
    missing = sorted(k for k in keys if k not in messages)
    broken = sorted(k for k in keys if k in messages
                    and placeholders(k) != placeholders(messages[k]))
    return missing, broken
