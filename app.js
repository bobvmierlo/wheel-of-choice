/* Wheel of Wander — couples' holiday destination picker. */
(function () {
  'use strict';

  const STORAGE_KEY = 'wheel-of-wander-v1';

  const VIBE_LABELS = { nature: '🌲 nature', culture: '🏛️ culture & museums', food: '🍽️ food', winter: '⛷️ snow' };
  const VIBE_MIGRATION = { beach: 'nature', city: 'culture' }; // pre-favourites tag names
  const FAVORITE_WEIGHT = 2; // favourites get a double-width wheel segment
  const BUDGET_LABELS = { low: '💶 low budget', mid: '💶💶 mid budget', high: '💶💶💶 high budget' };
  const DISTANCE_LABELS = { regional: '🚗 regional', europe: '✈️ Europe', longhaul: '🌏 long-haul' };

  const SEGMENT_COLORS = [
    '#ff5e7e', '#ffb84d', '#4dabff', '#6ee7a8',
    '#c084fc', '#f97362', '#38d0e0', '#facc15',
    '#fb7fb8', '#8aa9ff', '#5eddaf', '#ff9e6d',
  ];

  // ── State ─────────────────────────────────────────────────────────
  const state = {
    filters: { budget: 'any', distance: 'any', party: 'couple', vibe: 'any', season: 'any' },
    customDestinations: [],
    disabledIds: [],
    favoriteIds: BUILTIN_DESTINATIONS.filter((d) => d.favorite).map((d) => d.id),
    history: [],
    // Round state (not persisted): each partner gets one veto per round.
    vetoedIds: [],
    vetoesLeft: 2,
  };

  function loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved.filters) Object.assign(state.filters, saved.filters);
      if (Array.isArray(saved.customDestinations)) state.customDestinations = saved.customDestinations;
      if (Array.isArray(saved.disabledIds)) state.disabledIds = saved.disabledIds;
      if (Array.isArray(saved.favoriteIds)) state.favoriteIds = saved.favoriteIds;
      if (Array.isArray(saved.history)) state.history = saved.history;
      // Migrate state saved before the vibe taxonomy changed
      if (!(state.filters.vibe in VIBE_LABELS) && state.filters.vibe !== 'any') state.filters.vibe = 'any';
      for (const d of state.customDestinations) {
        d.vibes = [...new Set(d.vibes.map((v) => VIBE_MIGRATION[v] || v))];
      }
    } catch (err) {
      console.warn('Could not load saved state:', err);
    }
  }

  function saveState() {
    const { filters, customDestinations, disabledIds, favoriteIds, history } = state;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ filters, customDestinations, disabledIds, favoriteIds, history }));
  }

  function isFavorite(d) {
    return state.favoriteIds.includes(d.id);
  }

  function allDestinations() {
    return BUILTIN_DESTINATIONS.concat(state.customDestinations);
  }

  function eligibleDestinations() {
    const f = state.filters;
    return allDestinations().filter((d) =>
      !state.disabledIds.includes(d.id) &&
      !state.vetoedIds.includes(d.id) &&
      (f.budget === 'any' || d.budget === f.budget) &&
      (f.distance === 'any' || d.distance === f.distance) &&
      d.party.includes(f.party) &&
      (f.vibe === 'any' || d.vibes.includes(f.vibe)) &&
      (f.season === 'any' || d.seasons.includes(f.season))
    );
  }

  function startNewRound() {
    state.vetoedIds = [];
    state.vetoesLeft = 2;
  }

  // ── Elements ──────────────────────────────────────────────────────
  const canvas = document.getElementById('wheel');
  const ctx = canvas.getContext('2d');
  const spinBtn = document.getElementById('spin-btn');
  const wheelHint = document.getElementById('wheel-hint');
  const matchCount = document.getElementById('match-count');

  const resultModal = document.getElementById('result-modal');
  const resultFlag = document.getElementById('result-flag');
  const resultName = document.getElementById('result-name');
  const resultTags = document.getElementById('result-tags');
  const acceptBtn = document.getElementById('accept-btn');
  const vetoBtn = document.getElementById('veto-btn');
  const vetoNote = document.getElementById('veto-note');

  const manageModal = document.getElementById('manage-modal');
  const manageBtn = document.getElementById('manage-btn');
  const closeManageBtn = document.getElementById('close-manage-btn');
  const destList = document.getElementById('dest-list');
  const addForm = document.getElementById('add-form');

  const historyList = document.getElementById('history-list');
  const clearHistoryBtn = document.getElementById('clear-history-btn');

  // ── Wheel drawing ─────────────────────────────────────────────────
  let rotation = 0;          // current wheel rotation in radians
  let spinning = false;
  let currentSegments = [];  // destinations currently on the wheel
  let pendingResult = null;

  function drawWheel() {
    const size = canvas.width;
    const cx = size / 2;
    const cy = size / 2;
    const radius = size / 2 - 8;
    const n = currentSegments.length;

    ctx.clearRect(0, 0, size, size);

    // Outer rim
    ctx.beginPath();
    ctx.arc(cx, cy, radius + 6, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fill();

    if (n === 0) {
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = '#2a1747';
      ctx.fill();
      ctx.fillStyle = '#b9aed6';
      ctx.font = `600 ${size * 0.035}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('No destinations match', cx, cy - size * 0.03);
      ctx.fillText('— loosen the filters!', cx, cy + size * 0.03);
      return;
    }

    const bounds = segmentBounds();

    for (let i = 0; i < n; i++) {
      const seg = bounds[i].end - bounds[i].start;
      const start = rotation + bounds[i].start;
      const end = rotation + bounds[i].end;

      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, radius, start, end);
      ctx.closePath();
      ctx.fillStyle = SEGMENT_COLORS[i % SEGMENT_COLORS.length];
      // Avoid identical neighbouring colors when the count wraps awkwardly
      if (n % SEGMENT_COLORS.length === 1 && i === n - 1) {
        ctx.fillStyle = '#94a3b8';
      }
      ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,0.55)';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Label
      const d = currentSegments[i];
      const mid = start + seg / 2;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(mid);
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(20, 10, 40, 0.9)';
      const fontSize = Math.max(11, Math.min(20, (radius * seg) / 3.2, size * 0.032));
      ctx.font = `700 ${fontSize}px system-ui, sans-serif`;
      let label = `${isFavorite(d) ? '⭐ ' : ''}${d.flag} ${d.name}`;
      const maxWidth = radius * 0.62;
      while (ctx.measureText(label).width > maxWidth && label.length > 6) {
        label = label.slice(0, -2).trimEnd() + '…';
      }
      ctx.fillText(label, radius - 14, 0);
      ctx.restore();
    }

    // Hub
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.21, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fill();
  }

  // Angular extent of each segment (before wheel rotation), sized by weight:
  // favourites take twice the arc of a regular destination.
  function segmentBounds() {
    const weights = currentSegments.map((d) => (isFavorite(d) ? FAVORITE_WEIGHT : 1));
    const total = weights.reduce((a, b) => a + b, 0);
    const bounds = [];
    let acc = 0;
    for (const w of weights) {
      bounds.push({ start: (acc / total) * Math.PI * 2, end: ((acc + w) / total) * Math.PI * 2 });
      acc += w;
    }
    return bounds;
  }

  function winningIndex() {
    const pointerAngle = -Math.PI / 2; // pointer sits at the top
    let a = (pointerAngle - rotation) % (Math.PI * 2);
    if (a < 0) a += Math.PI * 2;
    const bounds = segmentBounds();
    const idx = bounds.findIndex((b) => a >= b.start && a < b.end);
    return idx === -1 ? currentSegments.length - 1 : idx;
  }

  function spin() {
    if (spinning || currentSegments.length === 0) return;
    if (currentSegments.length === 1) {
      // Only one option left — no suspense needed.
      showResult(currentSegments[0]);
      return;
    }

    spinning = true;
    spinBtn.disabled = true;
    wheelHint.textContent = 'Round and round it goes… 🤞';

    const startRotation = rotation;
    const extraTurns = 5 + Math.random() * 3;
    const targetRotation = startRotation + extraTurns * Math.PI * 2 + Math.random() * Math.PI * 2;
    const duration = 4400;
    const startTime = performance.now();

    function frame(now) {
      const t = Math.min(1, (now - startTime) / duration);
      const eased = 1 - Math.pow(1 - t, 4); // ease-out quart
      rotation = startRotation + (targetRotation - startRotation) * eased;
      drawWheel();
      if (t < 1) {
        requestAnimationFrame(frame);
      } else {
        spinning = false;
        spinBtn.disabled = false;
        wheelHint.textContent = '';
        showResult(currentSegments[winningIndex()]);
      }
    }
    requestAnimationFrame(frame);
  }

  // ── Result modal ──────────────────────────────────────────────────
  function describe(d) {
    const parts = [
      isFavorite(d) ? '⭐ favourite' : '',
      BUDGET_LABELS[d.budget],
      DISTANCE_LABELS[d.distance],
      d.vibes.map((v) => VIBE_LABELS[v]).join(' · '),
    ];
    return parts.filter(Boolean).join('  ·  ');
  }

  function showResult(destination) {
    pendingResult = destination;
    resultFlag.textContent = destination.flag;
    resultName.textContent = destination.name;
    resultTags.textContent = describe(destination);
    vetoBtn.disabled = state.vetoesLeft === 0;
    vetoNote.textContent = state.vetoesLeft > 0
      ? `Vetoes left this round: ${state.vetoesLeft} — each of you gets one.`
      : 'No vetoes left — the wheel has spoken!';
    resultModal.showModal();
    launchConfetti();
  }

  acceptBtn.addEventListener('click', () => {
    if (pendingResult) {
      state.history.unshift({
        name: pendingResult.name,
        flag: pendingResult.flag,
        date: new Date().toISOString(),
      });
      state.history = state.history.slice(0, 20);
      saveState();
      renderHistory();
    }
    resultModal.close();
    startNewRound();
    refresh();
    wheelHint.textContent = `${pendingResult.flag} ${pendingResult.name} it is — time to book! 🧳`;
    pendingResult = null;
  });

  vetoBtn.addEventListener('click', () => {
    if (!pendingResult || state.vetoesLeft === 0) return;
    state.vetoesLeft -= 1;
    state.vetoedIds.push(pendingResult.id);
    pendingResult = null;
    resultModal.close();
    refresh();
    if (currentSegments.length > 0) {
      spin();
    } else {
      wheelHint.textContent = 'Nothing left after that veto — loosen the filters or start over.';
    }
  });

  // ── Confetti ──────────────────────────────────────────────────────
  const confettiCanvas = document.getElementById('confetti');
  const confettiCtx = confettiCanvas.getContext('2d');
  let confettiRaf = null;

  function launchConfetti() {
    const W = confettiCanvas.width;
    const H = confettiCanvas.height;
    const pieces = Array.from({ length: 90 }, () => ({
      x: Math.random() * W,
      y: -10 - Math.random() * H * 0.5,
      size: 4 + Math.random() * 6,
      speed: 1.5 + Math.random() * 2.5,
      drift: (Math.random() - 0.5) * 1.2,
      tilt: Math.random() * Math.PI * 2,
      spin: (Math.random() - 0.5) * 0.25,
      color: SEGMENT_COLORS[Math.floor(Math.random() * SEGMENT_COLORS.length)],
    }));
    const started = performance.now();
    cancelAnimationFrame(confettiRaf);

    function tick(now) {
      confettiCtx.clearRect(0, 0, W, H);
      if (now - started > 3200 || !resultModal.open) return;
      for (const p of pieces) {
        p.y += p.speed;
        p.x += p.drift;
        p.tilt += p.spin;
        confettiCtx.save();
        confettiCtx.translate(p.x, p.y);
        confettiCtx.rotate(p.tilt);
        confettiCtx.fillStyle = p.color;
        confettiCtx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        confettiCtx.restore();
      }
      confettiRaf = requestAnimationFrame(tick);
    }
    confettiRaf = requestAnimationFrame(tick);
  }

  // ── Filters ───────────────────────────────────────────────────────
  document.querySelectorAll('.filter-group').forEach((group) => {
    const key = group.dataset.filter;
    group.addEventListener('click', (e) => {
      const btn = e.target.closest('.seg-btn');
      if (!btn || spinning) return;
      group.querySelectorAll('.seg-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.filters[key] = btn.dataset.value;
      startNewRound(); // changing preferences resets vetoes
      saveState();
      refresh();
    });
  });

  function syncFilterButtons() {
    document.querySelectorAll('.filter-group').forEach((group) => {
      const key = group.dataset.filter;
      group.querySelectorAll('.seg-btn').forEach((b) => {
        b.classList.toggle('active', b.dataset.value === state.filters[key]);
      });
    });
  }

  // ── Manage destinations ───────────────────────────────────────────
  manageBtn.addEventListener('click', () => {
    renderDestList();
    manageModal.showModal();
  });
  closeManageBtn.addEventListener('click', () => manageModal.close());
  manageModal.addEventListener('close', () => refresh());

  function renderDestList() {
    destList.innerHTML = '';
    const customIds = new Set(state.customDestinations.map((d) => d.id));
    for (const d of allDestinations()) {
      const li = document.createElement('li');
      const disabled = state.disabledIds.includes(d.id);
      li.classList.toggle('disabled', disabled);

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = !disabled;
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          state.disabledIds = state.disabledIds.filter((id) => id !== d.id);
        } else {
          state.disabledIds.push(d.id);
        }
        li.classList.toggle('disabled', !checkbox.checked);
        saveState();
      });

      const star = document.createElement('button');
      star.type = 'button';
      star.className = 'star-btn';
      star.title = 'Favourites get a double chance on the wheel';
      star.textContent = isFavorite(d) ? '⭐' : '☆';
      star.classList.toggle('starred', isFavorite(d));
      star.addEventListener('click', () => {
        if (isFavorite(d)) {
          state.favoriteIds = state.favoriteIds.filter((id) => id !== d.id);
        } else {
          state.favoriteIds.push(d.id);
        }
        star.textContent = isFavorite(d) ? '⭐' : '☆';
        star.classList.toggle('starred', isFavorite(d));
        saveState();
      });

      const name = document.createElement('span');
      name.className = 'dest-name';
      name.textContent = `${d.flag} ${d.name}`;

      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      meta.textContent = `${d.budget} · ${DISTANCE_LABELS[d.distance]}`;

      li.append(checkbox, star, name, meta);

      if (customIds.has(d.id)) {
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'del-btn';
        del.title = 'Remove this destination';
        del.textContent = '✕';
        del.addEventListener('click', () => {
          state.customDestinations = state.customDestinations.filter((c) => c.id !== d.id);
          state.disabledIds = state.disabledIds.filter((id) => id !== d.id);
          saveState();
          renderDestList();
        });
        li.append(del);
      }
      destList.append(li);
    }
  }

  function checkedValues(containerId) {
    return Array.from(document.querySelectorAll(`#${containerId} input:checked`)).map((i) => i.value);
  }

  addForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const name = document.getElementById('add-name').value.trim();
    if (!name) return;
    const vibes = checkedValues('add-vibes');
    const seasons = checkedValues('add-seasons');
    const party = checkedValues('add-party');
    state.customDestinations.push({
      id: 'custom-' + Date.now(),
      name,
      flag: document.getElementById('add-flag').value.trim() || '📍',
      budget: document.getElementById('add-budget').value,
      distance: document.getElementById('add-distance').value,
      vibes: vibes.length ? vibes : ['nature', 'culture', 'food'],
      seasons: seasons.length ? seasons : ['spring', 'summer', 'autumn', 'winter'],
      party: party.length ? party : ['couple', 'group'],
    });
    saveState();
    addForm.reset();
    renderDestList();
  });

  // ── History ───────────────────────────────────────────────────────
  function renderHistory() {
    historyList.innerHTML = '';
    clearHistoryBtn.hidden = state.history.length === 0;
    if (state.history.length === 0) {
      const li = document.createElement('li');
      li.className = 'history-empty';
      li.textContent = 'No trips picked yet — give it a spin!';
      historyList.append(li);
      return;
    }
    for (const item of state.history) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = `${item.flag} ${item.name}`;
      const when = document.createElement('span');
      when.className = 'when';
      when.textContent = new Date(item.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
      li.append(name, when);
      historyList.append(li);
    }
  }

  clearHistoryBtn.addEventListener('click', () => {
    state.history = [];
    saveState();
    renderHistory();
  });

  // ── Refresh ───────────────────────────────────────────────────────
  function refresh() {
    currentSegments = eligibleDestinations();
    const n = currentSegments.length;
    const favs = currentSegments.filter(isFavorite).length;
    matchCount.textContent = (n === 1 ? '1 destination on the wheel' : `${n} destinations on the wheel`) +
      (favs > 0 ? ` · ⭐ ${favs} favourite${favs === 1 ? '' : 's'} with double chance` : '');
    spinBtn.disabled = n === 0 || spinning;
    drawWheel();
  }

  spinBtn.addEventListener('click', spin);

  // ── Init ──────────────────────────────────────────────────────────
  loadState();
  syncFilterButtons();
  renderHistory();
  refresh();
})();
