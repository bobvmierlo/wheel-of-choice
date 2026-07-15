/* Wheel of Wander — couples' holiday destination picker.
 *
 * Destinations and spin history live on the server, per shared space
 * (see server.py). Your account stores your personal filter preferences,
 * so partners sharing a space can each keep their own filters. Only the
 * login token is kept in this browser.
 */
(function () {
  'use strict';

  const VIBE_LABELS = {
    nature: '🌲 nature', culture: '🏛️ culture & museums', food: '🍽️ food',
    beach: '🏖️ beach', nightlife: '🌃 nightlife', adventure: '🧗 adventure',
    wellness: '💆 wellness', winter: '⛷️ snow',
  };
  const BUDGET_LABELS = { low: '💶 low budget', mid: '💶💶 mid budget', high: '💶💶💶 high budget' };
  const DISTANCE_LABELS = { regional: '🚗 regional', europe: '✈️ Europe', longhaul: '🌏 long-haul' };
  const FAVORITE_WEIGHT = 2; // favourites get a double-width wheel segment
  const MULTI_FILTERS = ['budget', 'distance', 'vibe', 'season']; // 'party' stays single-choice

  const SEGMENT_COLORS = [
    '#ff5e7e', '#ffb84d', '#4dabff', '#6ee7a8',
    '#c084fc', '#f97362', '#38d0e0', '#facc15',
    '#fb7fb8', '#8aa9ff', '#5eddaf', '#ff9e6d',
  ];

  const TOKEN_KEY = 'wheel-of-wander-token';

  // ── State ─────────────────────────────────────────────────────────
  let wheelId = location.hash === '#citytrips' ? 'citytrips' : 'holidays';
  let token = localStorage.getItem(TOKEN_KEY) || '';
  let me = null; // { user: {id, name}, prefs, space: {code, onboarded, members} }

  const state = {
    filters: defaultFilters(),
    destinations: [],
    history: [],
    // Round state: each partner gets one veto per round.
    vetoedIds: [],
    vetoesLeft: 2,
    editingId: null,
  };

  function defaultFilters() {
    return { budget: [], distance: [], party: 'couple', vibe: [], season: [] };
  }

  function loadFilters() {
    state.filters = defaultFilters();
    const saved = (me && me.prefs && me.prefs[wheelId]) || {};
    for (const key of MULTI_FILTERS) {
      if (Array.isArray(saved[key])) state.filters[key] = [...saved[key]];
    }
    if (saved.party === 'couple' || saved.party === 'group') state.filters.party = saved.party;
  }

  let prefsTimer = null;
  function saveFilters() {
    if (!me) return;
    me.prefs[wheelId] = JSON.parse(JSON.stringify(state.filters));
    clearTimeout(prefsTimer);
    prefsTimer = setTimeout(() => {
      rootApi('/me/prefs', { method: 'PUT', body: JSON.stringify(me.prefs) }).catch(console.error);
    }, 600);
  }

  function startNewRound() {
    state.vetoedIds = [];
    state.vetoesLeft = 2;
  }

  // ── Server API ────────────────────────────────────────────────────
  async function rootApi(path, options = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`/api${path}`, { headers, ...options });
    if (res.status === 401 && token && !path.startsWith('/auth/')) {
      forceLogout(); // token expired or revoked — back to the login screen
    }
    if (!res.ok) {
      let message = '';
      try { message = (await res.json()).error; } catch { /* not JSON */ }
      throw new Error(message || `Server said ${res.status} for ${path}`);
    }
    return res.status === 204 ? null : res.json();
  }

  function api(path, options = {}) {
    return rootApi(`/wheels/${wheelId}${path}`, options);
  }

  async function loadWheelData() {
    try {
      const [destinations, history] = await Promise.all([api('/destinations'), api('/history')]);
      state.destinations = destinations;
      state.history = history;
      wheelHint.textContent = '';
    } catch (err) {
      console.error(err);
      state.destinations = [];
      state.history = [];
      wheelHint.textContent = '⚠️ Cannot reach the server — is it running? (python3 server.py)';
    }
    renderHistory();
    refresh();
  }

  function eligibleDestinations() {
    const f = state.filters;
    return state.destinations.filter((d) =>
      d.enabled &&
      !state.vetoedIds.includes(d.id) &&
      (f.budget.length === 0 || f.budget.includes(d.budget)) &&
      (f.distance.length === 0 || f.distance.includes(d.distance)) &&
      d.party.includes(f.party) &&
      (f.vibe.length === 0 || d.vibes.some((v) => f.vibe.includes(v))) &&
      (f.season.length === 0 || d.seasons.some((s) => f.season.includes(s)))
    );
  }

  // ── Elements ──────────────────────────────────────────────────────
  const authView = document.getElementById('auth-view');
  const onboardView = document.getElementById('onboard-view');
  const appView = document.getElementById('app-view');
  const wheelTabs = document.getElementById('wheel-tabs');
  const accountBar = document.getElementById('account-bar');
  const accountName = document.getElementById('account-name');
  const shareBtn = document.getElementById('share-btn');
  const logoutBtn = document.getElementById('logout-btn');

  const authForm = document.getElementById('auth-form');
  const authName = document.getElementById('auth-name');
  const authPassword = document.getElementById('auth-password');
  const authSubmit = document.getElementById('auth-submit');
  const authError = document.getElementById('auth-error');

  const onboardSubmit = document.getElementById('onboard-submit');
  const onboardError = document.getElementById('onboard-error');
  const onboardCode = document.getElementById('onboard-code');
  const onboardJoinBtn = document.getElementById('onboard-join-btn');

  const shareModal = document.getElementById('share-modal');
  const closeShareBtn = document.getElementById('close-share-btn');
  const shareCode = document.getElementById('share-code');
  const copyCodeBtn = document.getElementById('copy-code-btn');
  const shareMembers = document.getElementById('share-members');
  const joinCode = document.getElementById('join-code');
  const joinBtn = document.getElementById('join-btn');
  const shareError = document.getElementById('share-error');
  const leaveBtn = document.getElementById('leave-btn');

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
  const formTitle = document.getElementById('form-title');
  const formSubmit = document.getElementById('form-submit');
  const cancelEditBtn = document.getElementById('cancel-edit-btn');

  const historyList = document.getElementById('history-list');
  const clearHistoryBtn = document.getElementById('clear-history-btn');

  function setActive(btn, on) {
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', String(on));
  }

  // ── Views (auth → onboarding → app) ───────────────────────────────
  function showView(name) {
    authView.hidden = name !== 'auth';
    onboardView.hidden = name !== 'onboard';
    appView.hidden = name !== 'app';
    wheelTabs.hidden = name !== 'app';
    accountBar.hidden = name === 'auth';
  }

  function applyMe() {
    accountName.textContent = `👤 ${me.user.name}`;
    shareBtn.hidden = !me.space.onboarded;
    if (!me.space.onboarded) {
      document.getElementById('onboard-name').textContent = me.user.name;
      showView('onboard');
      return;
    }
    showView('app');
    loadFilters();
    syncFilterButtons();
    startNewRound();
    loadWheelData();
  }

  async function fetchMe() {
    try {
      me = await rootApi('/me');
      applyMe();
    } catch (err) {
      console.error(err);
      showView('auth');
      if (token) authError.textContent = '⚠️ Cannot reach the server — is it running?';
    }
  }

  function forceLogout() {
    token = '';
    me = null;
    localStorage.removeItem(TOKEN_KEY);
    clearTimeout(prefsTimer);
    showView('auth');
  }

  // ── Auth form ─────────────────────────────────────────────────────
  let authMode = 'login';

  document.querySelectorAll('.auth-tabs .tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      authMode = tab.dataset.mode;
      document.querySelectorAll('.auth-tabs .tab').forEach((t) => {
        t.classList.toggle('active', t === tab);
      });
      authSubmit.textContent = authMode === 'login' ? 'Log in' : 'Create account';
      authPassword.autocomplete = authMode === 'login' ? 'current-password' : 'new-password';
      authError.textContent = '';
    });
  });

  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    authError.textContent = '';
    authSubmit.disabled = true;
    try {
      const result = await rootApi(`/auth/${authMode === 'login' ? 'login' : 'register'}`, {
        method: 'POST',
        body: JSON.stringify({ name: authName.value.trim(), password: authPassword.value }),
      });
      token = result.token;
      localStorage.setItem(TOKEN_KEY, token);
      me = result.me;
      authPassword.value = '';
      applyMe();
    } catch (err) {
      authError.textContent = `⚠️ ${err.message}`;
    } finally {
      authSubmit.disabled = false;
    }
  });

  logoutBtn.addEventListener('click', async () => {
    try { await rootApi('/auth/logout', { method: 'POST' }); } catch { /* logging out anyway */ }
    forceLogout();
  });

  // ── Onboarding ────────────────────────────────────────────────────
  const onboardAnswers = { home: null, roam: 'europe', vibes: [], budget: 'mix' };

  document.querySelectorAll('#onboard-view .ob-group').forEach((group) => {
    const question = group.dataset.question;
    const multi = group.hasAttribute('data-multi');
    group.addEventListener('click', (e) => {
      const btn = e.target.closest('.seg-btn');
      if (!btn) return;
      const value = btn.dataset.value;
      if (multi) {
        const set = new Set(onboardAnswers[question]);
        set.has(value) ? set.delete(value) : set.add(value);
        onboardAnswers[question] = [...set];
        setActive(btn, set.has(value));
      } else {
        onboardAnswers[question] = value;
        group.querySelectorAll('.seg-btn').forEach((b) => setActive(b, b === btn));
      }
      onboardError.textContent = '';
    });
  });

  onboardSubmit.addEventListener('click', async () => {
    if (!onboardAnswers.home) {
      onboardError.textContent = '⚠️ Tell us where home is first 🙂';
      return;
    }
    onboardSubmit.disabled = true;
    try {
      me = await rootApi('/onboarding', { method: 'POST', body: JSON.stringify(onboardAnswers) });
      applyMe();
    } catch (err) {
      onboardError.textContent = `⚠️ ${err.message}`;
    } finally {
      onboardSubmit.disabled = false;
    }
  });

  async function joinWithCode(code, errorEl) {
    errorEl.textContent = '';
    if (!code.trim()) {
      errorEl.textContent = '⚠️ Enter a share code first';
      return;
    }
    try {
      me = await rootApi('/space/join', { method: 'POST', body: JSON.stringify({ code }) });
      if (shareModal.open) shareModal.close();
      applyMe();
    } catch (err) {
      errorEl.textContent = `⚠️ ${err.message}`;
    }
  }

  onboardJoinBtn.addEventListener('click', () => joinWithCode(onboardCode.value, onboardError));
  onboardCode.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); onboardJoinBtn.click(); }
  });

  // ── Share modal ───────────────────────────────────────────────────
  shareBtn.addEventListener('click', () => {
    shareCode.textContent = me.space.code;
    const others = me.space.members.filter((n) => n !== me.user.name);
    shareMembers.textContent = others.length
      ? `Travelling together: ${me.space.members.join(', ')} 💑`
      : 'Nobody has joined yet — send the code to your partner!';
    shareError.textContent = '';
    joinCode.value = '';
    copyCodeBtn.textContent = '📋 Copy';
    shareModal.showModal();
  });
  closeShareBtn.addEventListener('click', () => shareModal.close());

  copyCodeBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(me.space.code);
      copyCodeBtn.textContent = '✅ Copied';
    } catch {
      copyCodeBtn.textContent = '⚠️ Copy failed';
    }
  });

  joinBtn.addEventListener('click', () => joinWithCode(joinCode.value, shareError));
  joinCode.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); joinBtn.click(); }
  });

  leaveBtn.addEventListener('click', async () => {
    const alone = me.space.members.length <= 1;
    const msg = alone
      ? 'Start over with fresh wheels? Your current destinations and history will be gone for good.'
      : 'Start over with fresh wheels? You\'ll leave the shared wheels — your partner keeps them.';
    if (!confirm(msg)) return;
    try {
      me = await rootApi('/space/leave', { method: 'POST' });
      shareModal.close();
      applyMe();
    } catch (err) {
      shareError.textContent = `⚠️ ${err.message}`;
    }
  });

  // ── Wheel tabs ────────────────────────────────────────────────────
  function switchWheel(target) {
    if (spinning || target === wheelId) return;
    wheelId = target;
    if (location.hash !== (wheelId === 'holidays' ? '' : `#${wheelId}`)) {
      history.replaceState(null, '', wheelId === 'holidays' ? location.pathname : `#${wheelId}`);
    }
    syncTabs();
    loadFilters();
    syncFilterButtons();
    startNewRound();
    loadWheelData();
  }

  document.querySelectorAll('.wheel-tabs .tab').forEach((tab) => {
    tab.addEventListener('click', () => switchWheel(tab.dataset.wheel));
  });

  window.addEventListener('hashchange', () => {
    // keep the back/forward buttons working for the two wheels
    switchWheel(location.hash === '#citytrips' ? 'citytrips' : 'holidays');
  });

  function syncTabs() {
    document.querySelectorAll('.wheel-tabs .tab').forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.wheel === wheelId);
    });
  }

  // ── Wheel drawing ─────────────────────────────────────────────────
  let rotation = 0;          // current wheel rotation in radians
  let spinning = false;
  let currentSegments = [];  // destinations currently on the wheel
  let pendingResult = null;

  function isFavorite(d) {
    return !!d.favorite;
  }

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

  // Closing the dialog any other way (Esc) means "let's decide later":
  // no history entry, no veto spent.
  resultModal.addEventListener('close', () => {
    pendingResult = null;
  });

  acceptBtn.addEventListener('click', async () => {
    const result = pendingResult;
    pendingResult = null;
    resultModal.close();
    startNewRound();
    if (result) {
      wheelHint.textContent = `${result.flag} ${result.name} it is — time to book! 🧳`;
      try {
        state.history = await api('/history', {
          method: 'POST',
          body: JSON.stringify({ name: result.name, flag: result.flag }),
        });
      } catch (err) {
        console.error(err);
        wheelHint.textContent = '⚠️ Could not save to history — is the server still running?';
      }
      renderHistory();
    }
    refresh();
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

  // ── Filters (multi-select chips; "Any" clears the group) ─────────
  document.querySelectorAll('.filter-group').forEach((group) => {
    const key = group.dataset.filter;
    group.addEventListener('click', (e) => {
      const btn = e.target.closest('.seg-btn');
      if (!btn || spinning) return;
      const value = btn.dataset.value;
      if (key === 'party') {
        state.filters.party = value;
      } else if (value === 'any') {
        state.filters[key] = [];
      } else {
        const set = new Set(state.filters[key]);
        set.has(value) ? set.delete(value) : set.add(value);
        state.filters[key] = [...set];
      }
      startNewRound(); // changing preferences resets vetoes
      saveFilters();
      syncFilterButtons();
      refresh();
    });
  });

  function syncFilterButtons() {
    document.querySelectorAll('.filter-group').forEach((group) => {
      const key = group.dataset.filter;
      group.querySelectorAll('.seg-btn').forEach((b) => {
        if (key === 'party') {
          setActive(b, b.dataset.value === state.filters.party);
        } else if (b.dataset.value === 'any') {
          setActive(b, state.filters[key].length === 0);
        } else {
          setActive(b, state.filters[key].includes(b.dataset.value));
        }
      });
    });
  }

  // ── Manage destinations ───────────────────────────────────────────
  manageBtn.addEventListener('click', () => {
    exitEditMode();
    renderDestList();
    manageModal.showModal();
  });
  closeManageBtn.addEventListener('click', () => manageModal.close());
  manageModal.addEventListener('close', () => {
    exitEditMode();
    refresh();
  });

  async function patchDestination(id, changes) {
    const updated = await api(`/destinations/${id}`, { method: 'PUT', body: JSON.stringify(changes) });
    const i = state.destinations.findIndex((d) => d.id === id);
    if (i !== -1) state.destinations[i] = updated;
    return updated;
  }

  function renderDestList() {
    destList.innerHTML = '';
    for (const d of state.destinations) {
      const li = document.createElement('li');
      li.classList.toggle('disabled', !d.enabled);

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = d.enabled;
      checkbox.title = 'On the wheel?';
      checkbox.addEventListener('change', async () => {
        try {
          const updated = await patchDestination(d.id, { enabled: checkbox.checked });
          d.enabled = updated.enabled;
          li.classList.toggle('disabled', !d.enabled);
        } catch (err) {
          console.error(err);
          checkbox.checked = d.enabled; // save failed — revert the tick
        }
      });

      const star = document.createElement('button');
      star.type = 'button';
      star.className = 'star-btn';
      star.title = 'Favourites get a double chance on the wheel';
      star.textContent = d.favorite ? '⭐' : '☆';
      star.classList.toggle('starred', d.favorite);
      star.addEventListener('click', async () => {
        const updated = await patchDestination(d.id, { favorite: !d.favorite }).catch(console.error);
        if (!updated) return;
        d.favorite = updated.favorite;
        star.textContent = d.favorite ? '⭐' : '☆';
        star.classList.toggle('starred', d.favorite);
      });

      const name = document.createElement('span');
      name.className = 'dest-name';
      name.textContent = `${d.flag} ${d.name}`;

      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      meta.textContent = `${d.budget} · ${DISTANCE_LABELS[d.distance]}`;

      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'edit-btn';
      edit.title = 'Edit this destination';
      edit.textContent = '✏️';
      edit.addEventListener('click', () => enterEditMode(d));

      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'del-btn';
      del.title = 'Remove this destination';
      del.textContent = '✕';
      del.addEventListener('click', async () => {
        if (!confirm(`Remove ${d.name} for good?`)) return;
        try {
          await api(`/destinations/${d.id}`, { method: 'DELETE' });
          state.destinations = state.destinations.filter((x) => x.id !== d.id);
          if (state.editingId === d.id) exitEditMode();
          renderDestList();
        } catch (err) {
          console.error(err);
        }
      });

      li.append(checkbox, star, name, meta, edit, del);
      destList.append(li);
    }
  }

  // ── Add / edit form ───────────────────────────────────────────────
  function checkedValues(containerId) {
    return Array.from(document.querySelectorAll(`#${containerId} input:checked`)).map((i) => i.value);
  }

  function setCheckedValues(containerId, values) {
    document.querySelectorAll(`#${containerId} input`).forEach((input) => {
      input.checked = values.includes(input.value);
    });
  }

  function enterEditMode(d) {
    state.editingId = d.id;
    formTitle.textContent = `Edit ${d.name}`;
    formSubmit.textContent = '💾 Save changes';
    cancelEditBtn.hidden = false;
    document.getElementById('add-name').value = d.name;
    document.getElementById('add-flag').value = d.flag;
    document.getElementById('add-budget').value = d.budget;
    document.getElementById('add-distance').value = d.distance;
    setCheckedValues('add-vibes', d.vibes);
    setCheckedValues('add-seasons', d.seasons);
    setCheckedValues('add-party', d.party);
    addForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
    document.getElementById('add-name').focus();
  }

  function exitEditMode() {
    state.editingId = null;
    formTitle.textContent = 'Add your own';
    formSubmit.textContent = '➕ Add destination';
    cancelEditBtn.hidden = true;
    addForm.reset();
  }

  cancelEditBtn.addEventListener('click', exitEditMode);

  addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('add-name').value.trim();
    if (!name) return;
    const payload = {
      name,
      flag: document.getElementById('add-flag').value.trim() || '📍',
      budget: document.getElementById('add-budget').value,
      distance: document.getElementById('add-distance').value,
      vibes: checkedValues('add-vibes'),
      seasons: checkedValues('add-seasons'),
      party: checkedValues('add-party'),
    };
    try {
      if (state.editingId) {
        await patchDestination(state.editingId, payload);
      } else {
        const created = await api('/destinations', { method: 'POST', body: JSON.stringify(payload) });
        state.destinations.push(created);
      }
      exitEditMode();
      renderDestList();
    } catch (err) {
      console.error(err);
      alert('Could not save the destination — is the server still running?');
    }
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
      if (item.by) when.title = `Picked by ${item.by}`;
      li.append(name, when);
      historyList.append(li);
    }
  }

  clearHistoryBtn.addEventListener('click', async () => {
    if (!confirm('Clear the whole spin history? This clears it for both of you.')) return;
    try {
      await api('/history', { method: 'DELETE' });
      state.history = [];
      renderHistory();
    } catch (err) {
      console.error(err);
    }
  });

  // ── Refresh ───────────────────────────────────────────────────────
  function refresh() {
    currentSegments = eligibleDestinations();
    const n = currentSegments.length;
    const favs = currentSegments.filter(isFavorite).length;
    matchCount.textContent = n === 0
      ? 'No matches — loosen a filter or two'
      : (n === 1 ? '1 destination on the wheel' : `${n} destinations on the wheel`) +
        (favs > 0 ? ` · ⭐ ${favs} favourite${favs === 1 ? '' : 's'} with double chance` : '');
    spinBtn.disabled = n === 0 || spinning;
    drawWheel();
  }

  spinBtn.addEventListener('click', spin);

  // ── Init ──────────────────────────────────────────────────────────
  syncTabs();
  if (token) {
    fetchMe();
  } else {
    showView('auth');
  }
})();
