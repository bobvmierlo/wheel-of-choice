"""Translations: the Dutch file stays complete, the server speaks it."""
import json

from conftest import register
from wheelapp import core, i18n, i18n_keys, push


def test_dutch_file_covers_every_text():
    missing, broken = i18n.check("nl", i18n_keys.all_keys())
    assert not missing, f"untranslated: {missing[:10]}"
    assert not broken, f"placeholders differ: {broken[:10]}"


def test_dutch_file_has_no_stale_texts():
    messages = json.loads((core.ROOT / "locales" / "nl.json").read_text())["messages"]
    stale = sorted(set(messages) - i18n_keys.all_keys())
    assert not stale, f"no longer used: {stale[:10]}"


def test_languages_are_listed(client):
    langs = client.get("/api/i18n").get_json()["languages"]
    assert {"code": "en", "name": "English"} in langs
    assert {"code": "nl", "name": "Nederlands"} in langs


def test_errors_come_back_in_the_requested_language(client):
    r = client.get("/api/me", headers={"X-Lang": "nl"})
    assert r.get_json()["error"] == "log eerst in"
    r = client.get("/api/me", headers={"X-Lang": "xx"})  # unknown → English
    assert r.get_json()["error"] == "please log in"
    r = client.get("/api/me")
    assert r.get_json()["error"] == "please log in"


def test_interpolated_errors_are_translated(client):
    r = client.post("/api/auth/register", json={"name": "Alice", "password": "short"},
                    headers={"X-Lang": "nl"})
    assert r.get_json()["error"] == "kies een wachtwoord van minstens 8 tekens"


def test_cookie_language_for_plain_navigations(client):
    client.set_cookie(i18n.LANG_COOKIE, "nl")
    r = client.get("/api/auth/oidc/callback?state=nope")
    assert "verlopen" in r.headers["Location"]


def test_msg_translates_nested_values():
    m = i18n.msg("{weekday} {day} {month}", weekday=i18n.msg("Friday"), day=24, month=i18n.msg("Jul"))
    assert str(m) == "Friday 24 Jul"
    assert i18n.tr(m, "nl") == "vrijdag 24 jul"


def test_users_language_is_remembered_for_push(client, monkeypatch):
    s = register(client, "Alice")
    s.client.get("/api/me", headers={**s.headers, "X-Lang": "nl"})
    from wheelapp import storage
    user = storage.load_db()["users"][s.user_id]
    assert user["lang"] == "nl"
    sent = []
    monkeypatch.setattr(push, "webpush", object())
    monkeypatch.setattr(push, "vapid_public_key", lambda: "k")
    monkeypatch.setattr(push.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: sent.extend(args[0])})())
    user["push_subs"] = [{"endpoint": "https://push.example/1", "keys": {"p256dh": "a", "auth": "b"}}]
    push.push_to_users([user], i18n.msg("🎉 {user} joined {wheel}", user="Bob", wheel="Pizza"),
                       "Tap to accept and start spinning together!", {"id": "w-1"})
    payload = json.loads(sent[0][2])
    assert payload["title"] == "🎉 Bob doet mee met Pizza"
    assert payload["body"] == "Tik om te accepteren en samen te gaan draaien!"
