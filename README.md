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

## Deploying to a server (e.g. an Ubuntu LXC)

The site is fully static (four files, no build step, no backend), so any
web server can host it. Below is a complete recipe for an Ubuntu LXC —
for example on Proxmox — using nginx.

### 1. Create the container (Proxmox example)

On the Proxmox host (adjust storage/bridge/ID to your setup):

```bash
pct create 120 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname holiday-picker --memory 256 --cores 1 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --start 1
pct enter 120
```

Any existing Ubuntu LXC/VM/box works just as well — continue below inside it.

### 2. Install nginx and fetch the site

```bash
apt update && apt install -y nginx git
git clone https://github.com/bobvmierlo/holiday-picker.git /var/www/holiday-picker
```

### 3. Point nginx at it

```bash
cat > /etc/nginx/sites-available/holiday-picker <<'EOF'
server {
    listen 80 default_server;
    server_name _;

    root /var/www/holiday-picker;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }
}
EOF

ln -sf /etc/nginx/sites-available/holiday-picker /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

Find the container's IP with `ip a` and open `http://<container-ip>/` —
that's it. nginx starts on boot, so the container survives restarts
without further setup.

### Updating

```bash
cd /var/www/holiday-picker && git pull
```

No reload needed — nginx serves the new files immediately.

### Notes & alternatives

- **HTTPS / domain name**: if the container is reachable from the
  internet with a domain, [Caddy](https://caddyserver.com) is the
  simplest option — `apt install caddy`, then a two-line `/etc/caddy/Caddyfile`
  (`yourdomain.example` + `root * /var/www/holiday-picker` + `file_server`)
  gets you automatic Let's Encrypt certificates. On a purely local
  network, plain HTTP is fine: the app stores everything in the
  browser's `localStorage` and sends nothing over the network.
- **Quick test without nginx**: `python3 -m http.server 8000` in the
  repo folder serves it instantly (not recommended as a permanent setup).
- **No server at all**: enable GitHub Pages on this repository
  (Settings → Pages → deploy from branch) and the site is hosted for free.

## Customising

- The built-in destination catalogue lives in [`data.js`](data.js); the
  tags (`budget`, `distance`, `vibes`, `seasons`, `party`) are documented
  at the top of that file. "Regional" is meant as reachable by car or
  train — adjust to wherever home is for you.
- Colors and styling live in [`styles.css`](styles.css).
- All app logic is in [`app.js`](app.js).
