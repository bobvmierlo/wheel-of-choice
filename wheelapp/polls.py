"""polls — part of the Wheel of Choice server (see server.py)."""
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from flask import abort, jsonify, request

from .core import (
    POLL_MAX_DATES,
    _lock,
    app,
)
from . import i18n, accounts, core, push, storage, validation


# ── Date polls (restaurant wheels) ───────────────────────────────────
# After a restaurant pick lands in the history, any member can open a
# Doodle-style poll on it: the proposer puts up a handful of evenings,
# everyone ticks what works, and a date everyone ticked can be locked
# in. The poll lives *on* the history entry, so it rides the existing
# 5-second history poll, dies when the entry is deleted, and clears with
# the history — no separate lifecycle to keep in sync.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def pretty_date(iso):
    """'2026-07-24' → 'Friday 24 Jul', as a translatable Msg (weekday and
    month get translated too). Falls back to the raw string."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(iso)
    return i18n.msg("{weekday} {day} {month}", weekday=i18n.msg(WEEKDAYS[d.weekday()]),
                    day=d.day, month=i18n.msg(MONTHS[d.month - 1]))


def clean_poll_dates(values):
    """Validate a proposer's candidate dates: ISO YYYY-MM-DD, no past,
    within the horizon, deduped and sorted, 2–POLL_MAX_DATES of them."""
    if not isinstance(values, list):
        abort(400, description="pick a few evenings first")
    today = datetime.now(core.LOCAL_TZ).date()
    horizon = today + timedelta(days=core.POLL_HORIZON_DAYS)
    dates = set()
    for value in values:
        try:
            day = datetime.strptime(str(value), "%Y-%m-%d").date()
        except ValueError:
            abort(400, description="that's not a date I understand")
        if day < today:
            abort(400, description="those evenings are in the past — pick dates still to come")
        if day > horizon:
            abort(400, description=i18n.msg('keep it within the next {days} days', days=core.POLL_HORIZON_DAYS))
        dates.add(day.isoformat())
    dates = sorted(dates)
    if len(dates) < 2:
        abort(400, description="put up at least two evenings to choose between")
    if len(dates) > POLL_MAX_DATES:
        abort(400, description=i18n.msg("that's a lot of evenings — {max} at most", max=POLL_MAX_DATES))
    return dates


def restaurant_poll_entry(db, user, wheel_id, entry_id):
    """The (wheel, history entry) a poll route targets. Polls are a
    restaurant thing; travel picks track a trip date via trip status."""
    wheel = validation.get_wheel(db, user, wheel_id)
    if storage.is_travel(wheel):
        abort(400, description="date polls are a restaurant thing — travel picks use trip status")
    entry = next((e for e in wheel["history"] if e.get("id") == entry_id), None)
    if entry is None:
        abort(404, description="that pick is no longer in the history")
    return wheel, entry


def poll_unanimous(members, poll):
    """Dates every *current* member ticked — recomputed live so members
    joining or leaving mid-poll self-heal (a newcomer must vote before
    anything is unanimous; a leaver's votes simply stop counting)."""
    votes = poll.get("votes", {})
    return [d for d in poll["dates"]
            if members and all(d in votes.get(u["id"], ()) for u in members)]


def poll_view(db, wheel, entry, user):
    """One member's view of a poll: their own vote, everyone's ticks by
    name, which dates are unanimous, and who's still to vote. Names are
    resolved here (never stored in votes), so renames and departures need
    no rewrite of stored data."""
    poll = entry.get("poll")
    if not poll:
        return None
    members = accounts.wheel_member_users(db, wheel["id"])
    names = {u["id"]: u["name"] for u in members}
    votes = poll.get("votes", {})
    return {
        "id": poll["id"],
        "status": poll["status"],
        "dates": poll["dates"],
        "by_name": db["users"].get(poll["by"], {}).get("name", "someone"),
        "locked_date": poll.get("locked_date"),
        "locked_by_name": (db["users"].get(poll["locked_by"], {}).get("name")
                           if poll.get("locked_by") else None),
        "my_dates": votes.get(user["id"]),  # None → this member hasn't voted yet
        "votes": sorted(
            ({"name": names[uid], "dates": ds, "mine": uid == user["id"]}
             for uid, ds in votes.items() if uid in names),
            key=lambda v: v["name"].lower(),
        ),
        "unanimous": poll_unanimous(members, poll),
        "waiting_names": sorted(names[u["id"]] for u in members if u["id"] not in votes),
        "members": len(members),
    }


def history_view(db, wheel, user):
    """History with each entry's poll serialized for this viewer. Every
    endpoint that hands back history routes through here, so the client
    always sees one shape."""
    return [
        {**entry, "poll": poll_view(db, wheel, entry, user)} if entry.get("poll") else entry
        for entry in wheel["history"]
    ]


@app.post("/api/wheels/<wheel_id>/history/<entry_id>/poll")
def start_poll(wheel_id, entry_id):
    dates = clean_poll_dates((request.get_json(force=True, silent=True) or {}).get("dates"))
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel, entry = restaurant_poll_entry(db, user, wheel_id, entry_id)
        if entry.get("poll"):
            abort(409, description="there's already a date poll on this pick — "
                                   "vote there, or scrap it first")
        entry["poll"] = {
            "id": "p-" + uuid.uuid4().hex[:8],
            "status": "open",
            "by": user["id"],
            "created": datetime.now(timezone.utc).isoformat(),
            "dates": dates,
            # the proposer ticks all their own dates — they'd hardly put up
            # evenings they can't do; they can untick afterwards
            "votes": {user["id"]: list(dates)},
            "locked_date": None,
            "locked_by": None,
        }
        storage.save_db(db)
        label = " ".join(p for p in (entry.get("flag", ""), entry["name"]) if p)
        push.push_to_users(
            [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            i18n.msg('📅 {user} started a date poll for {label}', user=user['name'], label=label),
            i18n.msg('{wheel}: tick the evenings you can make!', wheel=wheel['name']),
            wheel,
            tag=f"{wheel['id']}-poll-{entry_id}",
        )
        return jsonify({"history": history_view(db, wheel, user)})


@app.put("/api/wheels/<wheel_id>/history/<entry_id>/poll/votes")
def vote_poll(wheel_id, entry_id):
    picked = (request.get_json(force=True, silent=True) or {}).get("dates")
    if not isinstance(picked, list):
        abort(400, description="send the evenings you can make")
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel, entry = restaurant_poll_entry(db, user, wheel_id, entry_id)
        poll = entry.get("poll")
        if not poll:
            abort(404, description="that date poll is gone")
        if poll["status"] == "locked":
            abort(409, description="the date's already locked in")
        had_voted = user["id"] in poll["votes"]  # a re-vote must not re-ping
        picked = set(map(str, picked))
        poll["votes"][user["id"]] = [d for d in poll["dates"] if d in picked]
        members = accounts.wheel_member_users(db, wheel["id"])
        member_ids = {u["id"] for u in members}
        storage.save_db(db)
        # ping the proposer only when the *last* remaining member first votes
        if not had_voted and member_ids <= set(poll["votes"]):
            spinner = db["users"].get(poll["by"])
            if spinner and spinner["id"] != user["id"]:
                unanimous = poll_unanimous(members, poll)
                body = (i18n.msg("{date} works for all of you — lock it in! 🔒",
                                 date=pretty_date(unanimous[0]))
                        if unanimous else
                        "…but no evening works for everyone yet. Add more dates?")
                push.push_to_users([spinner], i18n.msg("🗳️ Everyone's voted on {entry}", entry=entry['name']),
                              body, wheel, tag=f"{wheel['id']}-poll-{entry_id}")
        return jsonify({"history": history_view(db, wheel, user)})


def dinner_gcal_url(name, location, date_str):
    """A Google Calendar 'add event' template for a locked dinner —
    mirrors the frontend's googleCalUrl (19:00–21:00, floating local
    time) so the in-app button and the push action land the same event."""
    day = date_str.replace("-", "")
    spot = f"{name}, {location}" if location else name
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={quote('🍽️ ' + name)}"
        f"&dates={day}T190000/{day}T210000"
        f"&details={quote('Locked in with Wheel of Choice')}"
        f"&location={quote(spot)}"
    )


@app.post("/api/wheels/<wheel_id>/history/<entry_id>/poll/lock")
def lock_poll(wheel_id, entry_id):
    date_str = str((request.get_json(force=True, silent=True) or {}).get("date", ""))
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel, entry = restaurant_poll_entry(db, user, wheel_id, entry_id)
        poll = entry.get("poll")
        if not poll:
            abort(404, description="that date poll is gone")
        if poll["status"] == "locked":
            abort(409, description=i18n.msg('too late — {date} is already locked in', date=pretty_date(poll['locked_date'])))
        if date_str not in poll["dates"]:
            abort(400, description="that date isn't one of the options")
        # revalidate against current members: a fresh join or changed vote
        # since the button rendered must not be steamrolled
        members = accounts.wheel_member_users(db, wheel["id"])
        if date_str not in poll_unanimous(members, poll):
            abort(409, description="not everyone's free that evening (yet)")
        poll["status"] = "locked"
        poll["locked_date"] = date_str
        poll["locked_by"] = user["id"]
        entry["trip_date"] = date_str  # reuse the existing field
        storage.save_db(db)
        # the address lives on the destination (may be gone if deleted since)
        dest = next(
            (d for d in wheel["destinations"] if d["id"] == entry.get("dest_id")), None
        )
        push.push_to_users(
            [u for u in accounts.wheel_member_users(db, wheel["id"]) if u["id"] != user["id"]],
            i18n.msg('🗓️ {date} it is — dinner at {entry}!', date=pretty_date(date_str), entry=entry['name']),
            i18n.msg('{user} locked it in on {wheel} — pop it in your calendar 📅', user=user['name'], wheel=wheel['name']),
            wheel,
            tag=f"{wheel['id']}-poll-{entry_id}",
            gcal_url=dinner_gcal_url(entry["name"], validation.dest_calendar_location(dest), date_str),
            # body tap (the only actionable path on iOS PWAs) deep-links the
            # app to this dinner's calendar buttons
            url=f"/?dinner={quote(entry_id)}#{wheel['id']}",
        )
        return jsonify({"history": history_view(db, wheel, user)})


@app.delete("/api/wheels/<wheel_id>/history/<entry_id>/poll")
def delete_poll(wheel_id, entry_id):
    """Scrap the poll — also the 'unlock and pick a new date' path. Any
    member may, like clearing the history (household trust)."""
    with _lock:
        db = storage.load_db()
        user = accounts.current_user(db)
        wheel, entry = restaurant_poll_entry(db, user, wheel_id, entry_id)
        if entry.pop("poll", None) is not None:
            entry.pop("trip_date", None)  # a scrapped lock frees the date too
            storage.save_db(db)
        return jsonify({"history": history_view(db, wheel, user)})
