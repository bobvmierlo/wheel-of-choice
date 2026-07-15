# 🌍 Wheel of Wander — holiday picker for two

A tiny webapp that helps couples pick their next holiday destination with a
wheel-of-fortune spin. A small Flask server stores accounts and shared
wheels; the frontend is plain HTML/CSS/JS with no build step.

## Features

- **Accounts** 👤 — registering takes ten seconds: a name and a password,
  nothing else. Your destinations, favourites and filter preferences are
  saved to your account.
- **Onboarding** 🧭 — four quick questions on first login (where's home,
  how far you'll roam, what you love on a trip, budget style) seed your
  wheels with destinations that make sense for you: distances are
  computed relative to *your* home region, far-away places are parked as
  disabled instead of cluttering the wheel, and the best vibe/budget
  matches are pre-starred as favourites.
- **Sharing for couples** 🔗 — every set of wheels has a share code.
  Your partner registers their own account, enters your code, and from
  then on you both spin the *same* wheels: destinations, favourites and
  history stay in sync, while each of you keeps your own filters.
- **Two wheels** 🌍🏙️ — tabs switch between *Holidays* (whole countries)
  and *City trips* (cities in and around Europe), each with its own
  destination list and history.
- **Spin the wheel** 🎡 — filled with every destination that matches your
  current preferences.
- **Trip preferences** that filter what lands on the wheel — every group
  is multi-select (pick e.g. both *low* and *mid* budget; *Any* clears
  the group):
  - 💶 **Budget**: low / mid / high
  - ✈️ **Distance**: regional (car/train), Europe, or long-haul
  - 👥 **Travel party**: just the two of you, or with friends & family
  - 🌲 **Vibe**: nature, culture & museums, food, beach, nightlife,
    adventure, wellness, or snow
  - 📅 **Season**: when you want to travel (destinations are tagged with
    their best months)
- **Favourites** ⭐ — star the destinations you love (in the manage panel)
  and they get a double-width segment on the wheel: twice the chance to win.
- **Manage the destination list** ⚙️ — untick destinations to keep them
  off the wheel, ✏️ edit any destination's name and tags, delete them, or
  add your own.
- **Veto & respin** 🙅 — each partner gets one veto per round, so a single
  unlucky spin doesn't end the discussion.
- **Spin history** 📖 — accepted destinations are saved (including who
  accepted the spin) so you can look back at past picks.

**Storage**: everything lives on the server in `data/db.json` — accounts
(passwords stored as scrypt hashes), login sessions, and each shared
space's wheels. Only your login token stays in the browser. Databases
from the pre-account version are migrated automatically: the onboarding
screen offers your old destinations and history to the first account
("keep them"), or you can answer the questions and start fresh.

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
sudo git clone https://github.com/bobvmierlo/holiday-picker.git /opt/holiday-picker
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

**Changing the port**: edit the last line of `server.py`, then
`sudo systemctl restart holiday-picker`.

The service stores accounts, wheels and history in
`/var/lib/holiday-picker/db.json` (via systemd's `StateDirectory`), so
your data survives updates and restarts. Accounts keep casual visitors
out, but the app still speaks plain HTTP — run it on your home network
(or behind a reverse proxy with TLS), not naked on the open internet.

## Customising

- The starting catalogues live in
  [`seed-destinations.json`](seed-destinations.json) (holidays) and
  [`seed-citytrips.json`](seed-citytrips.json) (city trips) — used to
  seed each account's wheels during onboarding; after that, edit
  destinations in the app itself (⚙️ Manage destinations). Each entry's
  `near` list names the home regions from which it counts as "regional"
  (reachable by car or train); onboarding recomputes distances from the
  answers.
- The tag vocabulary (`budget`, `distance`, `vibes`, `seasons`, `party`)
  and the onboarding vocabulary (home regions, roam ranges) are defined
  at the top of [`server.py`](server.py).
- Colors and styling live in [`styles.css`](styles.css); all frontend
  logic is in [`app.js`](app.js).
