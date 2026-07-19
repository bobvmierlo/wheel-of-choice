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
- **Date polls** 🗳️ (restaurant wheels) — once the wheel picks *where*,
  settle *when*: open the pick and start a Doodle-style poll, everyone
  ticks the evenings they can make, and any evening you all agree on
  locks in with a tap (then drops into your calendar as an .ics or
  Google link). Optionally link your own calendar by its secret iCal
  address — no Google account or app needed — and the date grid marks
  the evenings you're already busy, just for you. Setup in
  [Date polls & calendars](#️-date-polls--calendars-restaurant-wheels).
- **Push notifications** 🔔 — optional, per device: get a ping when
  someone spins and waits for your thumbs-up, when a pick gets vetoed,
  when the decision is final, and when a member adds a new entry to a
  shared wheel — even with the app closed. Standard
  Web Push straight from your own server (no Firebase, no accounts with
  anyone); works on Android and, as a Home-Screen app, on iOS 16.4+.
  Setup in [Push notifications](#-push-notifications-optional).
- **Installable** 📲 — the app is a PWA: add it to your phone's home
  screen and it opens full-screen with its own icon, like an app-store
  app — without the app store.
- **Admin** 🛠️ — the first account ever registered runs the place: it can
  make other users admin, pull someone out of every wheel they share,
  delete accounts, download or restore a full backup of the database,
  and trigger a server self-update (see below).

**Storage**: everything lives on the server in `data/db.json` — accounts
(passwords stored as scrypt hashes), login sessions (expiring after 90
days), push subscriptions, and every wheel. Only your login token stays in the browser. The
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
([`deploy/wheel-of-choice.service`](deploy/wheel-of-choice.service)), so
deploying to any Linux box — an Ubuntu LXC, VM, Raspberry Pi, … — takes
three steps:

### 1. Get the code and Flask

```bash
sudo apt update && sudo apt install -y git python3-flask
sudo git clone https://github.com/bobvmierlo/wheel-of-choice.git /opt/wheel-of-choice
```

(Prefer pip? `pip install -r requirements.txt` in a venv works too —
then adjust `ExecStart` in the unit file to the venv's python.)

### 2. Try it

```bash
python3 /opt/wheel-of-choice/server.py
```

Open `http://<server-ip>:8000` — you should see the wheel. Ctrl-C to stop.

### 3. Make it a service

```bash
sudo cp /opt/wheel-of-choice/deploy/wheel-of-choice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wheel-of-choice
systemctl status wheel-of-choice   # should say "active (running)"
```

The service starts on boot, restarts on failure, and runs as an
unprivileged dynamic user with a read-only view of the system.

**Updating** to the latest version:

```bash
cd /opt/wheel-of-choice && sudo git pull && sudo systemctl restart wheel-of-choice
```

Or let the admin do it from the app: install the updater units once —

```bash
sudo cp /opt/wheel-of-choice/deploy/wheel-of-choice-update.service \
        /opt/wheel-of-choice/deploy/wheel-of-choice-update.path /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wheel-of-choice-update.path
```

— and the 🛠️ Admin panel's "Update & restart server" button does the
pull-and-restart for you. (The app itself runs sandboxed without sudo;
the button drops a flag file in the state directory, which the path unit
watches and acts on as root.)

**Changing the port**: edit the last line of `server.py`, then
`sudo systemctl restart wheel-of-choice`.

The service stores accounts, wheels and history in
`/var/lib/wheel-of-choice/db.json` (via systemd's `StateDirectory`), so
your data survives updates and restarts. Accounts keep casual visitors
out, but the app still speaks plain HTTP — run it on your home network
(or behind a reverse proxy with TLS), not naked on the open internet.

## 🔔 Push notifications (optional)

The app can ping members' phones when something happens on a shared
wheel: *"🎡 Emma spun 🇵🇹 Portugal — are you in, or is this your veto?"*,
*"🙅 Bob vetoed 🇫🇷 France — spin again!"*, *"🎉 🇮🇹 Italy it is!"*,
*"➕ Kim added 🍜 Pho 88"*. It uses **standard Web Push with VAPID
keys**, which means:

- your server signs every message itself and sends it **directly** to
  the push endpoint each browser registers — Apple's relay for iPhones,
  Google's for Chrome, Mozilla's for Firefox. There is **no Firebase
  project, no Google/Apple developer account, and nothing to sign up
  for** — the messages do pass through the platform relay (that's how
  phones get woken), but they're end-to-end encrypted, so the relay only
  sees ciphertext;
- everything else stays on your box: subscriptions live in `db.json`,
  and the signing key is a single PEM file in the data directory.

### One-time server setup

1. **Install [pywebpush](https://github.com/web-push-libs/pywebpush)**
   next to Flask (without it the app runs fine, just without pushes):

   ```bash
   sudo pip3 install pywebpush        # add --break-system-packages on Debian 12+
   # or, if you run the server from a venv: pip install -r requirements.txt
   ```

2. **Set a contact address** (optional but recommended): the VAPID
   `sub` claim tells the push relays how to reach you if your server
   misbehaves. Uncomment the `VAPID_SUBJECT` line in
   [`deploy/wheel-of-choice.service`](deploy/wheel-of-choice.service)
   and put your own `mailto:` address in, or export it before running
   `server.py` by hand.

3. **Restart the server.** That's it — the VAPID keypair is generated
   automatically on first use and stored as `vapid-private-key.pem`
   next to `db.json`. **Keep that file**: it's what all subscriptions
   are bound to, and losing it (e.g. wiping the data dir) silently
   breaks every enabled device until people toggle 🔔 off and on again.

### The HTTPS requirement

Push (and service workers in general) only work on a **secure origin**:
`http://localhost` is fine for development, but phones need the real
site served over **HTTPS with a certificate they trust**. A self-signed
certificate on a LAN IP won't cut it on an iPhone — the practical route
for a self-hosted box is a (sub)domain pointing at it and a reverse
proxy that does TLS, e.g. [Caddy](https://caddyserver.com) (automatic
Let's Encrypt) or nginx + certbot, forwarding to `localhost:8000`. Your
server also needs *outbound* HTTPS to the push relays
(`web.push.apple.com`, `fcm.googleapis.com`, …) — no inbound ports
beyond your proxy.

### Turning it on, per device

Everyone chooses per device — notifications are personal, not
per wheel:

- **Android** (Chrome, Firefox, …): open the site, log in, tap **🔔** in
  the top bar → *Turn on for this device*. Installing the app
  (browser menu → *Add to Home Screen / Install app*) is optional but
  nice.
- **iPhone / iPad** (iOS 16.4 or newer): Apple only allows Web Push for
  installed web apps, so first open the site in **Safari** → Share →
  **Add to Home Screen**. Then open the 🎡 app *from the Home Screen*,
  log in, tap **🔔** → *Turn on for this device*, and allow the
  permission prompt. (In a plain Safari tab the 🔔 dialog shows these
  same steps instead of the button.)

### Troubleshooting

- *The 🔔 dialog says the server can't send notifications* — pywebpush
  isn't installed (or the server wasn't restarted after installing it).
- *No permission prompt on an iPhone* — the app wasn't opened from the
  Home Screen icon, or iOS is older than 16.4.
- *Notifications stopped after restoring a backup onto a fresh box* —
  restore `vapid-private-key.pem` along with `db.json`, or have
  everyone toggle 🔔 off and on again.
- *One device went quiet* — browsers rotate or expire subscriptions now
  and then. The app re-registers on every login and the server drops
  dead endpoints automatically, so a visit to the app usually heals it;
  otherwise toggle 🔔 off and on.

## 🗳️ Date polls & calendars (restaurant wheels)

Once a restaurant wheel lands on a place, you still have to agree on a
*night*. Open the pick from the 📖 spin history and hit **📅 Start a
date poll**: put up a handful of evenings, everyone ticks the ones they
can make, and any evening everyone ticked can be **locked in** with one
tap. It rides the same live sync and 🔔 notifications as the rest of the
wheel — a partner's vote or a locked date shows up within a few seconds,
and once it's locked you get **⬇️ Add to calendar (.ics)** and **📆
Google Calendar** buttons (both built on the spot, no account needed).

Polls work on their own — but they get nicer if members link a calendar,
so the date grid can pre-mark the evenings they're already busy.

### Linking a calendar (optional)

This uses your calendar's **secret iCal address** — read-only, no OAuth,
no Google Cloud project, nothing to sign up for. Your busy/free is worked
out on the server and shown **only to you**; the others just see the
dates you tick. The secret URL is stored on the server and never handed
back out to anyone (not even you — it's write-only once saved).

**One-time server setup** — install the two optional libraries (without
them, polls still work, just without the busy-evening hints):

```bash
sudo pip3 install icalendar recurring-ical-events   # --break-system-packages on Debian 12+
# or, from a venv: pip install -r requirements.txt
```

Busy evenings are judged in local time (17:00–midnight). If your server
isn't on Amsterdam time, set your zone — uncomment `WHEEL_TZ` in
[`deploy/wheel-of-choice.service`](deploy/wheel-of-choice.service) (any
[IANA name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)
like `Europe/London` or `America/New_York`), then restart.

**Where each provider hides the secret address:**

- **Google Calendar** — Settings → *(pick the calendar)* → *Integrate
  calendar* → **Secret address in iCal format**. (Heads-up: Google's ICS
  feed can lag a few hours behind live changes — fine for planning a
  dinner, not minute-accurate.)
- **Outlook / Microsoft 365** — Settings → Calendar → *Shared calendars*
  → Publish a calendar → **ICS** link.
- **Apple iCloud** — share the calendar as a *Public Calendar* and copy
  the `webcal://` link (the app turns it into `https://` for you).
- **Nextcloud** — calendar → … → *Copy private link* (the `?export`
  `.ics` one).

**Then, per person:** open **📆** in the top bar, paste the address, give
it a label, and *Link this calendar*. You can link up to four (home,
work, partner's shared calendar…). Everything you link is yours alone —
each member does this on their own account.

### Troubleshooting

- *No 📆 button* — the `icalendar` / `recurring-ical-events` libraries
  aren't installed, or the server wasn't restarted after installing them.
- *"couldn't read that calendar"* — it must be an `https://` (or
  `webcal://`) address the server can reach; double-check you copied the
  **secret/private** iCal URL, not the calendar's web page.
- *Busy evenings look off by an hour or on the wrong day* — set `WHEEL_TZ`
  to your own timezone and restart.
- *All-day events don't mark me busy* — on purpose: birthday and
  public-holiday calendars would otherwise paint whole weeks red. Only
  timed evening events (17:00 onward) count.

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
