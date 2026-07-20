/* Wheel of Choice — shared decision wheels for holidays, city trips
 * and restaurants.
 *
 * Every wheel is a stand-alone thing on the server (see server.py):
 * it has its own entries, spin history and share code, and can be
 * shared with any number of accounts. A user can hold any number of
 * wheels. Your account stores your personal filter preferences per
 * wheel, so people sharing a wheel each keep their own filters. Only
 * the login token is kept in this browser.
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
  // Stars are per person (starred_by). One star doubles the wheel
  // segment; starred by two people triples it. Entries from before
  // per-person stars only carry the shared `favorite` flag — worth one.
  const MULTI_FILTERS = ['budget', 'distance', 'vibe', 'season']; // 'party' stays single-choice

  const WHEEL_TYPE_META = {
    holidays: { icon: '🌍', kicker: 'Your next holiday is…', noun: 'destination' },
    citytrips: { icon: '🏙️', kicker: 'Your next city trip is…', noun: 'destination' },
    restaurants: { icon: '🍽️', kicker: 'Tonight you\'re eating at…', noun: 'restaurant' },
  };

  const SEGMENT_COLORS = [
    '#ff5e7e', '#ffb84d', '#4dabff', '#6ee7a8',
    '#c084fc', '#f97362', '#38d0e0', '#facc15',
    '#fb7fb8', '#8aa9ff', '#5eddaf', '#ff9e6d',
  ];

  // Respect the OS "reduce motion" setting: shorten the wheel spin and
  // skip the confetti burst for anyone who's asked for less animation.
  const prefersReducedMotion = () =>
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // key predates the rename to Wheel of Choice — changing it would log
  // every existing browser out for nothing
  const TOKEN_KEY = 'wheel-of-wander-token';

  // ── State ─────────────────────────────────────────────────────────
  let wheelId = ''; // current wheel id — resolved from /me + the URL hash
  let token = localStorage.getItem(TOKEN_KEY) || '';
  let me = null; // { user: {id, name, admin}, prefs, wheels: [{id, type, name, code, members}] }

  // An invite link (?join=CODE) pre-fills the join flow: registering
  // through it joins that wheel right away, logging in gets the code
  // filled in wherever a join field is available.
  const urlParams = new URLSearchParams(location.search);
  let inviteCode = (urlParams.get('join') || '').toUpperCase().replace(/\s+/g, '');
  if (urlParams.has('join')) {
    urlParams.delete('join');
    const qs = urlParams.toString();
    history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : '') + location.hash);
  }

  // Set when this user just joined an existing wheel (via share code or
  // invite registration) — triggers the "star your own picks" welcome.
  let justJoined = false;
  let joinedWheelId = '';

  const state = {
    filters: defaultFilters(),
    destinations: [],
    history: [],
    // The round lives on the server so every member sees the same thing:
    // on travel wheels each member gets exactly one veto per round, and
    // an accepted pick waits for the others' thumbs-up before it lands
    // in the history. Restaurant wheels have no vetoes at all.
    round: emptyRound(),
    editingId: null,
  };

  function emptyRound() {
    return { vetoed_ids: [], my_veto_used: false, vetoes_used: 0, members: 1, pending: null };
  }

  function defaultFilters() {
    return { budget: [], distance: [], party: 'couple', vibe: [], season: [] };
  }

  function currentWheel() {
    return (me && me.wheels.find((w) => w.id === wheelId)) || null;
  }

  function wheelMeta(wheel) {
    return WHEEL_TYPE_META[(wheel && wheel.type) || 'holidays'];
  }

  function isTravelWheel(wheel = currentWheel()) {
    return !wheel || wheel.type !== 'restaurants';
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
    if (!me || !isTravelWheel()) return; // restaurant wheels have no filters
    me.prefs[wheelId] = JSON.parse(JSON.stringify(state.filters));
    clearTimeout(prefsTimer);
    prefsTimer = setTimeout(() => {
      rootApi('/me/prefs', { method: 'PUT', body: JSON.stringify(me.prefs) }).catch(console.error);
    }, 600);
  }

  function partnerNames(wheel = currentWheel()) {
    const members = (wheel && wheel.members) || [];
    const others = members.filter((n) => n !== me.user.name);
    return others.length ? others.join(' & ') : 'your partner';
  }

  // Merge fresh round state from the server; when a partner acted on my
  // pending pick in the meantime, say so in the hint.
  function applyRound(round) {
    const before = state.round;
    state.round = round;
    if (JSON.stringify(before) === JSON.stringify(round)) return;
    if (before.pending && (before.pending.mine || before.pending.i_confirmed) && !round.pending) {
      wheelHint.textContent = round.vetoed_ids.includes(before.pending.dest_id)
        ? `${before.pending.flag} ${before.pending.name} got vetoed 🙅 — spin again!`
        : finalMessage(before.pending);
    }
    renderPending();
    refresh();
  }

  function finalMessage(pick) {
    return isTravelWheel()
      ? `${pick.flag} ${pick.name} it is — time to book! 🧳`
      : `${pick.flag} ${pick.name} it is — enjoy your meal! 🍽️`;
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
      const [destinations, history, round] = await Promise.all([
        api('/destinations'), api('/history'), api('/round'),
      ]);
      state.destinations = destinations;
      state.history = history;
      state.round = round;
      wheelHint.textContent = '';
    } catch (err) {
      console.error(err);
      state.destinations = [];
      state.history = [];
      state.round = emptyRound();
      wheelHint.textContent = '⚠️ Cannot reach the server — is it running? (python3 server.py)';
    }
    renderHistory();
    renderPending();
    refresh();
  }

  function eligibleDestinations() {
    const f = state.filters;
    if (!isTravelWheel()) {
      // restaurants: no filters, no vetoes — everything ticked is on
      return state.destinations.filter((d) => d.enabled);
    }
    return state.destinations.filter((d) =>
      d.enabled &&
      !state.round.vetoed_ids.includes(d.id) &&
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
  const adminBtn = document.getElementById('admin-btn');
  const passwordBtn = document.getElementById('password-btn');
  const logoutBtn = document.getElementById('logout-btn');

  const authForm = document.getElementById('auth-form');
  const authName = document.getElementById('auth-name');
  const authPassword = document.getElementById('auth-password');
  const authSubmit = document.getElementById('auth-submit');
  const authError = document.getElementById('auth-error');

  const onboardTitle = document.getElementById('onboard-title');
  const onboardSubmit = document.getElementById('onboard-submit');
  const onboardError = document.getElementById('onboard-error');
  const onboardCode = document.getElementById('onboard-code');
  const onboardJoinBtn = document.getElementById('onboard-join-btn');
  const onboardBackRow = document.getElementById('onboard-back-row');
  const onboardBack = document.getElementById('onboard-back');
  const legacyBanner = document.getElementById('legacy-banner');
  const claimBtn = document.getElementById('claim-btn');
  const wheelNameInput = document.getElementById('wheel-name');
  const travelQuestions = document.getElementById('travel-questions');
  const restaurantHint = document.getElementById('restaurant-hint');

  const shareModal = document.getElementById('share-modal');
  const closeShareBtn = document.getElementById('close-share-btn');
  const shareWheelName = document.getElementById('share-wheel-name');
  const shareCode = document.getElementById('share-code');
  const copyCodeBtn = document.getElementById('copy-code-btn');
  const shareLink = document.getElementById('share-link');
  const copyLinkBtn = document.getElementById('copy-link-btn');
  const inviteBanner = document.getElementById('invite-banner');
  const shareMembers = document.getElementById('share-members');
  const shareError = document.getElementById('share-error');
  const leaveBtn = document.getElementById('leave-btn');

  const canvas = document.getElementById('wheel');
  const ctx = canvas.getContext('2d');
  const spinBtn = document.getElementById('spin-btn');
  const wheelHint = document.getElementById('wheel-hint');
  const matchCount = document.getElementById('match-count');
  const filtersTitle = document.getElementById('filters-title');

  const resultModal = document.getElementById('result-modal');
  const resultKicker = document.getElementById('result-kicker');
  const resultFlag = document.getElementById('result-flag');
  const resultName = document.getElementById('result-name');
  const resultTags = document.getElementById('result-tags');
  const resultNotes = document.getElementById('result-notes');
  const resultLinks = document.getElementById('result-links');
  const acceptBtn = document.getElementById('accept-btn');
  const vetoBtn = document.getElementById('veto-btn');
  const vetoNote = document.getElementById('veto-note');

  const pendingBanner = document.getElementById('pending-banner');
  const pendingText = document.getElementById('pending-text');
  const pendingActions = document.getElementById('pending-actions');
  const pendingAcceptBtn = document.getElementById('pending-accept-btn');
  const pendingVetoBtn = document.getElementById('pending-veto-btn');

  const adminModal = document.getElementById('admin-modal');
  const closeAdminBtn = document.getElementById('close-admin-btn');
  const adminUserList = document.getElementById('admin-user-list');
  const adminError = document.getElementById('admin-error');
  const adminUpdateBtn = document.getElementById('admin-update-btn');
  const adminVersion = document.getElementById('admin-version');
  const adminUpdateAvailable = document.getElementById('admin-update-available');
  const adminUpdateDot = document.getElementById('admin-update-dot');
  const updateStatus = document.getElementById('update-status');
  const footerVersion = document.getElementById('footer-version');

  const infoModal = document.getElementById('info-modal');
  const closeInfoBtn = document.getElementById('close-info-btn');
  const infoFlag = document.getElementById('info-flag');
  const infoName = document.getElementById('info-name');
  const infoTags = document.getElementById('info-tags');
  const infoNotes = document.getElementById('info-notes');
  const infoLinks = document.getElementById('info-links');
  const infoPlan = document.getElementById('info-plan');
  const infoStatus = document.getElementById('info-status');
  const statusChips = document.getElementById('status-chips');
  const tripDate = document.getElementById('trip-date');
  const infoEditHint = document.getElementById('info-edit-hint');
  const infoEditBtn = document.getElementById('info-edit-btn');

  const manageModal = document.getElementById('manage-modal');
  const manageBtn = document.getElementById('manage-btn');
  const closeManageBtn = document.getElementById('close-manage-btn');
  const destList = document.getElementById('dest-list');
  const syncCatalogBtn = document.getElementById('sync-catalog-btn');
  const syncCatalogStatus = document.getElementById('sync-catalog-status');
  const addForm = document.getElementById('add-form');
  const formTitle = document.getElementById('form-title');
  const formSubmit = document.getElementById('form-submit');
  const cancelEditBtn = document.getElementById('cancel-edit-btn');
  const addNameInput = document.getElementById('add-name');
  const addFlagInput = document.getElementById('add-flag');

  const historyList = document.getElementById('history-list');
  const clearHistoryBtn = document.getElementById('clear-history-btn');

  function setActive(btn, on) {
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', String(on));
  }

  // ── Views (auth → create/join → welcome → app) ────────────────────
  const welcomeView = document.getElementById('welcome-view');

  function showView(name) {
    authView.hidden = name !== 'auth';
    onboardView.hidden = name !== 'onboard';
    welcomeView.hidden = name !== 'welcome';
    appView.hidden = name !== 'app';
    wheelTabs.hidden = name !== 'app';
    accountBar.hidden = name === 'auth';
  }

  function hashWheelId() {
    return location.hash.replace(/^#/, '');
  }

  function syncHash() {
    if (hashWheelId() !== wheelId) {
      history.replaceState(null, '', `#${wheelId}`);
    }
  }

  function applyMe() {
    accountName.textContent = `👤 ${me.user.name}`;
    adminBtn.hidden = !me.user.admin;
    if (me.user.admin) checkForUpdate(); // light up the 🆕 dot if one's waiting
    notifyBtn.hidden = false;
    passwordBtn.hidden = false;
    syncPushSubscription(); // fire-and-forget — keeps this device buzzing for this account
    syncCalendarButton(); // shows 📆 only if the server can read calendars
    const invite = inviteCode;
    inviteCode = '';
    inviteBanner.hidden = true;
    if (justJoined && joinedWheelId) {
      // Fresh member of an existing wheel: offer to star their own
      // dream picks before the wheel appears.
      justJoined = false;
      wheelId = joinedWheelId;
      joinedWheelId = '';
      showWelcome();
      return;
    }
    justJoined = false;
    if (!me.wheels.length) {
      showAddWheel(true, invite);
      return;
    }
    // keep the wheel we were just working with (e.g. one freshly
    // created); otherwise the URL hash decides, then the first wheel
    const ids = me.wheels.map((w) => w.id);
    if (!ids.includes(wheelId)) {
      wheelId = ids.includes(hashWheelId()) ? hashWheelId() : ids[0];
    }
    shareBtn.hidden = false;
    renderTabs();
    syncHash();
    showView('app');
    loadFilters();
    syncFilterButtons();
    syncWheelChrome();
    state.round = emptyRound(); // the real round arrives with the wheel data
    loadWheelData();
    if (invite) {
      // Followed someone's invite link while already set up — offer the
      // join with the code filled in, one click away.
      showAddWheel(false, invite);
    }
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

  // ── Wheel tabs (one per wheel + "add") ────────────────────────────
  function renderTabs() {
    wheelTabs.innerHTML = '';
    for (const w of me.wheels) {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'tab';
      tab.classList.toggle('active', w.id === wheelId);
      tab.dataset.wheel = w.id;
      tab.textContent = `${wheelMeta(w).icon} ${w.name}`;
      tab.addEventListener('click', () => switchWheel(w.id));
      wheelTabs.append(tab);
    }
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'tab tab-add';
    add.title = 'Create or join another wheel';
    add.textContent = '➕';
    add.addEventListener('click', () => { if (!spinning) showAddWheel(false); });
    wheelTabs.append(add);
  }

  function switchWheel(target) {
    if (spinning || target === wheelId || !me.wheels.some((w) => w.id === target)) return;
    wheelId = target;
    syncHash();
    renderTabs();
    loadFilters();
    syncFilterButtons();
    syncWheelChrome();
    state.round = emptyRound();
    loadWheelData();
  }

  window.addEventListener('hashchange', () => {
    // keep the back/forward buttons working between wheels
    if (me && me.wheels.some((w) => w.id === hashWheelId())) switchWheel(hashWheelId());
  });

  // Per-type UI: restaurant wheels have no filters and no vetoes, and
  // their manage form drops the travel-only fields.
  function syncWheelChrome() {
    const wheel = currentWheel();
    const travel = isTravelWheel(wheel);
    document.querySelectorAll('.filter-group').forEach((g) => { g.hidden = !travel; });
    filtersTitle.textContent = travel ? 'Trip preferences' : '🍽️ Restaurant wheel';
    resultKicker.textContent = wheelMeta(wheel).kicker;
    vetoBtn.hidden = !travel;
    vetoNote.hidden = !travel;
    document.querySelectorAll('#manage-modal .travel-only').forEach((el) => { el.hidden = !travel; });
    addNameInput.placeholder = travel ? 'Destination name' : 'Restaurant name';
    addFlagInput.placeholder = travel ? 'Emoji, e.g. 🏝️' : 'Emoji, e.g. 🍕';
  }

  // ── Auth form ─────────────────────────────────────────────────────
  let authMode = 'login';

  document.querySelectorAll('.auth-tabs .tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      authMode = tab.dataset.mode;
      document.querySelectorAll('.auth-tabs .tab').forEach((t) => {
        t.classList.toggle('active', t === tab);
      });
      const registering = authMode === 'register';
      authSubmit.textContent = registering ? 'Create account' : 'Log in';
      authPassword.autocomplete = registering ? 'new-password' : 'current-password';
      // Only new passwords must clear the 8-char floor. Login stays lenient
      // so accounts created under the old 4-char rule can still get in.
      authPassword.minLength = registering ? 8 : 1;
      authPassword.placeholder = registering ? 'At least 8 characters' : 'Your password';
      authError.textContent = '';
    });
  });

  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    authError.textContent = '';
    authSubmit.disabled = true;
    try {
      const body = { name: authName.value.trim(), password: authPassword.value };
      if (authMode === 'register' && inviteCode) {
        body.code = inviteCode;
      }
      const result = await rootApi(`/auth/${authMode === 'login' ? 'login' : 'register'}`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      token = result.token;
      localStorage.setItem(TOKEN_KEY, token);
      me = result.me;
      if (me.joined) {
        justJoined = true; // landed straight on the shared wheel → welcome screen
        joinedWheelId = me.joined;
      }
      authPassword.value = '';
      applyMe();
    } catch (err) {
      authError.textContent = `⚠️ ${err.message}`;
    } finally {
      authSubmit.disabled = false;
    }
  });

  logoutBtn.addEventListener('click', async () => {
    try {
      // this device shouldn't keep buzzing for an account it logged out of
      const sub = await pushSubscription();
      if (sub) {
        await rootApi('/push/unsubscribe', {
          method: 'POST', body: JSON.stringify({ endpoint: sub.endpoint }),
        });
      }
    } catch { /* logging out anyway */ }
    try { await rootApi('/auth/logout', { method: 'POST' }); } catch { /* logging out anyway */ }
    forceLogout();
  });

  // ── Create / join a wheel ─────────────────────────────────────────
  const onboardAnswers = {
    type: 'holidays', home: null, roam: 'europe', vibes: [], budget: 'mix',
    favorites: [], // catalogue ids for the currently selected travel type
  };

  function showAddWheel(firstTime, invite = '') {
    onboardTitle.textContent = firstTime
      ? `Hi ${me.user.name} — let's build your first wheel 🎡`
      : 'Add a wheel 🎡';
    onboardBackRow.hidden = firstTime; // nothing to go back to yet
    legacyBanner.hidden = !(firstTime && me.legacy_available);
    onboardError.textContent = '';
    onboardCode.value = invite || '';
    wheelNameInput.value = '';
    loadFavCatalog();
    syncTypeUI();
    showView('onboard');
  }

  onboardBack.addEventListener('click', () => applyMe());

  const typeGroup = document.getElementById('ob-type');
  typeGroup.addEventListener('click', (e) => {
    const btn = e.target.closest('.seg-btn');
    if (!btn) return;
    onboardAnswers.type = btn.dataset.value;
    onboardAnswers.favorites = [];
    typeGroup.querySelectorAll('.seg-btn').forEach((b) => setActive(b, b === btn));
    syncTypeUI();
  });

  function syncTypeUI() {
    const travel = onboardAnswers.type !== 'restaurants';
    travelQuestions.hidden = !travel;
    restaurantHint.hidden = travel;
    wheelNameInput.placeholder = `e.g. ${WHEEL_TYPE_META[onboardAnswers.type].icon === '🌍' ? 'Holidays' : (onboardAnswers.type === 'citytrips' ? 'City trips' : 'Friday night dinners')}`;
    if (travel) renderFavList();
  }

  // the favourites picker has its own handler — only wire up the
  // single-answer question groups here
  document.querySelectorAll('#onboard-view .ob-group[data-question]').forEach((group) => {
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

  // Favourites picker: every catalogue entry of the selected travel type
  // as a toggle chip, with a little search box because the list is long.
  const favSearch = document.getElementById('fav-search');
  const favList = document.getElementById('fav-list');
  let favCatalog = null;

  async function loadFavCatalog() {
    if (favCatalog) return;
    try {
      favCatalog = await rootApi('/catalog');
      renderFavList();
    } catch (err) {
      console.error(err); // no picker then — creation works fine without it
    }
  }

  function renderFavList() {
    favList.innerHTML = '';
    const entries = (favCatalog && favCatalog[onboardAnswers.type]) || [];
    for (const entry of entries) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'seg-btn';
      btn.dataset.name = entry.name.toLowerCase();
      btn.textContent = `${entry.flag} ${entry.name}`;
      setActive(btn, onboardAnswers.favorites.includes(entry.id));
      btn.addEventListener('click', () => {
        const set = new Set(onboardAnswers.favorites);
        set.has(entry.id) ? set.delete(entry.id) : set.add(entry.id);
        onboardAnswers.favorites = [...set];
        setActive(btn, set.has(entry.id));
      });
      favList.append(btn);
    }
    favSearch.value = '';
  }

  favSearch.addEventListener('input', () => {
    const q = favSearch.value.trim().toLowerCase();
    document.querySelectorAll('#ob-favorites .seg-btn').forEach((btn) => {
      btn.hidden = q !== '' && !btn.dataset.name.includes(q);
    });
  });

  onboardSubmit.addEventListener('click', async () => {
    const travel = onboardAnswers.type !== 'restaurants';
    if (travel && !onboardAnswers.home) {
      onboardError.textContent = '⚠️ Tell us where home is first 🙂';
      return;
    }
    onboardSubmit.disabled = true;
    try {
      const payload = { type: onboardAnswers.type, name: wheelNameInput.value.trim() };
      if (travel) {
        payload.home = onboardAnswers.home;
        payload.roam = onboardAnswers.roam;
        payload.vibes = onboardAnswers.vibes;
        payload.budget = onboardAnswers.budget;
        payload.favorites = onboardAnswers.favorites;
      }
      me = await rootApi('/wheels', { method: 'POST', body: JSON.stringify(payload) });
      wheelId = me.created || wheelId;
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
      me = await rootApi('/wheels/join', { method: 'POST', body: JSON.stringify({ code }) });
      justJoined = true;
      joinedWheelId = me.joined;
      applyMe();
    } catch (err) {
      errorEl.textContent = `⚠️ ${err.message}`;
    }
  }

  claimBtn.addEventListener('click', async () => {
    claimBtn.disabled = true;
    try {
      me = await rootApi('/wheels/claim', { method: 'POST' });
      applyMe();
    } catch (err) {
      onboardError.textContent = `⚠️ ${err.message}`;
    } finally {
      claimBtn.disabled = false;
    }
  });

  onboardJoinBtn.addEventListener('click', () => joinWithCode(onboardCode.value, onboardError));
  onboardCode.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); onboardJoinBtn.click(); }
  });

  // ── Welcome (just joined someone's wheel) ─────────────────────────
  // The joiner inherits everything, so give them a voice too: pick the
  // entries they dream of and star them all in one go. Skipping is fine —
  // the star buttons in the manage panel do the same thing later.
  const welcomeSearch = document.getElementById('welcome-search');
  const welcomeList = document.getElementById('welcome-list');
  const welcomeSubmit = document.getElementById('welcome-submit');
  const welcomeSkip = document.getElementById('welcome-skip');
  const welcomeError = document.getElementById('welcome-error');
  const welcomePicks = new Set();

  async function showWelcome() {
    const wheel = currentWheel();
    document.getElementById('welcome-partner').textContent = partnerNames(wheel);
    document.getElementById('welcome-wheel').textContent =
      wheel ? `${wheelMeta(wheel).icon} ${wheel.name}` : '';
    welcomeError.textContent = '';
    welcomeSearch.value = '';
    welcomePicks.clear();
    let entries = [];
    try {
      entries = await api('/destinations');
    } catch (err) {
      console.error(err); // can't load the list — just go to the app
      applyMe();
      return;
    }
    if (!entries.length) {
      // nothing to star (an empty restaurant wheel) — straight to the app
      applyMe();
      return;
    }
    welcomeList.innerHTML = '';
    for (const d of entries) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'seg-btn';
      btn.dataset.name = d.name.toLowerCase();
      btn.textContent = `${starredByMe(d) ? '⭐ ' : ''}${d.flag} ${d.name}`;
      btn.addEventListener('click', () => {
        welcomePicks.has(d.id) ? welcomePicks.delete(d.id) : welcomePicks.add(d.id);
        setActive(btn, welcomePicks.has(d.id));
      });
      welcomeList.append(btn);
    }
    showView('welcome');
  }

  welcomeSearch.addEventListener('input', () => {
    const q = welcomeSearch.value.trim().toLowerCase();
    document.querySelectorAll('#welcome-picker .seg-btn').forEach((btn) => {
      btn.hidden = q !== '' && !btn.dataset.name.includes(q);
    });
  });

  welcomeSkip.addEventListener('click', () => applyMe());

  welcomeSubmit.addEventListener('click', async () => {
    welcomeSubmit.disabled = true;
    welcomeError.textContent = '';
    try {
      // starring also re-enables — a dream pick belongs on the wheel
      await Promise.all([...welcomePicks].map((id) => api(`/destinations/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ starred: true, enabled: true }),
      })));
      applyMe();
    } catch (err) {
      welcomeError.textContent = `⚠️ ${err.message}`;
    } finally {
      welcomeSubmit.disabled = false;
    }
  });

  // ── Share modal (per wheel) ───────────────────────────────────────
  function inviteLink(wheel) {
    return `${location.origin}${location.pathname}?join=${encodeURIComponent(wheel.code)}`;
  }

  shareBtn.addEventListener('click', () => {
    const wheel = currentWheel();
    if (!wheel) return;
    shareWheelName.textContent = `${wheelMeta(wheel).icon} ${wheel.name}`;
    shareCode.textContent = wheel.code;
    shareLink.textContent = inviteLink(wheel);
    const others = wheel.members.filter((n) => n !== me.user.name);
    shareMembers.textContent = others.length
      ? `On this wheel: ${wheel.members.join(', ')} 💑`
      : 'Nobody else is on this wheel yet — send the code!';
    shareError.textContent = '';
    copyCodeBtn.textContent = '📋 Copy';
    copyLinkBtn.textContent = '📋 Copy';
    shareModal.showModal();
  });
  closeShareBtn.addEventListener('click', () => shareModal.close());

  // The async Clipboard API only exists in secure contexts (https or
  // localhost); self-hosted installs usually run plain http on the LAN,
  // so fall back to the old textarea + execCommand('copy') trick there.
  async function copyToClipboard(text) {
    if (navigator.clipboard) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch { /* denied or unavailable — try the fallback */ }
    }
    const scratch = document.createElement('textarea');
    scratch.value = text;
    scratch.setAttribute('readonly', '');
    scratch.style.position = 'fixed';
    scratch.style.opacity = '0';
    document.body.append(scratch);
    scratch.focus();
    scratch.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch { /* nothing worked */ }
    scratch.remove();
    return copied;
  }

  copyCodeBtn.addEventListener('click', async () => {
    copyCodeBtn.textContent = (await copyToClipboard(currentWheel().code)) ? '✅ Copied' : '⚠️ Copy failed';
  });

  copyLinkBtn.addEventListener('click', async () => {
    copyLinkBtn.textContent = (await copyToClipboard(inviteLink(currentWheel()))) ? '✅ Copied' : '⚠️ Copy failed';
  });

  leaveBtn.addEventListener('click', async () => {
    const wheel = currentWheel();
    if (!wheel) return;
    const alone = wheel.members.length <= 1;
    const msg = alone
      ? `Leave ${wheel.name}? You're the only one on it, so the wheel and its history are gone for good.`
      : `Leave ${wheel.name}? The others keep the wheel — you can rejoin later with its code.`;
    if (!confirm(msg)) return;
    try {
      me = await rootApi(`/wheels/${wheel.id}/leave`, { method: 'POST' });
      shareModal.close();
      wheelId = '';
      applyMe();
    } catch (err) {
      shareError.textContent = `⚠️ ${err.message}`;
    }
  });

  // ── Push notifications ────────────────────────────────────────────
  // Web Push, the standard flavour: the server signs every push with
  // its own VAPID key and talks directly to whatever endpoint the
  // browser hands us — Apple's for iPhones, Google's for Chrome,
  // Mozilla's for Firefox. No Firebase, no accounts with anyone.
  // On iOS (16.4+) push only works once the app is on the Home Screen,
  // so the modal walks people through Add to Home Screen first.
  const notifyBtn = document.getElementById('notify-btn');
  const notifyModal = document.getElementById('notify-modal');
  const closeNotifyBtn = document.getElementById('close-notify-btn');
  const notifyState = document.getElementById('notify-state');
  const notifyEnable = document.getElementById('notify-enable');
  const notifyDisable = document.getElementById('notify-disable');
  const notifyIosHint = document.getElementById('notify-ios-hint');
  const notifyError = document.getElementById('notify-error');

  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1); // iPadOS masquerades as a Mac
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;

  function pushSupported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }

  if ('serviceWorker' in navigator) {
    // the worker only handles push — it never intercepts requests, so
    // the app keeps loading fresh from the server
    navigator.serviceWorker.register('sw.js').catch(console.error);
  }

  function urlBase64ToUint8Array(base64) {
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function pushSubscription() {
    if (!pushSupported()) return null;
    const reg = await navigator.serviceWorker.ready;
    return reg.pushManager.getSubscription();
  }

  // Called after login: re-registers this device's subscription under
  // the account that just logged in, so a browser-rotated subscription
  // (or a shared family tablet) ends up buzzing for the right person.
  async function syncPushSubscription() {
    if (!pushSupported() || Notification.permission !== 'granted') return;
    try {
      const sub = await pushSubscription();
      if (sub) {
        await rootApi('/push/subscribe', { method: 'POST', body: JSON.stringify(sub.toJSON()) });
      }
    } catch { /* notifications are a nicety — never block login on them */ }
  }

  async function renderNotifyModal() {
    notifyError.textContent = '';
    notifyEnable.hidden = true;
    notifyDisable.hidden = true;
    notifyIosHint.hidden = true;
    if (!pushSupported()) {
      if (isIOS && !isStandalone) {
        notifyState.textContent = '📲 One step to go — this needs the app on your Home Screen:';
        notifyIosHint.hidden = false;
      } else {
        notifyState.textContent = "📴 This browser doesn't support push notifications.";
      }
      return;
    }
    let server;
    try {
      server = await rootApi('/push/status');
    } catch (err) {
      notifyState.textContent = `⚠️ ${err.message}`;
      return;
    }
    if (!server.enabled) {
      notifyState.textContent = "📴 The server can't send notifications yet — the README's " +
        'notifications section explains the one-time setup (install pywebpush, restart).';
      return;
    }
    if (Notification.permission === 'denied') {
      notifyState.textContent = '🔕 Notifications are blocked for this site — allow them again ' +
        'in your browser or system settings, then come back here.';
      return;
    }
    const sub = await pushSubscription().catch(() => null);
    if (sub && Notification.permission === 'granted') {
      notifyState.textContent = '✅ Notifications are on for this device.';
      notifyDisable.hidden = false;
    } else {
      notifyState.textContent = '💤 Notifications are off for this device.';
      notifyEnable.hidden = false;
    }
  }

  notifyBtn.addEventListener('click', () => {
    notifyModal.showModal();
    renderNotifyModal();
  });
  closeNotifyBtn.addEventListener('click', () => notifyModal.close());

  notifyEnable.addEventListener('click', async () => {
    notifyError.textContent = '';
    notifyEnable.disabled = true;
    try {
      const server = await rootApi('/push/status');
      if (!server.enabled) throw new Error('the server has push notifications disabled');
      // must happen in this click handler — browsers only show the
      // permission prompt straight from a user gesture
      if (await Notification.requestPermission() !== 'granted') {
        throw new Error("permission wasn't granted");
      }
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(server.public_key),
        });
      }
      await rootApi('/push/subscribe', { method: 'POST', body: JSON.stringify(sub.toJSON()) });
    } catch (err) {
      console.error(err);
      notifyError.textContent = `⚠️ ${err.message}`;
    } finally {
      notifyEnable.disabled = false;
      renderNotifyModal();
    }
  });

  notifyDisable.addEventListener('click', async () => {
    notifyError.textContent = '';
    try {
      const sub = await pushSubscription();
      if (sub) {
        await rootApi('/push/unsubscribe', {
          method: 'POST', body: JSON.stringify({ endpoint: sub.endpoint }),
        }).catch(() => {}); // browser-side unsubscribe matters more
        await sub.unsubscribe();
      }
    } catch (err) {
      console.error(err);
      notifyError.textContent = `⚠️ ${err.message}`;
    }
    renderNotifyModal();
  });

  // ── Date polls & calendars (restaurant wheels) ────────────────────
  // After a restaurant pick, members settle an evening: a Doodle-style
  // poll on the history entry. Each member may link their own calendar
  // by its secret ICS URL so the grid pre-marks their busy evenings —
  // shown only to them; the others see only the dates people tick.
  const calendarBtn = document.getElementById('calendar-btn');
  const calendarModal = document.getElementById('calendar-modal');
  const closeCalendarBtn = document.getElementById('close-calendar-btn');
  const calendarState = document.getElementById('calendar-state');
  const calendarError = document.getElementById('calendar-error');
  const feedList = document.getElementById('feed-list');
  const feedForm = document.getElementById('feed-form');
  const feedUrl = document.getElementById('feed-url');
  const feedLabel = document.getElementById('feed-label');
  const feedAddBtn = document.getElementById('feed-add-btn');

  const infoPoll = document.getElementById('info-poll');
  const infoPollSummary = document.getElementById('info-poll-summary');
  const infoPollActions = document.getElementById('info-poll-actions');

  const pollModal = document.getElementById('poll-modal');
  const closePollBtn = document.getElementById('close-poll-btn');
  const pollTitle = document.getElementById('poll-title');
  const pollSub = document.getElementById('poll-sub');
  const pollPropose = document.getElementById('poll-propose');
  const pollVoteView = document.getElementById('poll-vote');
  const pollLegend = document.getElementById('poll-legend');
  const pollVoteLegend = document.getElementById('poll-vote-legend');
  const dateGrid = document.getElementById('date-grid');
  const dateMonth = document.getElementById('date-month');
  const datePrevBtn = document.getElementById('date-prev-btn');
  const dateNextBtn = document.getElementById('date-next-btn');
  const pollCreateBtn = document.getElementById('poll-create-btn');
  const pollRows = document.getElementById('poll-rows');
  const pollFoot = document.getElementById('poll-foot');
  const pollScrapBtn = document.getElementById('poll-scrap-btn');
  const pollError = document.getElementById('poll-error');

  // Both refreshed from the server (admin-configurable) whenever a poll's
  // availability loads — see loadPollAvailability. The defaults only stand
  // in for the moment before that first fetch returns.
  let pollHorizonDays = 60;  // how far ahead a poll can reach
  let pollEveningFrom = 17;  // local hour an evening counts as busy

  let pollEntryId = null;
  let pollSelected = new Set();  // candidate dates while proposing
  let pollViewMonth = null;      // first day of the month shown in the propose grid
  let myVote = new Set();        // this member's ticks while voting
  let pollAvail = {};            // date → 'busy'|'free'|'unknown' (viewer's own)
  let pollLinked = false;        // viewer has a calendar linked
  let pollCalEnabled = false;    // server can read calendars at all

  function isoLocal(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function prettyDate(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  }

  function currentPollEntry() {
    return state.history.find((e) => e.id === pollEntryId) || null;
  }

  function ghostBtn(parent, label, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn btn-ghost btn-small';
    b.textContent = label;
    b.addEventListener('click', onClick);
    parent.append(b);
    return b;
  }

  // Add-to-calendar without any API: a floating-time VEVENT the device
  // reads in its own zone (right for a dinner), and a Google template URL.
  function icsForPick(name, dateStr) {
    const day = dateStr.replace(/-/g, '');
    const uid = `wheel-${dateStr}-${Math.random().toString(36).slice(2)}@wheel-of-choice`;
    return [
      'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Wheel of Choice//EN', 'BEGIN:VEVENT',
      `UID:${uid}`, `DTSTART:${day}T190000`, `DTEND:${day}T210000`,
      `SUMMARY:🍽️ ${name}`, `LOCATION:${name}`, 'END:VEVENT', 'END:VCALENDAR',
    ].join('\r\n');
  }

  function downloadIcs(name, dateStr) {
    const blob = new Blob([icsForPick(name, dateStr)], { type: 'text/calendar' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `dinner-${name.replace(/[^\w]+/g, '-').toLowerCase()}.ics`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function googleCalUrl(name, dateStr) {
    const day = dateStr.replace(/-/g, '');
    const dates = `${day}T190000/${day}T210000`;  // leave the slash literal — Google wants it raw
    return 'https://calendar.google.com/calendar/render?action=TEMPLATE'
      + `&text=${encodeURIComponent('🍽️ ' + name)}&dates=${dates}`
      + `&details=${encodeURIComponent('Locked in with Wheel of Choice')}`;
  }

  // ── Info-modal poll section ───────────────────────────────────────
  function renderInfoPoll() {
    const entry = infoEntry;
    if (!entry || isTravelWheel()) { infoPoll.hidden = true; return; }
    infoPoll.hidden = false;
    const poll = entry.poll;
    infoPollActions.innerHTML = '';
    if (!poll) {
      infoPollSummary.textContent = "Great pick — now find an evening you're all free 🗳️";
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-primary btn-small';
      b.textContent = '📅 Start a date poll';
      b.addEventListener('click', () => openPollModal(entry, 'propose'));
      infoPollActions.append(b);
    } else if (poll.status === 'locked') {
      infoPollSummary.textContent = `🗓️ ${prettyDate(poll.locked_date)} — it's a date!`;
      ghostBtn(infoPollActions, '⬇️ Add to calendar (.ics)', () => downloadIcs(entry.name, poll.locked_date));
      const g = document.createElement('a');
      g.className = 'btn btn-ghost btn-small';
      g.href = googleCalUrl(entry.name, poll.locked_date);
      g.target = '_blank';
      g.rel = 'noopener noreferrer';
      g.textContent = '📆 Google Calendar';
      infoPollActions.append(g);
      const scrap = document.createElement('button');
      scrap.type = 'button';
      scrap.className = 'linklike';
      scrap.textContent = 'Scrap the date & poll again…';
      scrap.addEventListener('click', () => { pollEntryId = entry.id; scrapPoll(); });
      infoPollActions.append(scrap);
    } else {
      const voted = poll.votes.length;
      infoPollSummary.textContent = `${poll.dates.length} evenings up · ${voted} of ${poll.members} voted`
        + (poll.waiting_names.length ? ` · waiting for ${poll.waiting_names.join(' & ')}` : '');
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-primary btn-small';
      b.textContent = '🗳️ Vote / view poll';
      b.addEventListener('click', () => openPollModal(entry, 'vote'));
      infoPollActions.append(b);
    }
  }

  // ── Poll modal ────────────────────────────────────────────────────
  async function loadPollAvailability(fresh = false) {
    pollAvail = {};
    pollLinked = false;
    pollCalEnabled = false;
    try {
      const today = new Date(); today.setHours(0, 0, 0, 0);
      // ask for the widest window we support; the server clamps to its own
      // horizon and tells us what that (admin-set) horizon actually is.
      // fresh=1 (when starting a poll) skips the feed cache for a live pull.
      const av = await rootApi(`/me/availability?from=${isoLocal(today)}&days=366${fresh ? '&fresh=1' : ''}`);
      pollCalEnabled = !!(av && av.enabled);
      pollLinked = !!(av && av.linked);
      if (av && Number.isFinite(av.horizon_days)) pollHorizonDays = av.horizon_days;
      if (av && Number.isFinite(av.evening_from)) pollEveningFrom = av.evening_from;
      if (pollLinked && av.days) pollAvail = av.days;
    } catch { /* availability is a hint, never a blocker */ }
  }

  // 17 → "5pm", 0 → "midnight", 12 → "noon" — a friendly evening-start label
  function fmtHour(h) {
    if (h === 0) return 'midnight';
    if (h === 12) return 'noon';
    return `${((h + 11) % 12) + 1}${h < 12 ? 'am' : 'pm'}`;
  }

  function setPollLegend(el) {
    if (pollLinked) {
      el.innerHTML = `<span class="cal-dot busy"></span>busy that evening (from ${fmtHour(pollEveningFrom)})`
        + '<span class="cal-dot free"></span>free — <em>from your calendar; all still tappable</em>';
    } else if (pollCalEnabled) {
      el.textContent = '📆 Link your calendar (top bar) and your busy evenings mark themselves.';
    } else {
      el.textContent = '';
    }
  }

  async function openPollModal(entry, mode) {
    pollEntryId = entry.id;
    pollError.textContent = '';
    // starting a poll → pull the freshest calendar; viewing an existing one
    // rides the cache (fast, and busy hints are only ever hints there)
    await loadPollAvailability(mode === 'propose');
    const latest = currentPollEntry() || entry;
    if (mode === 'propose' && !latest.poll) showPollPropose(latest);
    else showPollVote(latest);
    if (!pollModal.open) pollModal.showModal();
  }

  function monthStart(d) {
    return new Date(d.getFullYear(), d.getMonth(), 1);
  }

  // The propose grid pages a month at a time. Days before today or beyond
  // the poll horizon are shown but disabled, and the ‹ › buttons stop at
  // those same bounds so you can only land on dates a poll would accept.
  function buildDateGrid() {
    dateGrid.innerHTML = '';
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const todayIso = isoLocal(today);
    const horizon = new Date(today); horizon.setDate(today.getDate() + pollHorizonDays);
    const horizonIso = isoLocal(horizon);
    if (!pollViewMonth) pollViewMonth = monthStart(today);

    const first = pollViewMonth;
    const lead = (first.getDay() + 6) % 7;  // blank cells before the 1st (Monday-first)
    const daysInMonth = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
    for (let i = 0; i < lead; i++) {
      const blank = document.createElement('div');
      blank.className = 'day-cell empty';
      dateGrid.append(blank);
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const day = new Date(first.getFullYear(), first.getMonth(), d);
      const iso = isoLocal(day);
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'day-cell';
      cell.textContent = String(d);
      if (iso < todayIso || iso > horizonIso) {
        cell.disabled = true;
      } else {
        cell.dataset.date = iso;
        if (pollSelected.has(iso)) cell.classList.add('selected');
        const st = pollAvail[iso];
        if (st === 'busy') { cell.classList.add('busy'); cell.title = "you're busy that evening"; }
        else if (st === 'unknown') cell.classList.add('unknown');
        cell.addEventListener('click', () => {
          if (pollSelected.has(iso)) pollSelected.delete(iso); else pollSelected.add(iso);
          cell.classList.toggle('selected');
          syncCreateBtn();
        });
      }
      dateGrid.append(cell);
    }

    dateMonth.textContent = first.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    datePrevBtn.disabled = first <= monthStart(today);
    dateNextBtn.disabled = new Date(first.getFullYear(), first.getMonth() + 1, 1) > horizon;
  }

  function shiftPollMonth(delta) {
    const base = pollViewMonth || monthStart(new Date());
    pollViewMonth = new Date(base.getFullYear(), base.getMonth() + delta, 1);
    buildDateGrid();
  }

  function syncCreateBtn() {
    pollCreateBtn.disabled = pollSelected.size < 2;
    pollCreateBtn.textContent = pollSelected.size < 2
      ? 'Pick at least two evenings' : `📅 Put ${pollSelected.size} evenings up`;
  }

  function showPollPropose(entry) {
    pollTitle.textContent = '🗳️ Find an evening';
    pollSub.textContent = `Pick the evenings that could work for ${entry.flag} ${entry.name}, then put them to the group.`;
    pollSelected = new Set();
    pollViewMonth = null;  // always open on the current month
    setPollLegend(pollLegend);
    buildDateGrid();
    syncCreateBtn();
    pollPropose.hidden = false;
    pollVoteView.hidden = true;
  }

  function showPollVote(entry) {
    const poll = entry.poll;
    if (!poll) { showPollPropose(entry); return; }
    pollTitle.textContent = poll.status === 'locked' ? "🗓️ It's a date" : '🗳️ When can everyone make it?';
    pollSub.textContent = `${entry.flag} ${entry.name} · started by ${poll.by_name}`;
    myVote = new Set(poll.my_dates || []);
    setPollLegend(pollVoteLegend);
    renderPollRows(entry);
    pollPropose.hidden = true;
    pollVoteView.hidden = false;
  }

  function renderPollRows(entry) {
    const poll = entry.poll;
    pollRows.innerHTML = '';
    for (const date of poll.dates) {
      const li = document.createElement('li');
      li.className = 'poll-row';
      const main = document.createElement('div');
      main.className = 'poll-row-main';
      const dl = document.createElement('div');
      dl.className = 'poll-row-date';
      dl.textContent = prettyDate(date);
      const st = pollAvail[date];
      if (st === 'busy' || st === 'free') {
        const dot = document.createElement('span');
        dot.className = `cal-dot ${st}`;
        dl.append(dot);
      }
      const voters = document.createElement('div');
      voters.className = 'poll-row-voters';
      const yes = poll.votes.filter((v) => v.dates.includes(date)).map((v) => v.name);
      voters.textContent = yes.length ? `👍 ${yes.join(' & ')}` : 'no one yet';
      main.append(dl, voters);
      li.append(main);
      if (poll.status === 'locked') {
        if (poll.locked_date === date) {
          const badge = document.createElement('span');
          badge.className = 'poll-locked';
          badge.textContent = '🔒 locked';
          li.append(badge);
        }
      } else {
        const tick = document.createElement('button');
        tick.type = 'button';
        tick.className = 'poll-tick' + (myVote.has(date) ? ' on' : '');
        tick.textContent = myVote.has(date) ? '✓ me' : 'I can';
        tick.addEventListener('click', () => toggleVote(date));
        li.append(tick);
        if (poll.unanimous.includes(date)) {
          const lock = document.createElement('button');
          lock.type = 'button';
          lock.className = 'btn btn-primary btn-small';
          lock.textContent = 'Lock it in 🔒';
          lock.addEventListener('click', () => lockDate(date));
          li.append(lock);
        }
      }
      pollRows.append(li);
    }
    if (poll.status === 'locked') {
      pollFoot.textContent = `Locked in by ${poll.locked_by_name}. Enjoy! 🎉`;
    } else if (poll.my_dates == null) {
      pollFoot.textContent = 'Tick the evenings you can make.';
    } else if (poll.waiting_names.length) {
      pollFoot.textContent = `Waiting for ${poll.waiting_names.join(' & ')} to vote.`;
    } else if (!poll.unanimous.length) {
      pollFoot.textContent = "No evening works for everyone yet 😬 — scrap it and try different dates.";
    } else {
      pollFoot.textContent = "Everyone's voted — lock in an evening that works! 🔒";
    }
    pollScrapBtn.hidden = false;
  }

  function syncPollFromHistory(history) {
    state.history = history;
    renderHistory();
    if (infoEntry) {
      const updated = state.history.find((e) => e.id === infoEntry.id);
      if (updated) infoEntry = updated;
      renderInfoPoll();
    }
    if (pollModal.open && pollPropose.hidden) {  // don't disturb an in-progress proposal
      const entry = currentPollEntry();
      if (entry) showPollVote(entry); else pollModal.close();
    }
  }

  async function toggleVote(date) {
    if (myVote.has(date)) myVote.delete(date); else myVote.add(date);
    pollError.textContent = '';
    try {
      const res = await api(`/history/${pollEntryId}/poll/votes`, {
        method: 'PUT', body: JSON.stringify({ dates: [...myVote] }),
      });
      syncPollFromHistory(res.history);
    } catch (err) {
      pollError.textContent = `⚠️ ${err.message}`;
    }
  }

  async function lockDate(date) {
    pollError.textContent = '';
    try {
      const res = await api(`/history/${pollEntryId}/poll/lock`, {
        method: 'POST', body: JSON.stringify({ date }),
      });
      syncPollFromHistory(res.history);
    } catch (err) {
      pollError.textContent = `⚠️ ${err.message}`;
    }
  }

  async function scrapPoll() {
    if (!confirm('Scrap this date poll for everyone on the wheel?')) return;
    pollError.textContent = '';
    try {
      const res = await api(`/history/${pollEntryId}/poll`, { method: 'DELETE' });
      if (pollModal.open) pollModal.close();
      syncPollFromHistory(res.history);
    } catch (err) {
      pollError.textContent = `⚠️ ${err.message}`;
    }
  }

  pollCreateBtn.addEventListener('click', async () => {
    const dates = [...pollSelected];
    if (dates.length < 2) return;
    pollError.textContent = '';
    pollCreateBtn.disabled = true;
    try {
      const res = await api(`/history/${pollEntryId}/poll`, {
        method: 'POST', body: JSON.stringify({ dates }),
      });
      syncPollFromHistory(res.history);
      const entry = currentPollEntry();
      if (entry) showPollVote(entry);
    } catch (err) {
      pollError.textContent = `⚠️ ${err.message}`;
      syncCreateBtn();
    }
  });

  datePrevBtn.addEventListener('click', () => shiftPollMonth(-1));
  dateNextBtn.addEventListener('click', () => shiftPollMonth(1));

  pollScrapBtn.addEventListener('click', scrapPoll);
  closePollBtn.addEventListener('click', () => pollModal.close());

  // Live updates: a partner's vote or a fresh lock lands via the 5s poll.
  function reflectPollUpdates() {
    if (infoEntry) {
      const updated = state.history.find((e) => e.id === infoEntry.id);
      if (updated) { infoEntry = updated; renderInfoPoll(); }
    }
    if (pollModal.open && pollPropose.hidden) {
      const entry = currentPollEntry();
      if (entry) showPollVote(entry); else pollModal.close();
    }
  }

  // ── My calendars modal ────────────────────────────────────────────
  async function syncCalendarButton() {
    try {
      const info = await rootApi('/me/calendars');
      calendarBtn.hidden = !info.enabled;
    } catch {
      calendarBtn.hidden = true;
    }
  }

  async function renderCalendarModal() {
    calendarError.textContent = '';
    try {
      const info = await rootApi('/me/calendars');
      if (!info.enabled) {
        calendarState.textContent = "📴 This server can't read calendars yet — polls still work, "
          + 'just without the busy-evening hints.';
        feedForm.hidden = true;
        feedList.innerHTML = '';
        return;
      }
      feedList.innerHTML = '';
      for (const feed of info.feeds) {
        const li = document.createElement('li');
        li.className = 'feed-row';
        const main = document.createElement('div');
        main.className = 'feed-row-main';
        const lbl = document.createElement('div');
        lbl.textContent = feed.label;
        const host = document.createElement('div');
        host.className = 'feed-row-host';
        host.textContent = feed.host;
        main.append(lbl, host);
        const rm = document.createElement('button');
        rm.type = 'button';
        rm.className = 'btn btn-ghost btn-small';
        rm.title = 'Remove this calendar';
        rm.textContent = '✕';
        rm.addEventListener('click', () => removeFeed(feed.id));
        li.append(main, rm);
        feedList.append(li);
      }
      feedForm.hidden = info.feeds.length >= 4;
      calendarState.textContent = info.feeds.length
        ? `${info.feeds.length} calendar${info.feeds.length === 1 ? '' : 's'} linked — only you see your busy evenings.`
        : 'No calendars linked yet.';
    } catch (err) {
      calendarState.textContent = `⚠️ ${err.message}`;
    }
  }

  async function removeFeed(id) {
    calendarError.textContent = '';
    try {
      await rootApi(`/me/calendars/${id}`, { method: 'DELETE' });
      await renderCalendarModal();
    } catch (err) {
      calendarError.textContent = `⚠️ ${err.message}`;
    }
  }

  calendarBtn.addEventListener('click', () => { calendarModal.showModal(); renderCalendarModal(); });
  closeCalendarBtn.addEventListener('click', () => calendarModal.close());

  // ── Change your own password ─────────────────────────────────────
  const passwordModal = document.getElementById('password-modal');
  const closePasswordBtn = document.getElementById('close-password-btn');
  const passwordForm = document.getElementById('password-form');
  const passwordCurrent = document.getElementById('password-current');
  const passwordNew = document.getElementById('password-new');
  const passwordConfirm = document.getElementById('password-confirm');
  const passwordSubmit = document.getElementById('password-submit');
  const passwordState = document.getElementById('password-state');
  const passwordError = document.getElementById('password-error');

  passwordBtn.addEventListener('click', () => {
    passwordForm.reset();
    passwordState.textContent = '';
    passwordError.textContent = '';
    passwordModal.showModal();
    passwordCurrent.focus();
  });
  closePasswordBtn.addEventListener('click', () => passwordModal.close());

  passwordForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    passwordError.textContent = '';
    passwordState.textContent = '';
    if (passwordNew.value !== passwordConfirm.value) {
      passwordError.textContent = '⚠️ The two new passwords don\'t match';
      return;
    }
    passwordSubmit.disabled = true;
    try {
      await rootApi('/me/password', {
        method: 'PUT',
        body: JSON.stringify({ current: passwordCurrent.value, new: passwordNew.value }),
      });
      passwordForm.reset();
      passwordState.textContent = '✅ Password changed';
      setTimeout(() => { if (passwordModal.open) passwordModal.close(); }, 1200);
    } catch (err) {
      passwordError.textContent = `⚠️ ${err.message}`;
    } finally {
      passwordSubmit.disabled = false;
    }
  });

  feedForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    calendarError.textContent = '';
    feedAddBtn.disabled = true;
    try {
      const res = await rootApi('/me/calendars', {
        method: 'POST',
        body: JSON.stringify({ url: feedUrl.value.trim(), label: feedLabel.value.trim() }),
      });
      feedUrl.value = '';
      feedLabel.value = '';
      await renderCalendarModal();
      calendarState.textContent = `Linked ✓ — ${res.busy_days} busy evening${res.busy_days === 1 ? '' : 's'} spotted in the next couple of months.`;
    } catch (err) {
      calendarError.textContent = `⚠️ ${err.message}`;
    } finally {
      feedAddBtn.disabled = false;
    }
  });

  // ── Admin modal ───────────────────────────────────────────────────
  adminBtn.addEventListener('click', async () => {
    adminError.textContent = '';
    adminUserList.innerHTML = '';
    adminModal.showModal();
    loadVersion(); // another admin may have updated in the meantime
    loadAdminSettings(); // another admin may have retuned the polls
    startUpdatePolling(); // watch git for a newer commit while the panel's open
    try {
      renderAdminUsers(await rootApi('/admin/users'));
    } catch (err) {
      adminError.textContent = `⚠️ ${err.message}`;
    }
  });
  closeAdminBtn.addEventListener('click', () => adminModal.close());
  adminModal.addEventListener('close', stopUpdatePolling);

  // ── Poll settings (timezone, evening hour & horizon) ──────────────
  const settingTimezone = document.getElementById('setting-timezone');
  const tzOptions = document.getElementById('tz-options');
  const settingEveningFrom = document.getElementById('setting-evening-from');
  const settingHorizon = document.getElementById('setting-horizon');
  const settingsSaveBtn = document.getElementById('settings-save-btn');
  const settingsStatus = document.getElementById('settings-status');
  let tzListLoaded = false;

  function fillSettings(s) {
    settingTimezone.value = s.timezone;
    settingEveningFrom.value = s.evening_from;
    settingHorizon.value = s.poll_horizon_days;
    const [eMin, eMax] = s.bounds.evening_from;
    const [hMin, hMax] = s.bounds.poll_horizon_days;
    settingEveningFrom.min = eMin; settingEveningFrom.max = eMax;
    settingHorizon.min = hMin; settingHorizon.max = hMax;
    // the zone list only rides along with the GET — populate the datalist once
    if (Array.isArray(s.zones) && !tzListLoaded) {
      tzOptions.innerHTML = '';
      for (const z of s.zones) {
        const opt = document.createElement('option');
        opt.value = z;
        tzOptions.append(opt);
      }
      tzListLoaded = true;
    }
    // keep the propose grid in step with what's saved
    pollHorizonDays = s.poll_horizon_days;
    pollEveningFrom = s.evening_from;
  }

  async function loadAdminSettings() {
    settingsStatus.textContent = '';
    try {
      fillSettings(await rootApi('/admin/settings'));
    } catch (err) {
      settingsStatus.textContent = `⚠️ ${err.message}`;
    }
  }

  settingsSaveBtn.addEventListener('click', async () => {
    settingsStatus.textContent = '⏳ Saving…';
    settingsSaveBtn.disabled = true;
    try {
      const s = await rootApi('/admin/settings', {
        method: 'PUT',
        body: JSON.stringify({
          timezone: settingTimezone.value.trim(),
          evening_from: Number(settingEveningFrom.value),
          poll_horizon_days: Number(settingHorizon.value),
        }),
      });
      fillSettings(s);
      settingsStatus.textContent = '✅ Saved — polls use these straight away.';
    } catch (err) {
      settingsStatus.textContent = `⚠️ ${err.message}`;
    } finally {
      settingsSaveBtn.disabled = false;
    }
  });

  // ── Backup & restore ──────────────────────────────────────────────
  const adminBackupBtn = document.getElementById('admin-backup-btn');
  const adminRestoreBtn = document.getElementById('admin-restore-btn');
  const adminRestoreFile = document.getElementById('admin-restore-file');
  const backupStatus = document.getElementById('backup-status');

  adminBackupBtn.addEventListener('click', async () => {
    backupStatus.textContent = '';
    try {
      // fetch by hand: a plain download link can't carry the auth header
      const res = await fetch('/api/admin/backup', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`Server said ${res.status}`);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `wheel-of-choice-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      backupStatus.textContent = '✅ Backup downloaded — tuck it away somewhere safe.';
    } catch (err) {
      backupStatus.textContent = `⚠️ ${err.message}`;
    }
  });

  adminRestoreBtn.addEventListener('click', () => {
    adminRestoreFile.value = '';
    adminRestoreFile.click();
  });

  adminRestoreFile.addEventListener('change', async () => {
    const file = adminRestoreFile.files[0];
    if (!file) return;
    if (!confirm(`Restore "${file.name}"? This replaces ALL current data — accounts, wheels and history — with the backup's contents.`)) return;
    backupStatus.textContent = '⏳ Restoring…';
    try {
      const text = await file.text();
      JSON.parse(text); // catch garbage before it travels
      const res = await rootApi('/admin/restore', { method: 'POST', body: text });
      if (res.relogin) {
        alert('Backup restored. Your account isn\'t in this backup, so please log in again.');
        forceLogout();
        return;
      }
      backupStatus.textContent = '✅ Backup restored.';
      me = await rootApi('/me');
      applyMe();
      renderAdminUsers(await rootApi('/admin/users'));
    } catch (err) {
      backupStatus.textContent = `⚠️ ${err.message}`;
    }
  });

  async function adminAction(request) {
    adminError.textContent = '';
    try {
      renderAdminUsers(await request);
    } catch (err) {
      adminError.textContent = `⚠️ ${err.message}`;
    }
  }

  function adminActionBtn(label, title, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-ghost btn-small';
    btn.textContent = label;
    btn.title = title;
    btn.addEventListener('click', onClick);
    return btn;
  }

  function renderAdminUsers(users) {
    adminUserList.innerHTML = '';
    for (const u of users) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'dest-name';
      name.textContent = `${u.admin ? '🛡️' : '👤'} ${u.name}${u.id === me.user.id ? ' (you)' : ''}`;
      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      const wheels = `${u.wheel_count} wheel${u.wheel_count === 1 ? '' : 's'}`;
      meta.textContent = u.sharing_with.length
        ? `${wheels} · shares with ${u.sharing_with.join(', ')}`
        : wheels;
      li.append(name, meta);

      if (u.id !== me.user.id) {
        li.append(adminActionBtn(
          u.admin ? 'Remove admin' : 'Make admin',
          u.admin ? 'Take away admin rights' : 'Give admin rights',
          () => adminAction(rootApi(`/admin/users/${u.id}`, {
            method: 'PUT',
            body: JSON.stringify({ admin: !u.admin }),
          }))
        ));
        li.append(adminActionBtn('🔑 Reset password',
          'Set a temporary password and log them out everywhere', async () => {
            const pw = prompt(`Set a temporary password for ${u.name} (at least 8 characters).\n`
              + `They'll be logged out on every device and come back in with it — `
              + `then they can change it themselves.`);
            if (pw === null) return; // cancelled
            if (pw.length < 8) {
              adminError.textContent = '⚠️ That password is too short — at least 8 characters.';
              return;
            }
            adminError.textContent = '';
            try {
              await rootApi(`/admin/users/${u.id}/password`, {
                method: 'PUT',
                body: JSON.stringify({ new: pw }),
              });
              adminError.textContent = `✅ ${u.name}'s password was reset — pass the new one along.`;
            } catch (err) {
              adminError.textContent = `⚠️ ${err.message}`;
            }
          }));
        if (u.sharing_with.length) {
          li.append(adminActionBtn('Unshare', 'Pull them out of every wheel they share', () => {
            if (!confirm(`Pull ${u.name} out of every wheel they share with ${u.sharing_with.join(', ')}? They keep their account and any wheels of their own.`)) return;
            adminAction(rootApi(`/admin/users/${u.id}/unshare`, { method: 'POST' }));
          }));
        }
        li.append(adminActionBtn('✕ Delete', 'Delete this account for good', () => {
          if (!confirm(`Delete ${u.name}'s account for good? Wheels they share with others stay with the others.`)) return;
          adminAction(rootApi(`/admin/users/${u.id}`, { method: 'DELETE' }));
        }));
      }
      adminUserList.append(li);
    }
  }

  // ── Server version & updates ──────────────────────────────────────
  // /api/version tells us which commit is running and when the server
  // process started. After an update request we poll it: the updater
  // restarting the server changes server_started (and, if the pull
  // brought something new, the commit) — that's our success signal.
  const UPDATE_POLL_MS = 3000;
  const UPDATE_TIMEOUT_MS = 3 * 60 * 1000;
  let serverVersion = null;
  let updateTimer = null;

  async function fetchVersion() {
    const res = await fetch('/api/version');
    if (!res.ok) throw new Error(`Server said ${res.status}`);
    return res.json();
  }

  function formatDateTime(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? iso
      : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  function renderVersion() {
    const v = serverVersion;
    if (!v || !v.commit) {
      footerVersion.textContent = '';
      adminVersion.textContent = '';
      return;
    }
    const when = formatDateTime(v.commit_date);
    footerVersion.textContent = `running ${v.commit} · last updated ${when}`;
    adminVersion.textContent =
      `Currently running commit ${v.commit}` +
      (v.commit_subject ? ` (“${v.commit_subject}”)` : '') +
      ` · last updated ${when}.`;
  }

  async function loadVersion() {
    try {
      serverVersion = await fetchVersion();
    } catch {
      serverVersion = null;
    }
    renderVersion();
  }

  function watchUpdate(baseline) {
    clearInterval(updateTimer);
    const startedAt = Date.now();
    updateStatus.textContent = '⏳ Update requested — waiting for the server to pull the latest version and restart…';
    updateTimer = setInterval(async () => {
      let v;
      try {
        v = await fetchVersion();
      } catch {
        // unreachable mid-restart — that's expected, keep polling
        updateStatus.textContent = '🔄 Server is restarting…';
        return;
      }
      if (!baseline || v.server_started !== baseline.server_started) {
        clearInterval(updateTimer);
        adminUpdateBtn.disabled = false;
        serverVersion = v;
        renderVersion();
        updateStatus.textContent = !baseline || v.commit !== baseline.commit
          ? `✅ Updated! Now running ${v.commit}${v.commit_subject ? ` (“${v.commit_subject}”)` : ''}.`
          : '✅ Server restarted — it was already on the latest version.';
        checkForUpdate(true); // refresh the badge now we've moved
        return;
      }
      if (Date.now() - startedAt > UPDATE_TIMEOUT_MS) {
        clearInterval(updateTimer);
        adminUpdateBtn.disabled = false;
        updateStatus.textContent = v.update_pending
          ? '⚠️ Nothing picked up the update request — are the updater units from deploy/ installed and enabled?'
          : '⚠️ The update request was picked up, but the server never restarted — the git pull may have failed. Check `journalctl -u wheel-of-choice-update` on the server.';
      }
    }, UPDATE_POLL_MS);
  }

  adminUpdateBtn.addEventListener('click', async () => {
    if (!confirm('Pull the latest version from git and restart the server? Everyone is briefly disconnected.')) return;
    adminError.textContent = '';
    updateStatus.textContent = '';
    adminUpdateBtn.disabled = true;
    let baseline = serverVersion;
    try {
      baseline = await fetchVersion(); // fresh baseline to compare against
    } catch { /* keep the last known one */ }
    try {
      await rootApi('/admin/update', { method: 'POST' });
    } catch (err) {
      adminUpdateBtn.disabled = false;
      updateStatus.textContent = `⚠️ ${err.message}`;
      return;
    }
    watchUpdate(baseline);
  });

  // ── "Update available" check ──────────────────────────────────────
  // Poll the git remote (server-side, cached) so the panel — and a 🆕 dot
  // on the Admin button — flag when a newer commit is waiting to be pulled.
  const UPDATE_CHECK_POLL_MS = 60 * 1000;
  let updateCheckTimer = null;

  function renderUpdateAvailable(info) {
    const available = !!(info && info.checked && info.update_available);
    adminUpdateDot.hidden = !available;
    if (!info || !info.checked) {
      adminUpdateAvailable.hidden = true;
      return;
    }
    if (available) {
      adminUpdateAvailable.hidden = false;
      adminUpdateAvailable.textContent =
        `🆕 A newer version is on git (${info.latest} on ${info.branch}) — `
        + `you're running ${info.current}. Hit “Update & restart” to pull it.`;
    } else {
      adminUpdateAvailable.hidden = false;
      adminUpdateAvailable.textContent = '✅ Up to date with the git remote.';
    }
  }

  async function checkForUpdate(force = false) {
    try {
      const info = await rootApi(`/admin/update-check${force ? '?force=1' : ''}`);
      renderUpdateAvailable(info);
    } catch { /* offline or not admin — leave the last state be */ }
  }

  function startUpdatePolling() {
    checkForUpdate();
    clearInterval(updateCheckTimer);
    updateCheckTimer = setInterval(checkForUpdate, UPDATE_CHECK_POLL_MS);
  }

  function stopUpdatePolling() {
    clearInterval(updateCheckTimer);
    updateCheckTimer = null;
  }

  // ── Wheel drawing ─────────────────────────────────────────────────
  let rotation = 0;          // current wheel rotation in radians
  let spinning = false;
  let currentSegments = [];  // entries currently on the wheel
  let pendingResult = null;

  function starCount(d) {
    const stars = (d.starred_by || []).length;
    return stars || (d.favorite ? 1 : 0);
  }

  function isFavorite(d) {
    return starCount(d) > 0;
  }

  function segWeight(d) {
    // every member's star widens the slice: 1 star → double, 2 → triple,
    // 3+ (wheels of three or four) → quadruple, where it caps
    return 1 + Math.min(starCount(d), 3);
  }

  function starIcon(d) {
    return starCount(d) >= 2 ? '🌟' : (isFavorite(d) ? '⭐' : '☆');
  }

  function starredByMe(d) {
    return (d.starred_by || []).includes(me.user.id);
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
      const empty = isTravelWheel()
        ? ['No destinations match', '— loosen the filters!']
        : ['Nothing on the wheel yet', '— add some restaurants!'];
      ctx.fillText(empty[0], cx, cy - size * 0.03);
      ctx.fillText(empty[1], cx, cy + size * 0.03);
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
      // Left-anchor and position manually: WebKit (iOS Safari) ignores
      // textAlign for strings that take the complex text path (emoji,
      // flags), which pushed right-aligned labels outside the wheel.
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(20, 10, 40, 0.9)';
      const fontSize = Math.max(11, Math.min(20, (radius * seg) / 3.2, size * 0.032));
      ctx.font = `700 ${fontSize}px system-ui, sans-serif`;
      let label = `${isFavorite(d) ? `${starIcon(d)} ` : ''}${d.flag} ${d.name}`;
      const maxWidth = radius * 0.62;
      while (ctx.measureText(label).width > maxWidth && label.length > 6) {
        label = label.slice(0, -2).trimEnd() + '…';
      }
      ctx.fillText(label, radius - 14 - ctx.measureText(label).width, 0);
      ctx.restore();
    }

    // Hub
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.21, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fill();
  }

  // Angular extent of each segment (before wheel rotation), sized by
  // weight: one star doubles the arc, stars from two members triple it.
  function segmentBounds() {
    const weights = currentSegments.map(segWeight);
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

    // With reduced motion the wheel settles quickly onto its pick instead
    // of the long dramatic spin — same landing, far less movement.
    const reduce = prefersReducedMotion();
    const startRotation = rotation;
    const extraTurns = reduce ? 0 : 5 + Math.random() * 3;
    const targetRotation = startRotation + extraTurns * Math.PI * 2 + Math.random() * Math.PI * 2;
    const duration = reduce ? 700 : 4400;
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

  // ── Entry info & links ────────────────────────────────────────────
  // Seeded destinations carry curated links (Wikivoyage/Wikipedia by
  // default); for travel entries without links — older databases,
  // home-made destinations, deleted history entries — build the same
  // defaults from the name. Both wikis keep redirects for alternate
  // spellings. Restaurants get no wiki fallback — just your own links.
  function fallbackLinks(name) {
    if (!isTravelWheel()) return [];
    const slug = encodeURIComponent(name.replace(/ /g, '_'));
    return [
      { label: 'Wikivoyage travel guide', url: `https://en.wikivoyage.org/wiki/${slug}` },
      { label: 'Wikipedia', url: `https://en.wikipedia.org/wiki/${slug}` },
    ];
  }

  function destLinks(d) {
    return (d.links && d.links.length) ? d.links : fallbackLinks(d.name);
  }

  function renderLinkList(el, links) {
    el.innerHTML = '';
    for (const link of links) {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = link.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = link.label || link.url;
      li.append(a);
      el.append(li);
    }
  }

  // Booking-search links built from the name — the tabs you actually
  // open once a pick is made. Zero data to maintain. Restaurants get a
  // maps search instead of flights.
  function planLinks(name) {
    if (!isTravelWheel()) {
      return [
        { label: '📍 Find it on Maps', url: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(name)}` },
      ];
    }
    return [
      { label: '✈️ Flights', url: `https://www.google.com/travel/flights?q=${encodeURIComponent(`flights to ${name}`)}` },
      { label: '🛏️ Stays', url: `https://www.booking.com/searchresults.html?ss=${encodeURIComponent(name)}` },
    ];
  }

  // The info view: opened from a spin history entry (with trip status
  // controls) or from an entry in the manage list (without them).
  let infoEntry = null; // the history entry being viewed, if any

  function showInfoModal(d, historyItem) {
    infoEntry = historyItem || null;
    const name = d ? d.name : historyItem.name;
    infoFlag.textContent = d ? d.flag : historyItem.flag;
    infoName.textContent = name;
    infoTags.textContent = d ? describe(d) : 'No longer on this wheel';
    const notes = d && d.notes;
    infoNotes.hidden = !notes;
    infoNotes.textContent = notes || '';
    renderLinkList(infoLinks, d ? destLinks(d) : fallbackLinks(name));
    renderLinkList(infoPlan, planLinks(name));
    infoStatus.hidden = !infoEntry || !isTravelWheel();
    if (infoEntry && isTravelWheel()) syncStatusControls();
    renderInfoPoll();  // restaurant history entries get the date-poll section
    infoEditHint.hidden = !d;
    infoEditBtn.hidden = !d;
    infoEditBtn.onclick = () => {
      infoModal.close();
      if (!manageModal.open) manageBtn.click();
      enterEditMode(d);
    };
    infoModal.showModal();
  }

  // Newer history entries carry the entry id; older ones only a
  // name — match what we can.
  function openDestInfo(item) {
    const d = state.destinations.find((x) => x.id === item.dest_id)
      || state.destinations.find((x) => x.name === item.name)
      || null;
    showInfoModal(d, item);
  }

  closeInfoBtn.addEventListener('click', () => infoModal.close());
  infoModal.addEventListener('close', () => { infoEntry = null; });

  // ── Trip status (booked / been there) on a history entry ─────────
  function syncStatusControls() {
    statusChips.querySelectorAll('.seg-btn').forEach((btn) => {
      setActive(btn, (infoEntry.status || '') === btn.dataset.status);
    });
    tripDate.value = infoEntry.trip_date || '';
  }

  async function updateHistoryEntry(changes) {
    const res = await api(`/history/${infoEntry.id}`, {
      method: 'PUT',
      body: JSON.stringify(changes),
    });
    state.history = res.history;
    const updated = state.history.find((e) => e.id === infoEntry.id);
    if (updated) infoEntry = updated;
    renderHistory();
    if (res.destination) {
      // marking "been there" took it off the wheel
      const i = state.destinations.findIndex((d) => d.id === res.destination.id);
      if (i !== -1) state.destinations[i] = res.destination;
      refresh();
    }
    syncStatusControls();
  }

  statusChips.addEventListener('click', (e) => {
    const btn = e.target.closest('.seg-btn');
    if (!btn || !infoEntry) return;
    updateHistoryEntry({ status: btn.dataset.status }).catch((err) => {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
    });
  });

  tripDate.addEventListener('change', () => {
    if (!infoEntry) return;
    updateHistoryEntry({ trip_date: tripDate.value }).catch(console.error);
  });

  // ── Result modal ──────────────────────────────────────────────────
  function describe(d) {
    if (!isTravelWheel()) {
      return starCount(d) >= 2
        ? `🌟 starred by ${starCount(d)} of you`
        : (isFavorite(d) ? '⭐ favourite' : '');
    }
    const parts = [
      starCount(d) >= 2 ? `🌟 starred by ${starCount(d)} of you` : (isFavorite(d) ? '⭐ favourite' : ''),
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
    resultNotes.hidden = !destination.notes;
    resultNotes.textContent = destination.notes || '';
    renderLinkList(resultLinks, [...destLinks(destination), ...planLinks(destination.name)]);
    if (isTravelWheel()) {
      vetoBtn.disabled = state.round.my_veto_used;
      vetoNote.textContent = state.round.my_veto_used
        ? 'You\'ve already used your veto this round — the wheel has spoken!'
        : 'Each of you gets one veto per round — yours is still up your sleeve.';
    }
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
    if (!result) { refresh(); return; }
    try {
      const res = await api('/round/pick', {
        method: 'POST',
        body: JSON.stringify({ dest_id: result.id }),
      });
      applyRound(res.round);
      if (res.final) {
        state.history = res.history;
        renderHistory();
        wheelHint.textContent = finalMessage(result);
        // A restaurant pick's next step is settling an evening, so take the
        // spinner straight into the date poll instead of making them dig it
        // out of the history. Travel picks book instead — no poll for them.
        if (!isTravelWheel()) {
          const entry = res.history.find((e) => e.dest_id === result.id);
          if (entry) openPollModal(entry, 'propose');
        }
      } else {
        wheelHint.textContent = `Waiting for ${waitingNames(res.round.pending)} to okay ${result.flag} ${result.name} — they can still veto ✋`;
      }
    } catch (err) {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
    }
    refresh();
  });

  vetoBtn.addEventListener('click', async () => {
    if (!pendingResult || state.round.my_veto_used) return;
    const result = pendingResult;
    pendingResult = null;
    resultModal.close();
    try {
      applyRound(await api('/round/veto', {
        method: 'POST',
        body: JSON.stringify({ dest_id: result.id }),
      }));
    } catch (err) {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
      refresh();
      return;
    }
    if (currentSegments.length > 0) {
      spin();
    } else {
      wheelHint.textContent = 'Nothing left after that veto — loosen the filters or start over.';
    }
  });

  // ── Pending pick banner (someone spun — accept or veto) ──────────
  // With three or four people on a travel wheel a pick stays pending
  // until everyone who can still veto has okayed it; waiting_names says
  // who's still due. Restaurant wheels never have a pending pick.
  function waitingNames(p) {
    return (p.waiting_names || []).join(' & ') || partnerNames();
  }

  function renderPending() {
    const p = state.round.pending;
    pendingBanner.hidden = !p;
    if (!p) return;
    if (p.mine) {
      pendingText.textContent = `⏳ You picked ${p.flag} ${p.name} — waiting for ${waitingNames(p)} to give it a thumbs-up.`;
      pendingActions.hidden = true;
    } else if (p.i_confirmed) {
      pendingText.textContent = `👍 You're in for ${p.flag} ${p.name} — waiting for ${waitingNames(p)}.`;
      pendingActions.hidden = true;
    } else {
      pendingText.textContent = `🎡 ${p.by_name} spun ${p.flag} ${p.name}! Are you in — or is this your veto?`;
      pendingActions.hidden = false;
      pendingVetoBtn.disabled = state.round.my_veto_used;
      pendingVetoBtn.title = state.round.my_veto_used
        ? 'You already used your veto this round' : '';
    }
  }

  pendingAcceptBtn.addEventListener('click', async () => {
    const p = state.round.pending;
    if (!p) return;
    try {
      const res = await api('/round/confirm', { method: 'POST' });
      if (res.final) {
        state.history = res.history;
        renderHistory();
        wheelHint.textContent = finalMessage(p);
      } else {
        wheelHint.textContent = `You're in! Still waiting for ${waitingNames(res.round.pending)} to okay ${p.flag} ${p.name}.`;
      }
      applyRound(res.round);
    } catch (err) {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
    }
  });

  pendingVetoBtn.addEventListener('click', async () => {
    const p = state.round.pending;
    if (!p || state.round.my_veto_used) return;
    try {
      applyRound(await api('/round/veto', {
        method: 'POST',
        body: JSON.stringify({ dest_id: p.dest_id }),
      }));
      wheelHint.textContent = `You vetoed ${p.flag} ${p.name} 🙅 — give it another spin!`;
    } catch (err) {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
    }
  });

  // Other members' devices find out about vetoes, pending picks and
  // fresh history by polling — plenty for people deciding on a sofa.
  setInterval(async () => {
    if (!me || !currentWheel() || appView.hidden || document.hidden || spinning) return;
    try {
      const [round, history] = await Promise.all([api('/round'), api('/history')]);
      if (JSON.stringify(history) !== JSON.stringify(state.history)) {
        state.history = history;
        renderHistory();
        reflectPollUpdates();  // a partner's vote or a fresh lock just landed
        // a history change can also change the wheel: someone may have
        // starred, edited, or marked an entry "been there"
        state.destinations = await api('/destinations');
        refresh();
      }
      applyRound(round);
    } catch { /* transient — the next poll retries */ }
  }, 5000);

  // ── Confetti ──────────────────────────────────────────────────────
  const confettiCanvas = document.getElementById('confetti');
  const confettiCtx = confettiCanvas.getContext('2d');
  let confettiRaf = null;

  function launchConfetti() {
    if (prefersReducedMotion()) return;  // no celebratory shower if motion's dialled down
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

  // ── Manage the wheel ──────────────────────────────────────────────
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

  syncCatalogBtn.addEventListener('click', async () => {
    syncCatalogBtn.disabled = true;
    syncCatalogStatus.hidden = false;
    syncCatalogStatus.textContent = 'Checking the catalogue…';
    try {
      const res = await api('/destinations/sync', { method: 'POST' });
      state.destinations = res.destinations;
      renderDestList();
      syncCatalogStatus.textContent = res.added
        ? `✅ Added ${res.added} new ${res.added === 1 ? 'place' : 'places'} — scroll down to find them.`
        : '👍 You’re already up to date — nothing new in the catalogue.';
    } catch (err) {
      console.error(err);
      syncCatalogStatus.textContent = '⚠️ Could not reach the server — try again in a moment.';
    } finally {
      syncCatalogBtn.disabled = false;
    }
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
      star.title = 'Star it (your star) — every member\'s star widens this slice of the wheel';
      const paintStar = () => {
        star.textContent = starIcon(d);
        star.classList.toggle('starred', isFavorite(d));
      };
      paintStar();
      star.addEventListener('click', async () => {
        const updated = await patchDestination(d.id, { starred: !starredByMe(d) }).catch(console.error);
        if (!updated) return;
        Object.assign(d, updated);
        paintStar();
      });

      const name = document.createElement('button');
      name.type = 'button';
      name.className = 'dest-name';
      name.title = 'Info & links about this place';
      name.textContent = `${d.flag} ${d.name}`;
      name.addEventListener('click', () => showInfoModal(d, null));

      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      meta.textContent = isTravelWheel()
        ? `${d.budget} · ${DISTANCE_LABELS[d.distance]}`
        : (d.notes ? d.notes.slice(0, 40) : '');

      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'edit-btn';
      edit.title = 'Edit this entry';
      edit.textContent = '✏️';
      edit.addEventListener('click', () => enterEditMode(d));

      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'del-btn';
      del.title = 'Remove this entry';
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

  // Links editor: a row of label + URL inputs per link
  const addNotes = document.getElementById('add-notes');
  const linkRows = document.getElementById('link-rows');
  const addLinkBtn = document.getElementById('add-link-btn');

  function addLinkRow(link = { label: '', url: '' }) {
    const row = document.createElement('div');
    row.className = 'link-row';
    const label = document.createElement('input');
    label.type = 'text';
    label.className = 'link-label';
    label.placeholder = 'Label, e.g. Our hotel';
    label.maxLength = 60;
    label.value = link.label || '';
    const url = document.createElement('input');
    url.type = 'text';
    url.className = 'link-url';
    url.placeholder = 'https://…';
    url.maxLength = 300;
    url.value = link.url || '';
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'del-btn';
    del.title = 'Remove this link';
    del.textContent = '✕';
    del.addEventListener('click', () => row.remove());
    row.append(label, url, del);
    linkRows.append(row);
    return row;
  }

  addLinkBtn.addEventListener('click', () => {
    addLinkRow().querySelector('.link-url').focus();
  });

  function collectLinks() {
    return Array.from(linkRows.querySelectorAll('.link-row')).map((row) => {
      let url = row.querySelector('.link-url').value.trim();
      if (url && !/^https?:\/\//i.test(url)) url = `https://${url}`;
      return { label: row.querySelector('.link-label').value.trim(), url };
    }).filter((link) => link.url);
  }

  function enterEditMode(d) {
    state.editingId = d.id;
    formTitle.textContent = `Edit ${d.name}`;
    formSubmit.textContent = '💾 Save changes';
    cancelEditBtn.hidden = false;
    addNameInput.value = d.name;
    addFlagInput.value = d.flag;
    if (isTravelWheel()) {
      document.getElementById('add-budget').value = d.budget;
      document.getElementById('add-distance').value = d.distance;
      setCheckedValues('add-vibes', d.vibes);
      setCheckedValues('add-seasons', d.seasons);
      setCheckedValues('add-party', d.party);
    }
    addNotes.value = d.notes || '';
    linkRows.innerHTML = '';
    (d.links || []).forEach(addLinkRow);
    addForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
    addNameInput.focus();
  }

  function exitEditMode() {
    state.editingId = null;
    formTitle.textContent = 'Add your own';
    formSubmit.textContent = '➕ Add it';
    cancelEditBtn.hidden = true;
    addForm.reset();
    linkRows.innerHTML = '';
  }

  cancelEditBtn.addEventListener('click', exitEditMode);

  addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = addNameInput.value.trim();
    if (!name) return;
    const payload = {
      name,
      flag: addFlagInput.value.trim() || (isTravelWheel() ? '📍' : '🍽️'),
      notes: addNotes.value.trim(),
      links: collectLinks(),
    };
    if (isTravelWheel()) {
      payload.budget = document.getElementById('add-budget').value;
      payload.distance = document.getElementById('add-distance').value;
      payload.vibes = checkedValues('add-vibes');
      payload.seasons = checkedValues('add-seasons');
      payload.party = checkedValues('add-party');
    }
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
      alert('Could not save it — is the server still running?');
    }
  });

  // ── History ───────────────────────────────────────────────────────
  function renderHistory() {
    historyList.innerHTML = '';
    clearHistoryBtn.hidden = state.history.length === 0;
    if (state.history.length === 0) {
      const li = document.createElement('li');
      li.className = 'history-empty';
      li.textContent = 'Nothing picked yet — give it a spin!';
      historyList.append(li);
      return;
    }
    for (const item of state.history) {
      const li = document.createElement('li');
      const name = document.createElement('button');
      name.type = 'button';
      name.className = 'history-name';
      name.title = 'Info & links for this pick';
      name.textContent = `${item.flag} ${item.name}`;
      name.addEventListener('click', () => openDestInfo(item));
      const when = document.createElement('span');
      when.className = 'when';
      const badge = item.status === 'booked' ? '📅 ' : (item.status === 'visited' ? '✅ ' : '');
      when.textContent = badge + new Date(item.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
      const titleBits = [];
      if (item.by) titleBits.push(`Picked by ${item.by}`);
      if (item.status === 'booked') titleBits.push('booked');
      if (item.status === 'visited') titleBits.push('been there');
      if (item.trip_date) titleBits.push(`trip: ${item.trip_date}`);
      if (titleBits.length) when.title = titleBits.join(' · ');
      li.append(name, when);
      historyList.append(li);
    }
  }

  clearHistoryBtn.addEventListener('click', async () => {
    if (!confirm('Clear the whole spin history? This clears it for everyone on this wheel.')) return;
    try {
      await api('/history', { method: 'DELETE' });
      state.history = [];
      renderHistory();
      applyRound(await api('/round')); // clearing history also starts a fresh round
    } catch (err) {
      console.error(err);
    }
  });

  // ── Refresh ───────────────────────────────────────────────────────
  function refresh() {
    currentSegments = eligibleDestinations();
    const n = currentSegments.length;
    const favs = currentSegments.filter(isFavorite).length;
    const noun = wheelMeta(currentWheel()).noun;
    matchCount.textContent = n === 0
      ? (isTravelWheel() ? 'No matches — loosen a filter or two' : 'Nothing on the wheel yet — add some spots below')
      : (n === 1 ? `1 ${noun} on the wheel` : `${n} ${noun}s on the wheel`) +
        (favs > 0 ? ` · ⭐ ${favs} favourite${favs === 1 ? '' : 's'} with extra chance` : '');
    spinBtn.disabled = n === 0 || spinning;
    drawWheel();
  }

  spinBtn.addEventListener('click', spin);

  // ── Init ──────────────────────────────────────────────────────────
  loadVersion();
  if (token) {
    fetchMe();
  } else {
    showView('auth');
    if (inviteCode) {
      document.querySelector('.auth-tabs .tab[data-mode="register"]').click();
      inviteBanner.textContent = `🎟️ You've been invited to a shared wheel (code ${inviteCode}) — create an account and you'll join it automatically.`;
      inviteBanner.hidden = false;
    }
  }
})();
