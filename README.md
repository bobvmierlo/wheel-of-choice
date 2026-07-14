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

## Customising

- The built-in destination catalogue lives in [`data.js`](data.js); the
  tags (`budget`, `distance`, `vibes`, `seasons`, `party`) are documented
  at the top of that file. "Regional" is meant as reachable by car or
  train — adjust to wherever home is for you.
- Colors and styling live in [`styles.css`](styles.css).
- All app logic is in [`app.js`](app.js).
