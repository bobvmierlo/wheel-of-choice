# 🌍 Wheel of Wander — holiday picker for two

A tiny webapp that helps couples pick their next holiday destination with a
wheel-of-fortune spin. No build step, no dependencies — just open
`index.html` in a browser (or host it on any static host, e.g. GitHub Pages).

## Features

- **Spin the wheel** 🎡 — the homepage shows a wheel filled with every
  destination that matches your current preferences.
- **Trip preferences** that filter what lands on the wheel:
  - 💶 **Budget**: low / mid / high
  - ✈️ **Distance**: regional (car/train), Europe, or long-haul
  - 👥 **Travel party**: just the two of you, or with friends & family
  - 🌲 **Vibe**: nature, culture & museums, food, or snow
  - 📅 **Season**: when you want to travel (destinations are tagged with
    their best months)
- **Favourites** ⭐ — star the destinations you love (in the manage panel)
  and they get a double-width segment on the wheel: twice the chance to win.
- **Manage the destination list** ⚙️ — untick built-in countries to keep
  them off the wheel, or add your own destinations with full tagging.
- **Veto & respin** 🙅 — each partner gets one veto per round, so a single
  unlucky spin doesn't end the discussion.
- **Spin history** 📖 — accepted destinations are saved so you can look
  back at past picks.

Preferences, custom destinations and history are stored in the browser's
`localStorage` — nothing leaves your machine.

## Running locally

```bash
# any static file server works, for example:
python3 -m http.server 8000
# then open http://localhost:8000
```

Or simply double-click `index.html`.

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

Since the app stores everything in the browser's `localStorage` and the
server only hands out static files, plain HTTP on a home network is fine —
there's nothing sensitive in transit.

## Customising

- The built-in destination catalogue lives in [`data.js`](data.js); the
  tags (`budget`, `distance`, `vibes`, `seasons`, `party`) are documented
  at the top of that file. "Regional" is meant as reachable by car or
  train — adjust to wherever home is for you.
- Colors and styling live in [`styles.css`](styles.css).
- All app logic is in [`app.js`](app.js).
