# 🎡 Wheel of Choice — shared decision wheels

A tiny webapp that settles "where to next?" — and "where do we eat?" —
with a wheel-of-fortune spin. A small Flask server stores accounts and
wheels; the frontend is plain HTML/CSS/JS with no build step.

## Features

- **Accounts** 👤 — registering takes ten seconds: a name and a password,
  nothing else. Your wheels, favourites and filter preferences are saved
  to your account.
- **Wheels, plural** 🎡 — every wheel is its own little world: its own
  entries, its own spin history, its own share code. A user can hold any
  number of wheels (shown as tabs; ➕ creates or joins another one), and
  a wheel can be shared with any number of accounts. There are three
  kinds:
  - 🌍 **Holidays** — whole-country destinations, seeded from the
    catalogue via a few onboarding questions.
  - 🏙️ **City trips** — cities in and around Europe, seeded the same way.
  - 🍽️ **Restaurants** — a fully custom list: no seeding, you add every
    spot yourself, and **no vetoes** — where the wheel lands is where
    you eat.
- **Onboarding per travel wheel** 🧭 — creating a holidays or city-trips
  wheel asks a few quick questions (where's home, how far you'll roam,
  what you love on a trip, budget style, and which places you're already
  dreaming of) and seeds that wheel with destinations that make sense
  for you: distances are computed relative to *your* home region,
  far-away places are parked as disabled instead of cluttering the
  wheel, and local gems get switched on for the homes they're near — a
  Benelux wheel starts with Maastricht and Lille on it, a British one
  with York and Galway. The best vibe/budget matches are pre-starred as
  favourites, and the places you picked yourself start starred too —
  even when they're beyond your roam range.
- **Per-wheel sharing** 🔗 — every wheel has its **own** share code and
  invite link; a code joins exactly that one wheel and nothing else. So
  you can share your restaurant wheel with the whole friend group while
  the holiday wheel stays between the two of you — and one person can be
  on their partner's holiday wheel, their family's city-trip wheel and
  three restaurant wheels at once. Everyone on a wheel spins the *same*
  wheel: entries, favourites and history stay in sync, while everyone
  keeps their own filters. Right after joining, a welcome screen invites
  the newcomer to star the entries *they* dream of.
- **Spin the wheel** 🎡 — filled with every entry that matches your
  current preferences.
- **Trip preferences** (travel wheels) that filter what lands on the
  wheel — every group is multi-select (pick e.g. both *low* and *mid*
  budget; *Any* clears the group):
  - 💶 **Budget**: low / mid / high
  - ✈️ **Distance**: regional (car/train), Europe, or long-haul
  - 👥 **Travel party**: just the two of you, or with friends & family
  - 🌲 **Vibe**: nature, culture & museums, food, beach, nightlife,
    adventure, wellness, or snow
  - 📅 **Season**: when you want to travel (destinations are tagged with
    their best months)
- **Favourites** ⭐ — every star is personal: star the entries you love
  (in the manage panel, during creation, or on the joiner's welcome
  screen) and they get a double-width segment on the wheel. Every extra
  star from another member widens the slice further 🌟 (up to four
  wide) — the wheel leans toward what you agree on.
- **Manage the wheel** ⚙️ — untick entries to keep them off the wheel,
  ✏️ edit any entry's name and tags, delete them, or add your own. On a
  restaurant wheel the form is just a name, an emoji, notes and links.
- **Veto & respin** 🙅 (travel wheels) — every member gets exactly one
  veto per round (tracked on the server, so no sneaky double vetoes),
  and a spin one member accepts waits for the others' thumbs-up: their
  devices show the pick with an accept-or-veto banner, and with three or
  four members it only counts once *everyone* who can still veto has
  okayed it. A round closes when a pick makes it into the history.
  Restaurant wheels skip all of this: the wheel's word is final and the
  pick goes straight into the history.
- **Spin history & trip status** 📖 — accepted picks are saved
  (including who spun them) so you can look back. On travel wheels each
  pick can be marked 📅 *Booked* or ✅ *Been there* (with an optional
  trip date) from its info view; marking a pick "been there" takes the
  destination off the wheel automatically, so you don't spin Rome again
  next year — turning the history into a little travel log.
- **Entry info & links** 📍 — every entry can carry notes and links.
  Click a pick in the spin history (or a name in the manage panel) to
  see its tags, your notes, and clickable links (seeded destinations
  start with their Wikivoyage and Wikipedia pages), plus ready-made
  planning searches — ✈️ flights and 🛏️ stays for travel wheels, 📍 a
  maps lookup for restaurants; the spin result shows the notes and links
  too, so you can start planning right away.
- **Admin** 🛠️ — the first account ever registered runs the place: it can
  make other users admin, pull someone out of every wheel they share,
  delete accounts, download or restore a full backup of the database,
  and trigger a server self-update (see below).

**Storage**: everything lives on the server in `data/db.json` — accounts
(passwords stored as scrypt hashes), login sessions (expiring after 90
days), and every wheel. Only your login token stays in the browser. The
admin panel can download that file as a backup and restore one later.
Older databases are migrated automatically: the shared *spaces* of v2
are split into independent wheels (the old space code keeps working — it
now joins the holidays wheel, and the city-trips wheel gets a fresh code
of its own), and the pre-account layout becomes unclaimed wheels the
first account can adopt from the first-wheel screen.

## Running locally

```bash
pip install -r requirements.txt   # or: apt install python3-flask
python3 server.py
# then open http://localhost:8000
```

## Deploying to a Linux server

The repo ships with a tiny [Flask](https://flask.palletsprojects.com)
server ([`server.py`](server.py)) and a ready-made systemd unit
([`deploy/holiday-picker.service`](deploy/holiday-picker.service)), so
deploying to any Linux box — an Ubuntu LXC, VM, Raspberry Pi, … — takes
three steps:

### 1. Get the code and Flask

```bash
sudo apt update && sudo apt install -y git python3-flask
sudo git clone https://github.com/bobvmierlo/wheel-of-choice.git /opt/holiday-picker
```

(Prefer pip? `pip install -r requirements.txt` in a venv works too —
then adjust `ExecStart` in the unit file to the venv's python.)

### 2. Try it

```bash
python3 /opt/holiday-picker/server.py
```

Open `http://<server-ip>:8000` — you should see the wheel. Ctrl-C to stop.

### 3. Make it a service

```bash
sudo cp /opt/holiday-picker/deploy/holiday-picker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now holiday-picker
systemctl status holiday-picker   # should say "active (running)"
```

The service starts on boot, restarts on failure, and runs as an
unprivileged dynamic user with a read-only view of the system.

**Updating** to the latest version:

```bash
cd /opt/holiday-picker && sudo git pull && sudo systemctl restart holiday-picker
```

Or let the admin do it from the app: install the updater units once —

```bash
sudo cp /opt/holiday-picker/deploy/holiday-picker-update.service \
        /opt/holiday-picker/deploy/holiday-picker-update.path /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now holiday-picker-update.path
```

— and the 🛠️ Admin panel's "Update & restart server" button does the
pull-and-restart for you. (The app itself runs sandboxed without sudo;
the button drops a flag file in the state directory, which the path unit
watches and acts on as root.)

**Changing the port**: edit the last line of `server.py`, then
`sudo systemctl restart holiday-picker`.

The service stores accounts, wheels and history in
`/var/lib/holiday-picker/db.json` (via systemd's `StateDirectory`), so
your data survives updates and restarts. Accounts keep casual visitors
out, but the app still speaks plain HTTP — run it on your home network
(or behind a reverse proxy with TLS), not naked on the open internet.

## Seed data & sources

The two starting catalogues —
[`seed-destinations.json`](seed-destinations.json) (~85 countries) and
[`seed-citytrips.json`](seed-citytrips.json) (~125 cities) — were
**hand-curated for this app**, not imported from an external dataset.
(Restaurant wheels have no catalogue on purpose — every entry is yours.)
The tags are subjective editorial estimates meant to make the wheel
useful on day one, roughly:

- `budget` — typical price level for visitors from Western Europe
  (accommodation, food, getting around; flights not included);
- `vibes` — what the place is best known for as a trip;
- `seasons` — when a visit is usually at its best (e.g. Mediterranean
  summers, Alpine winters);
- `near` — the home regions from which it's realistically reachable by
  car or train.

Treat them as conversation starters, not travel advice — and correct
anything you disagree with in ⚙️ Manage the wheel.

For sources, every seeded destination starts with links to its
[Wikivoyage](https://en.wikivoyage.org) travel guide and
[Wikipedia](https://en.wikipedia.org) article, shown in the spin result
and in the info view behind every spin-history entry. You can replace or
extend these per destination with your own links (official tourism
sites, blogs, hotel pages, …) via ✏️ edit.

## Customising

- The starting catalogues live in
  [`seed-destinations.json`](seed-destinations.json) (holidays) and
  [`seed-citytrips.json`](seed-citytrips.json) (city trips) — used to
  seed a travel wheel when it's created; after that, edit entries in the
  app itself (⚙️ Manage the wheel). Each entry's `near` list names the
  home regions from which it counts as "regional" (reachable by car or
  train); the creation questions recompute distances from the answers.
  Entries with `"enabled": false` are niche picks that start off the
  wheel — except for homes they're regional to, where a local gem starts
  on it.
- The wheel types and the tag/onboarding vocabulary (`budget`,
  `distance`, `vibes`, `seasons`, `party`, home regions, roam ranges)
  are defined at the top of [`server.py`](server.py).
- Colors and styling live in [`styles.css`](styles.css); all frontend
  logic is in [`app.js`](app.js).
