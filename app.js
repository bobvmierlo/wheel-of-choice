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
import {
  VIBE_LABELS, BUDGET_LABELS, DISTANCE_LABELS, MULTI_FILTERS,
  WHEEL_TYPE_META, SEGMENT_COLORS, TOKEN_KEY, TRAVEL_TYPES,
} from './constants.js';
import { prefersReducedMotion, prettyDate, fmtHour } from './utils.js';
import { actionBtn } from './ui.js';
import {
  t, tn, initI18n, currentLanguage, availableLanguages, setLanguage,
} from './i18n.js';
import { initAdmin } from './admin.js';
import { initAccount } from './account.js';

// Translations load before anything renders, so every t() below already
// speaks the user's language (switching language reloads the page).
await initI18n();

(function () {
  'use strict';

  // ── State ─────────────────────────────────────────────────────────
  let wheelId = ''; // current wheel id — resolved from /me + the URL hash
  let token = localStorage.getItem(TOKEN_KEY) || '';
  let me = null; // { user: {id, name, admin}, prefs, wheels: [{id, type, name, code, members}] }

  // Two kinds of invite link pre-fill the join flow, both carried into
  // registration as `code`:
  //   ?join=CODE   — a wheel's share code: registering joins that wheel.
  //   ?invite=CODE — an admin account invite: registering just creates
  //                  the account (used when open registration is off).
  const urlParams = new URLSearchParams(location.search);
  const SSO_CODE_KEY = 'wheel-sso-code';
  const accountInvite = urlParams.has('invite') && !urlParams.has('join');
  let inviteCode = (urlParams.get('join') || urlParams.get('invite') || '')
    .toUpperCase().replace(/\s+/g, '');
  // ?dinner=<entry-id> — a date-locked push tapped on the body (iOS has no
  // notification action buttons for PWAs, so the tap deep-links here). The
  // wheel comes from the hash; once its data loads we pop the info modal on
  // that entry so its calendar buttons are one tap away. See maybeOpenDinner.
  let pendingDinnerId = urlParams.get('dinner') || '';
  // ?sso=<one-time code> / ?sso=linked / ?sso_error=<msg> — the server's
  // bounce back from a single-sign-on round trip (see startSso).
  const ssoResult = urlParams.get('sso') || '';
  const ssoError = urlParams.get('sso_error') || '';
  if (ssoResult || ssoError) {
    // an invite link followed before signing on survives the round trip
    try {
      inviteCode = inviteCode || sessionStorage.getItem(SSO_CODE_KEY) || '';
      sessionStorage.removeItem(SSO_CODE_KEY);
    } catch { /* storage blocked — the invite is just lost */ }
  }
  const PARAMS_TO_STRIP = ['join', 'invite', 'dinner', 'sso', 'sso_error'];
  if (PARAMS_TO_STRIP.some((k) => urlParams.has(k))) {
    PARAMS_TO_STRIP.forEach((k) => urlParams.delete(k));
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
    rev: null, // the wheel's revision — a change means "fetch it again"
    editingId: null,
  };

  function emptyRound() {
    return { vetoed_ids: [], my_veto_used: false, vetoes_used: 0, members: 1, pending: null };
  }

  function defaultFilters() {
    return { budget: [], distance: [], party: 'couple', vibe: [], season: [], tags: [] };
  }

  function currentWheel() {
    return (me && me.wheels.find((w) => w.id === wheelId)) || null;
  }

  function wheelMeta(wheel) {
    return WHEEL_TYPE_META[(wheel && wheel.type) || 'holidays'];
  }

  function isTravelWheel(wheel = currentWheel()) {
    return !wheel || TRAVEL_TYPES.includes(wheel.type);
  }

  // Restaurant wheels add a location per entry and dinner-style polls.
  function isFoodWheel(wheel = currentWheel()) {
    return !!wheel && wheel.type === 'restaurants';
  }

  // A custom ("anything") wheel picks things that need no date.
  function hasPolls(wheel = currentWheel()) {
    return !!wheel && wheel.type !== 'custom';
  }

  function loadFilters() {
    state.filters = defaultFilters();
    const saved = (me && me.prefs && me.prefs[wheelId]) || {};
    for (const key of [...MULTI_FILTERS, 'tags']) {
      if (Array.isArray(saved[key])) state.filters[key] = [...saved[key]];
    }
    if (saved.party === 'couple' || saved.party === 'group') state.filters.party = saved.party;
  }

  let prefsTimer = null;
  function saveFilters() {
    if (!me) return;
    const f = state.filters;
    // travel wheels filter on the trip preferences, the others on tags
    me.prefs[wheelId] = isTravelWheel()
      ? { budget: [...f.budget], distance: [...f.distance], party: f.party, vibe: [...f.vibe], season: [...f.season] }
      : { tags: [...f.tags] };
    clearTimeout(prefsTimer);
    prefsTimer = setTimeout(() => {
      rootApi('/me/prefs', { method: 'PUT', body: JSON.stringify(me.prefs) }).catch(console.error);
    }, 600);
  }

  function partnerNames(wheel = currentWheel()) {
    const members = (wheel && wheel.members) || [];
    const others = members.filter((n) => n !== me.user.name);
    return others.length ? others.join(' & ') : t('your partner');
  }

  // Merge fresh round state from the server; when a partner acted on my
  // pending pick in the meantime, say so in the hint.
  function applyRound(round) {
    const before = state.round;
    state.round = round;
    if (JSON.stringify(before) === JSON.stringify(round)) return;
    if (before.pending && (before.pending.mine || before.pending.i_confirmed) && !round.pending) {
      wheelHint.textContent = round.vetoed_ids.includes(before.pending.dest_id)
        ? t(`{flag} {name} got vetoed 🙅 — spin again!`, { flag: before.pending.flag, name: before.pending.name })
        : finalMessage(before.pending);
    }
    renderPending();
    refresh();
  }

  function finalMessage(pick) {
    if (isTravelWheel()) return t(`{flag} {name} it is — time to book! 🧳`, { flag: pick.flag, name: pick.name });
    if (isFoodWheel()) return t(`{flag} {name} it is — enjoy your meal! 🍽️`, { flag: pick.flag, name: pick.name });
    return t(`{flag} {name} it is — the wheel has spoken! 🎯`, { flag: pick.flag, name: pick.name });
  }

  // ── Language menu ─────────────────────────────────────────────────
  // One entry per locale file on the server; picking one reloads the page.
  (async () => {
    const langs = await availableLanguages();
    if (langs.length < 2) return;
    const select = document.getElementById('lang-select');
    for (const l of langs) {
      const opt = document.createElement('option');
      opt.value = l.code;
      opt.textContent = l.name;
      select.append(opt);
    }
    select.value = currentLanguage();
    select.addEventListener('change', () => setLanguage(select.value));
    document.getElementById('lang-picker').hidden = false;
  })();

  // ── Server API ────────────────────────────────────────────────────
  async function rootApi(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', 'X-Lang': currentLanguage() };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`/api${path}`, { headers, ...options });
    if (res.status === 401 && token && !path.startsWith('/auth/')) {
      forceLogout(); // token expired or revoked — back to the login screen
    }
    if (!res.ok) {
      let message = '';
      try { message = (await res.json()).error; } catch { /* not JSON */ }
      const err = new Error(message || t(`Server said {status} for {path}`, { status: res.status, path }));
      err.status = res.status;
      throw err;
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
      state.destinations = withoutPendingDeletes('dest', destinations);
      state.history = withoutPendingDeletes('history', history);
      state.round = round;
      state.rev = round.rev;
      wheelHint.textContent = '';
    } catch (err) {
      console.error(err);
      state.destinations = [];
      state.history = [];
      state.round = emptyRound();
      state.rev = null;
      wheelHint.textContent = t('⚠️ Cannot reach the server — is it running? (python3 server.py)');
    }
    renderHistory();
    renderPending();
    refresh();
    maybeOpenDinner();
  }

  // A date-locked notification was tapped: once we're on the right wheel
  // with its history loaded, open that entry's info modal so its "add to
  // calendar" buttons are right there. No-op until both line up.
  function maybeOpenDinner() {
    if (!pendingDinnerId) return;
    const entry = state.history.find((e) => e.id === pendingDinnerId);
    if (!entry) return;  // wrong wheel loaded, or the entry's gone — give up quietly
    pendingDinnerId = '';
    openDestInfo(entry);
  }

  // Every tag used on the wheel's entries, alphabetically.
  function wheelTags() {
    const all = new Set();
    for (const d of state.destinations) (d.tags || []).forEach((tag) => all.add(tag));
    return [...all].sort((a, b) => a.localeCompare(b));
  }

  // The selected tags that still exist — a tag nobody uses any more
  // mustn't silently empty the wheel.
  function activeTags() {
    const existing = new Set(wheelTags());
    return state.filters.tags.filter((tag) => existing.has(tag));
  }

  function eligibleDestinations() {
    const f = state.filters;
    if (!isTravelWheel()) {
      // restaurants & custom: no vetoes, only the tag filter
      const tags = activeTags();
      return state.destinations.filter((d) =>
        d.enabled && (tags.length === 0 || (d.tags || []).some((tag) => tags.includes(tag))));
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
  const authNameLabel = document.getElementById('auth-name-label');
  const authEmailRow = document.getElementById('auth-email-row');
  const authEmail = document.getElementById('auth-email');
  const ssoBlock = document.getElementById('sso-block');
  const ssoBtn = document.getElementById('sso-btn');
  const authHint = document.getElementById('auth-hint');

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
  const sharePeople = document.getElementById('share-people');
  const sharePeopleList = document.getElementById('share-people-list');
  const sharePeopleEmpty = document.getElementById('share-people-empty');
  const shareInviteBtn = document.getElementById('share-invite-btn');
  const sharePending = document.getElementById('share-pending');
  const invitesBanner = document.getElementById('invites-banner');
  const selectedInvitees = new Set();

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
  const resultLocation = document.getElementById('result-location');
  const resultLinks = document.getElementById('result-links');
  const acceptBtn = document.getElementById('accept-btn');
  const vetoBtn = document.getElementById('veto-btn');
  const vetoNote = document.getElementById('veto-note');

  const pendingBanner = document.getElementById('pending-banner');
  const pendingText = document.getElementById('pending-text');
  const pendingActions = document.getElementById('pending-actions');
  const pendingAcceptBtn = document.getElementById('pending-accept-btn');
  const pendingVetoBtn = document.getElementById('pending-veto-btn');


  const infoModal = document.getElementById('info-modal');
  const closeInfoBtn = document.getElementById('close-info-btn');
  const infoFlag = document.getElementById('info-flag');
  const infoName = document.getElementById('info-name');
  const infoTags = document.getElementById('info-tags');
  const infoNotes = document.getElementById('info-notes');
  const infoLocation = document.getElementById('info-location');
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
  const historyExport = document.getElementById('history-export');
  const tagFilter = document.getElementById('tag-filter');
  const tagFilterChips = document.getElementById('tag-filter-chips');
  const addTagsInput = document.getElementById('add-tags');
  const addError = document.getElementById('add-error');
  const pendingWithdrawBtn = document.getElementById('pending-withdraw-btn');
  const tripEnd = document.getElementById('trip-end');

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
    renderWheelInvites(); // wheels other people shared with me — accept/dismiss
    adminBtn.hidden = !me.user.admin;
    if (me.user.admin) checkForUpdate(); // light up the 🆕 dot if one's waiting
    notifyBtn.hidden = false;
    passwordBtn.hidden = false;
    syncPushSubscription(); // fire-and-forget — keeps this device buzzing for this account
    syncCalendarButton(); // shows 📆 only if the server can read calendars
    maybePromotePwa(); // nudge phone-web users onto the Home Screen (once)
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
      if (token) authError.textContent = t('⚠️ Cannot reach the server — is it running?');
    }
  }

  function forceLogout() {
    token = '';
    me = null;
    localStorage.removeItem(TOKEN_KEY);
    clearTimeout(prefsTimer);
    invitesBanner.hidden = true;
    showView('auth');
    if (!inviteCode) applyRegistrationGate();
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
    add.title = t('Create or join another wheel');
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
    const food = isFoodWheel(wheel);
    document.querySelectorAll('.filter-group').forEach((g) => { g.hidden = !travel; });
    filtersTitle.textContent = travel ? t('Trip preferences')
      : (food ? t('🍽️ Restaurant wheel') : t('🎯 Your wheel'));
    resultKicker.textContent = t(wheelMeta(wheel).kicker);
    vetoBtn.hidden = !travel;
    vetoNote.hidden = !travel;
    document.querySelectorAll('#manage-modal .travel-only').forEach((el) => { el.hidden = !travel; });
    document.querySelectorAll('#manage-modal .food-only').forEach((el) => { el.hidden = !food; });
    document.querySelectorAll('#manage-modal .list-only').forEach((el) => { el.hidden = travel; });
    addNameInput.placeholder = travel ? t('Destination name') : (food ? t('Restaurant name') : t('Entry name'));
    addFlagInput.placeholder = travel ? t('Emoji, e.g. 🏝️') : (food ? t('Emoji, e.g. 🍕') : t('Emoji, e.g. 🎬'));
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
      authSubmit.textContent = registering ? t('Create account') : t('Log in');
      // logging in takes a name *or* an e-mail; registering asks for both
      authNameLabel.textContent = registering ? t('Your name') : t('Name or e-mail');
      authName.maxLength = registering ? 30 : 254;
      authName.placeholder = registering ? t('e.g. Bob') : t('Your name or e-mail address');
      authName.autocomplete = registering ? 'nickname' : 'username';
      authEmailRow.hidden = !registering;
      authEmail.autocomplete = registering ? 'email' : 'off';
      authPassword.autocomplete = registering ? 'new-password' : 'current-password';
      // Only new passwords must clear the 8-char floor. Login stays lenient
      // so accounts created under the old 4-char rule can still get in.
      authPassword.minLength = registering ? 8 : 1;
      authPassword.placeholder = registering ? t('At least 8 characters') : t('Your password');
      authError.textContent = '';
    });
  });

  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    authError.textContent = '';
    authSubmit.disabled = true;
    try {
      const body = authMode === 'login'
        ? { login: authName.value.trim(), password: authPassword.value }
        : { name: authName.value.trim(), password: authPassword.value, email: authEmail.value.trim() };
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

  // ── Single sign-on ────────────────────────────────────────────────
  // The server hands back the identity provider's URL; we go there, and
  // the provider sends the browser back to /api/auth/oidc/callback, which
  // bounces to /?sso=<one-time code>. finishSso trades that code for a
  // session token. mode 'link' attaches the logged-in account instead.
  async function startSso(mode = 'login') {
    const body = { mode };
    if (inviteCode) {
      body.code = inviteCode;
      try { sessionStorage.setItem(SSO_CODE_KEY, inviteCode); } catch { /* fine */ }
    }
    const { url } = await rootApi('/auth/oidc/start', { method: 'POST', body: JSON.stringify(body) });
    location.assign(url);
  }

  ssoBtn.addEventListener('click', async () => {
    authError.textContent = '';
    ssoBtn.disabled = true;
    try {
      await startSso('login');
    } catch (err) {
      authError.textContent = `⚠️ ${err.message}`;
      ssoBtn.disabled = false;
    }
  });

  function showSsoButton(sso) {
    ssoBlock.hidden = !(sso && sso.enabled);
    if (sso && sso.enabled) ssoBtn.textContent = `🔐 ${sso.label}`;
  }

  // Shape the logged-out screen after /api/config: the sign-on button,
  // whether passwords work at all (an admin can switch them off once SSO
  // is set up — then the sign-on button is the only way in), and on a
  // brand-new server the "restore from a backup" offer.
  const authTabs = document.querySelector('.auth-tabs');
  const setupRestore = document.getElementById('setup-restore');
  function applyAuthConfig(config) {
    showSsoButton(config.sso);
    const passwords = config.password_login !== false;
    authTabs.hidden = !passwords;
    authForm.hidden = !passwords;
    ssoBlock.classList.toggle('sso-only', !passwords);
    if (!passwords) {
      authHint.textContent = inviteCode
        ? t('Sign on with your account at the identity provider — you\'ll join the shared wheel straight after.')
        : t('This server uses single sign-on — log in with your account at the identity provider.');
    }
    setupRestore.hidden = !config.fresh;
    return passwords;
  }

  async function finishSso(code) {
    showView('auth');
    authError.textContent = t('⏳ Signing you in…');
    try {
      const result = await rootApi('/auth/oidc/finish', { method: 'POST', body: JSON.stringify({ code }) });
      authError.textContent = '';
      token = result.token;
      localStorage.setItem(TOKEN_KEY, token);
      me = result.me;
      if (me.joined) {
        justJoined = true;
        joinedWheelId = me.joined;
      }
      applyMe();
    } catch (err) {
      authError.textContent = `⚠️ ${err.message}`;
      applyRegistrationGate();
    }
  }

  // ── Create / join a wheel ─────────────────────────────────────────
  const onboardAnswers = {
    type: 'holidays', home: null, roam: 'europe', vibes: [], budget: 'mix',
    favorites: [], // catalogue ids for the currently selected travel type
  };

  function showAddWheel(firstTime, invite = '') {
    onboardTitle.textContent = firstTime
      ? t(`Hi {name} — let's build your first wheel 🎡`, { name: me.user.name })
      : t('Add a wheel 🎡');
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
    const travel = TRAVEL_TYPES.includes(onboardAnswers.type);
    travelQuestions.hidden = !travel;
    restaurantHint.hidden = onboardAnswers.type !== 'restaurants';
    document.getElementById('custom-hint').hidden = onboardAnswers.type !== 'custom';
    const example = {
      holidays: t('Holidays'), citytrips: t('City trips'), custom: t('Films to watch'),
    }[onboardAnswers.type] || t('Friday night dinners');
    wheelNameInput.placeholder = t('e.g. {name}', { name: example });
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
    const travel = TRAVEL_TYPES.includes(onboardAnswers.type);
    if (travel && !onboardAnswers.home) {
      onboardError.textContent = t('⚠️ Tell us where home is first 🙂');
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
      errorEl.textContent = t('⚠️ Enter a share code first');
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
    // built here so the whole sentence (names included) is one translation
    const esc = (s) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
    document.getElementById('welcome-intro').innerHTML = t(
      'You\'ve joined the wheel <strong>{wheel}</strong> of {partner}. Now star the entries <em>you</em> dream of — starred entries get a bigger slice of the wheel, and every extra star from another member 🌟 widens it further.',
      { wheel: esc(wheel ? `${wheelMeta(wheel).icon} ${wheel.name}` : ''), partner: esc(partnerNames(wheel)) },
    );
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

  // ── Incoming invitations (someone shared a wheel with me) ─────────
  // Rendered from me.invites into a banner that sits above every view, so
  // it's reachable whether the person has wheels of their own or not.
  // (Named distinctly from the admin panel's renderInvites, which handles
  // account-invite links — same scope, so the names must not collide.)
  function renderWheelInvites() {
    const invites = (me && me.invites) || [];
    invitesBanner.innerHTML = '';
    if (!token || !invites.length) {
      invitesBanner.hidden = true;
      return;
    }
    invitesBanner.hidden = false;
    for (const inv of invites) {
      const meta = WHEEL_TYPE_META[inv.type] || WHEEL_TYPE_META.holidays;
      const card = document.createElement('div');
      card.className = 'invite-card';

      const text = document.createElement('p');
      text.className = 'invite-text';
      const who = document.createElement('strong');
      who.textContent = inv.from_name || t('Someone');
      const what = document.createElement('strong');
      what.textContent = `${meta.icon} ${inv.name}`;
      // "<who> shared <what> with you", with both names in bold
      const [before, middle, after] = t('{who} shared {what} with you').split(/\{who\}|\{what\}/);
      text.append(document.createTextNode(before), who, document.createTextNode(middle), what,
        document.createTextNode(after || ''));

      const actions = document.createElement('div');
      actions.className = 'invite-actions';
      const accept = document.createElement('button');
      accept.type = 'button';
      accept.className = 'btn btn-primary';
      accept.textContent = t('✅ Accept');
      accept.addEventListener('click', () => respondToInvite(inv.wheel_id, 'accept', accept));
      const dismiss = document.createElement('button');
      dismiss.type = 'button';
      dismiss.className = 'btn btn-ghost';
      dismiss.textContent = t('Dismiss');
      dismiss.addEventListener('click', () => respondToInvite(inv.wheel_id, 'decline', dismiss));
      actions.append(accept, dismiss);

      card.append(text, actions);
      invitesBanner.append(card);
    }
  }

  async function respondToInvite(id, action, btn) {
    const buttons = [...btn.parentElement.querySelectorAll('button')];
    buttons.forEach((b) => { b.disabled = true; });
    try {
      me = await rootApi(`/wheels/${id}/${action}`, { method: 'POST' });
      if (action === 'accept' && me.joined) {
        // fresh member — send them through the same "star your dreams" welcome
        justJoined = true;
        joinedWheelId = me.joined;
      }
      applyMe();
    } catch (err) {
      buttons.forEach((b) => { b.disabled = false; });
      const note = document.createElement('p');
      note.className = 'form-error';
      note.textContent = `⚠️ ${err.message}`;
      btn.parentElement.append(note);
    }
  }

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
    renderShareSettings(wheel);
    shareError.textContent = '';
    copyCodeBtn.textContent = t('📋 Copy');
    copyLinkBtn.textContent = t('📋 Copy');
    renderSharePeople(wheel);
    shareModal.showModal();
  });

  // ── Wheel settings (members, rename, new code, tab order) ─────────
  const memberList = document.getElementById('member-list');
  const renameForm = document.getElementById('rename-form');
  const renameInput = document.getElementById('rename-input');
  const renewCodeBtn = document.getElementById('renew-code-btn');
  const tabLeftBtn = document.getElementById('tab-left-btn');
  const tabRightBtn = document.getElementById('tab-right-btn');

  // Everything in the share modal that depends on the wheel's summary —
  // re-run after any change that hands back a fresh `me`.
  function renderShareSettings(wheel) {
    shareWheelName.textContent = `${wheelMeta(wheel).icon} ${wheel.name}`;
    shareCode.textContent = wheel.code;
    shareLink.textContent = inviteLink(wheel);
    const others = wheel.members.filter((n) => n !== me.user.name);
    shareMembers.textContent = others.length
      ? ''
      : t('Nobody else is on this wheel yet — send the code!');
    shareMembers.hidden = !!others.length;
    renderSharePending(wheel);
    renderMemberList(wheel);
    if (document.activeElement !== renameInput) renameInput.value = wheel.name;
    const i = me.wheels.findIndex((w) => w.id === wheel.id);
    document.getElementById('tab-order-row').hidden = me.wheels.length < 2;
    tabLeftBtn.disabled = i <= 0;
    tabRightBtn.disabled = i === -1 || i >= me.wheels.length - 1;
  }

  function renderMemberList(wheel) {
    memberList.innerHTML = '';
    const people = [
      ...(wheel.member_list || []).map((m) => ({ ...m, pending: false })),
      ...(wheel.pending_list || []).map((m) => ({ ...m, pending: true })),
    ];
    for (const person of people) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = person.id === me.user.id ? t('{name} (you)', { name: person.name }) : person.name;
      if (person.pending) {
        const note = document.createElement('span');
        note.className = 'member-note';
        note.textContent = ` · ${t('invited, not accepted yet')}`;
        name.append(note);
      }
      li.append(name);
      if (person.id !== me.user.id) {
        li.append(actionBtn(
          person.pending ? t('Withdraw') : t('Remove'),
          person.pending ? t('Withdraw the invitation') : t('Take them off this wheel'),
          () => removeMember(wheel, person),
        ));
      }
      memberList.append(li);
    }
  }

  async function removeMember(wheel, person) {
    if (!person.pending && !confirm(t('Take {name} off {wheel}? They keep their other wheels and can rejoin with a code.', { name: person.name, wheel: wheel.name }))) return;
    await updateWheelSettings(() => rootApi(`/wheels/${wheel.id}/members/${person.id}/remove`, { method: 'POST' }));
  }

  // Run a settings call that answers with a fresh `me`, then redraw.
  async function updateWheelSettings(call) {
    shareError.textContent = '';
    try {
      me = { ...(await call()), prefs: me.prefs };
      renderTabs();
      const wheel = currentWheel();
      if (wheel) renderShareSettings(wheel);
      return true;
    } catch (err) {
      shareError.textContent = `⚠️ ${err.message}`;
      return false;
    }
  }

  renameForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const wheel = currentWheel();
    const name = renameInput.value.trim();
    if (!wheel || !name) return;
    renameInput.blur();
    await updateWheelSettings(() => rootApi(`/wheels/${wheel.id}`, { method: 'PUT', body: JSON.stringify({ name }) }));
  });

  renewCodeBtn.addEventListener('click', async () => {
    const wheel = currentWheel();
    if (!wheel) return;
    if (!confirm(t('Make a new share code? The current code and invite link stop working right away.'))) return;
    if (await updateWheelSettings(() => rootApi(`/wheels/${wheel.id}/code`, { method: 'POST' }))) {
      copyCodeBtn.textContent = t('📋 Copy');
      copyLinkBtn.textContent = t('📋 Copy');
    }
  });

  function moveTab(delta) {
    const ids = me.wheels.map((w) => w.id);
    const i = ids.indexOf(wheelId);
    const j = i + delta;
    if (i === -1 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    updateWheelSettings(() => rootApi('/me/wheel-order', { method: 'PUT', body: JSON.stringify({ order: ids }) }));
  }
  tabLeftBtn.addEventListener('click', () => moveTab(-1));
  tabRightBtn.addEventListener('click', () => moveTab(1));
  closeShareBtn.addEventListener('click', () => shareModal.close());

  // People already invited but not yet accepted — shown so the sharer
  // knows a nudge is still outstanding.
  function renderSharePending(wheel) {
    const pending = (wheel && wheel.pending) || [];
    sharePending.hidden = !pending.length;
    if (pending.length) sharePending.textContent = t('⏳ Waiting to accept: {names}', { names: pending.join(', ') });
  }

  // Pick-from-a-list sharing: fetch who's invitable and offer a checkbox
  // per person, then share to everyone ticked in one go.
  async function renderSharePeople(wheel) {
    selectedInvitees.clear();
    shareInviteBtn.disabled = true;
    shareInviteBtn.textContent = t('Share with selected');
    sharePeopleList.innerHTML = '';
    sharePeopleEmpty.hidden = true;
    sharePeople.hidden = false;
    let people = [];
    try {
      people = await rootApi(`/wheels/${wheel.id}/invitable`);
    } catch {
      sharePeople.hidden = true; // no list to offer — the code/link still work
      return;
    }
    if (currentWheel() !== wheel) return; // modal moved on while we fetched
    if (!people.length) {
      sharePeopleEmpty.hidden = false;
      return;
    }
    for (const p of people) {
      const label = document.createElement('label');
      label.className = 'share-person';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = p.id;
      box.addEventListener('change', () => {
        if (box.checked) selectedInvitees.add(p.id); else selectedInvitees.delete(p.id);
        shareInviteBtn.disabled = selectedInvitees.size === 0;
      });
      const name = document.createElement('span');
      name.textContent = p.name;
      label.append(box, name);
      sharePeopleList.append(label);
    }
  }

  shareInviteBtn.addEventListener('click', async () => {
    if (!selectedInvitees.size) return;
    const wheel = currentWheel();
    if (!wheel) return;
    shareInviteBtn.disabled = true;
    shareError.textContent = '';
    try {
      me = await rootApi(`/wheels/${wheel.id}/invite`, {
        method: 'POST',
        body: JSON.stringify({ user_ids: [...selectedInvitees] }),
      });
      const n = me.invited || 0;
      const fresh = me.wheels.find((w) => w.id === wheel.id) || wheel;
      renderShareSettings(fresh);     // now shows them under "waiting to accept"
      await renderSharePeople(fresh); // and drops them from the pick list
      shareInviteBtn.textContent = n
        ? tn(n, '✅ Shared with 1 person', '✅ Shared with {n} people')
        : t('Nobody new to share with');
    } catch (err) {
      shareError.textContent = `⚠️ ${err.message}`;
      shareInviteBtn.disabled = false;
    }
  });

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
    copyCodeBtn.textContent = (await copyToClipboard(currentWheel().code)) ? t('✅ Copied') : t('⚠️ Copy failed');
  });

  copyLinkBtn.addEventListener('click', async () => {
    copyLinkBtn.textContent = (await copyToClipboard(inviteLink(currentWheel()))) ? t('✅ Copied') : t('⚠️ Copy failed');
  });

  leaveBtn.addEventListener('click', async () => {
    const wheel = currentWheel();
    if (!wheel) return;
    const alone = wheel.members.length <= 1;
    const msg = alone
      ? t(`Leave {name}? You're the only one on it, so the wheel and its history are gone for good.`, { name: wheel.name })
      : t(`Leave {name}? The others keep the wheel — you can rejoin later with its code.`, { name: wheel.name });
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
    // When the app is already open, tapping a notification only focuses it
    // (no navigation), so the worker forwards the target here to act on.
    navigator.serviceWorker.addEventListener('message', (event) => {
      const msg = event.data || {};
      if (msg.type !== 'wheel-nav' || !msg.url) return;
      navigateToDeepLink(msg.url);
    });
  }

  // ── Install / notifications nudge ─────────────────────────────────
  // Two flavours of the same toast, shown once per browser until
  // dismissed:
  //   • phone/tablet web, not yet installed → add to the Home Screen,
  //     which is what unlocks push (iOS especially) and the calendar.
  //     Chrome/Android hands us a real install prompt via
  //     beforeinstallprompt; iOS has none, so we spell out the steps.
  //   • desktop, push available but not yet on → turn notifications on
  //     right here, no install needed.
  const PWA_TOAST_DISMISSED_KEY = 'wheel-pwa-toast-dismissed';
  const isMobile = isIOS || /Android|Mobile/i.test(navigator.userAgent);
  const pwaToast = document.getElementById('pwa-toast');
  const pwaInstallBody = document.getElementById('pwa-toast-install-body');
  const pwaNotifyBody = document.getElementById('pwa-toast-notify-body');
  const pwaToastSteps = document.getElementById('pwa-toast-steps');
  const pwaToastInstall = document.getElementById('pwa-toast-install');
  const pwaToastNotify = document.getElementById('pwa-toast-notify');
  const pwaNotifyError = document.getElementById('pwa-toast-notify-error');
  let deferredInstallPrompt = null;

  window.addEventListener('beforeinstallprompt', (event) => {
    // Hold onto it so our own button can trigger the native prompt.
    event.preventDefault();
    deferredInstallPrompt = event;
  });

  function dismissPwaToast() {
    pwaToast.hidden = true;
    try { localStorage.setItem(PWA_TOAST_DISMISSED_KEY, '1'); } catch { /* private mode — fine */ }
  }

  // Installed (or already added to the Home Screen): never nag again.
  window.addEventListener('appinstalled', dismissPwaToast);

  document.getElementById('pwa-toast-close').addEventListener('click', dismissPwaToast);
  document.querySelectorAll('.pwa-toast-dismiss').forEach((b) => b.addEventListener('click', dismissPwaToast));

  pwaToastInstall.addEventListener('click', async () => {
    if (deferredInstallPrompt) {
      // Android/Chrome: fire the browser's own install prompt.
      deferredInstallPrompt.prompt();
      const { outcome } = await deferredInstallPrompt.userChoice.catch(() => ({ outcome: 'dismissed' }));
      deferredInstallPrompt = null;
      if (outcome === 'accepted') dismissPwaToast();
      return;
    }
    // iOS (and anything without the native prompt): reveal the manual steps.
    pwaToastSteps.hidden = false;
    pwaToastInstall.hidden = true;
  });

  pwaToastNotify.addEventListener('click', async () => {
    pwaNotifyError.hidden = true;
    pwaToastNotify.disabled = true;
    try {
      await enablePush(); // native permission prompt fires from this click
      dismissPwaToast();
    } catch (err) {
      console.error(err);
      pwaNotifyError.textContent = `⚠️ ${err.message}`;
      pwaNotifyError.hidden = false;
    } finally {
      pwaToastNotify.disabled = false;
    }
  });

  async function maybePromotePwa() {
    let dismissed = false;
    try { dismissed = localStorage.getItem(PWA_TOAST_DISMISSED_KEY) === '1'; } catch { /* ignore */ }
    if (dismissed) return;
    if (isMobile) {
      if (isStandalone) return;         // already installed — nothing to nudge
      pwaInstallBody.hidden = false;
      pwaToast.hidden = false;
      return;
    }
    // Desktop: push works straight in the browser. Only nudge when it's
    // actually available and the user hasn't decided yet — a denied
    // permission can't be re-prompted, and no point if the server can't send.
    if (!pushSupported() || Notification.permission !== 'default') return;
    try {
      if (!(await rootApi('/push/status')).enabled) return;
    } catch { return; }
    pwaNotifyBody.hidden = false;
    pwaToast.hidden = false;
  }

  // Land on the wheel (and dinner) a notification points at, from a URL
  // like "/?dinner=<id>#<wheel-id>" — used both on cold start and when the
  // running app is refocused by a notification tap.
  function navigateToDeepLink(rawUrl) {
    let target;
    try { target = new URL(rawUrl, location.origin); } catch { return; }
    const wheel = target.hash.replace(/^#/, '');
    const dinner = new URLSearchParams(target.search).get('dinner');
    if (dinner) pendingDinnerId = dinner;
    if (!me) return;  // not signed in yet — init will read the params instead
    if (wheel && wheel !== wheelId && me.wheels.some((w) => w.id === wheel)) {
      switchWheel(wheel);  // reloads that wheel's data, then maybeOpenDinner fires
    } else {
      maybeOpenDinner();   // already on the right wheel; open straight away
    }
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
        notifyState.textContent = t('📲 One step to go — this needs the app on your Home Screen:');
        notifyIosHint.hidden = false;
      } else {
        notifyState.textContent = t("📴 This browser doesn't support push notifications.");
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
      notifyState.textContent = t("📴 The server can't send notifications yet — the README's notifications section explains the one-time setup (install pywebpush, restart).");
      return;
    }
    if (Notification.permission === 'denied') {
      notifyState.textContent = t('🔕 Notifications are blocked for this site — allow them again in your browser or system settings, then come back here.');
      return;
    }
    const sub = await pushSubscription().catch(() => null);
    if (sub && Notification.permission === 'granted') {
      notifyState.textContent = t('✅ Notifications are on for this device.');
      notifyDisable.hidden = false;
    } else {
      notifyState.textContent = t('💤 Notifications are off for this device.');
      notifyEnable.hidden = false;
    }
  }

  notifyBtn.addEventListener('click', () => {
    notifyModal.showModal();
    renderNotifyModal();
  });
  closeNotifyBtn.addEventListener('click', () => notifyModal.close());

  // Turn push on for this device: check the server can send, get the
  // browser's permission (must be inside a user gesture), subscribe, and
  // register that subscription. Throws with a human message on any snag,
  // so callers can surface it. Shared by the modal and the desktop toast.
  async function enablePush() {
    const server = await rootApi('/push/status');
    if (!server.enabled) throw new Error(t('the server has push notifications disabled'));
    if (await Notification.requestPermission() !== 'granted') {
      throw new Error(t("permission wasn't granted"));
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
  }

  notifyEnable.addEventListener('click', async () => {
    notifyError.textContent = '';
    notifyEnable.disabled = true;
    try {
      await enablePush();
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

  const infoPoll = document.getElementById('info-poll');
  const infoPollSummary = document.getElementById('info-poll-summary');
  const infoPollActions = document.getElementById('info-poll-actions');

  const pollModal = document.getElementById('poll-modal');
  const closePollBtn = document.getElementById('close-poll-btn');
  const pollTitle = document.getElementById('poll-title');
  const pollSub = document.getElementById('poll-sub');
  const pollLoading = document.getElementById('poll-loading');
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
  const pollNightsRow = document.getElementById('poll-nights-row');
  const pollNights = document.getElementById('poll-nights');

  // Both refreshed from the server (admin-configurable) whenever a poll's
  // availability loads — see loadPollAvailability. The defaults only stand
  // in for the moment before that first fetch returns.
  let pollHorizonDays = 60;  // how far ahead a poll can reach
  const TRIP_POLL_HORIZON_DAYS = 365;  // mirrors the server's limit for trip polls
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

  // A restaurant's calendar spot: its address when one is set (with the
  // name kept in front so the entry stays recognisable), else the name.
  function calendarSpot(name, location) {
    return location ? `${name}, ${location}` : name;
  }

  // Add-to-calendar without any API: a floating-time VEVENT the device
  // reads in its own zone (right for a dinner), and a Google template URL.
  function icsEscape(s) {
    return s.replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n');
  }

  function icsForPick(name, dateStr, location) {
    const day = dateStr.replace(/-/g, '');
    const uid = `wheel-${dateStr}-${Math.random().toString(36).slice(2)}@wheel-of-choice`;
    return [
      'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Wheel of Choice//EN', 'BEGIN:VEVENT',
      `UID:${uid}`, `DTSTART:${day}T190000`, `DTEND:${day}T210000`,
      `SUMMARY:${icsEscape('🍽️ ' + name)}`, `LOCATION:${icsEscape(calendarSpot(name, location))}`,
      'END:VEVENT', 'END:VCALENDAR',
    ].join('\r\n');
  }

  // A trip: an all-day event from the start day up to (not including)
  // the day after it ends — DTEND is exclusive for all-day events.
  function icsForTrip(name, start, end) {
    const uid = `wheel-trip-${start}-${Math.random().toString(36).slice(2)}@wheel-of-choice`;
    return [
      'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Wheel of Choice//EN', 'BEGIN:VEVENT',
      `UID:${uid}`, `DTSTART;VALUE=DATE:${start.replace(/-/g, '')}`,
      `DTEND;VALUE=DATE:${addDays(end || start, 1).replace(/-/g, '')}`,
      `SUMMARY:${icsEscape('🧳 ' + name)}`,
      'END:VEVENT', 'END:VCALENDAR',
    ].join('\r\n');
  }

  function addDays(iso, n) {
    const [y, m, d] = iso.split('-').map(Number);
    return isoLocal(new Date(y, m - 1, d + n));
  }

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function downloadIcs(name, dateStr, location) {
    saveBlob(new Blob([icsForPick(name, dateStr, location)], { type: 'text/calendar' }),
      `dinner-${name.replace(/[^\w]+/g, '-').toLowerCase()}.ics`);
  }

  function downloadTripIcs(name, start, end) {
    saveBlob(new Blob([icsForTrip(name, start, end)], { type: 'text/calendar' }),
      `trip-${name.replace(/[^\w]+/g, '-').toLowerCase()}.ics`);
  }

  function googleTripUrl(name, start, end) {
    return 'https://calendar.google.com/calendar/render?action=TEMPLATE'
      + `&text=${encodeURIComponent('🧳 ' + name)}`
      + `&dates=${start.replace(/-/g, '')}/${addDays(end || start, 1).replace(/-/g, '')}`
      + `&details=${encodeURIComponent('Locked in with Wheel of Choice')}`;
  }

  // A travel poll's option: its start date and the day you travel home.
  function tripRange(start, nights) {
    return t('{start} → {end}', { start: prettyDate(start), end: prettyDate(addDays(start, nights)) });
  }

  // "Busy" for a trip: any evening of it your calendar says is taken.
  function tripAvail(start, nights) {
    let any = false;
    for (let i = 0; i < nights; i++) {
      const st = pollAvail[addDays(start, i)];
      if (st === 'busy') return 'busy';
      if (st === 'free') any = true;
    }
    return any ? 'free' : undefined;
  }

  function pollIsTrip(poll) {
    return !!(poll && poll.nights);
  }

  function googleCalUrl(name, dateStr, location) {
    const day = dateStr.replace(/-/g, '');
    const dates = `${day}T190000/${day}T210000`;  // leave the slash literal — Google wants it raw
    return 'https://calendar.google.com/calendar/render?action=TEMPLATE'
      + `&text=${encodeURIComponent('🍽️ ' + name)}&dates=${dates}`
      + `&details=${encodeURIComponent('Locked in with Wheel of Choice')}`
      + `&location=${encodeURIComponent(calendarSpot(name, location))}`;
  }

  // ── Info-modal poll section ───────────────────────────────────────
  function renderInfoPoll() {
    const entry = infoEntry;
    if (!entry || !hasPolls()) { infoPoll.hidden = true; return; }
    infoPoll.hidden = false;
    const travel = isTravelWheel();
    const poll = entry.poll;
    infoPollActions.innerHTML = '';
    if (!poll) {
      infoPollSummary.textContent = travel
        ? t("Find dates that work for everyone — put a few up and let them vote 🗳️")
        : t("Great pick — now find an evening you're all free 🗳️");
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-primary btn-small';
      b.textContent = t('📅 Start a date poll');
      b.addEventListener('click', () => openPollModal(entry, 'propose'));
      infoPollActions.append(b);
    } else if (poll.status === 'locked' && pollIsTrip(poll)) {
      const end = addDays(poll.locked_date, poll.nights);
      infoPollSummary.textContent = t(`🗓️ {dates} — it's in the diary!`, { dates: tripRange(poll.locked_date, poll.nights) });
      ghostBtn(infoPollActions, t('⬇️ Add to calendar (.ics)'), () => downloadTripIcs(entry.name, poll.locked_date, end));
      const g = document.createElement('a');
      g.className = 'btn btn-ghost btn-small';
      g.href = googleTripUrl(entry.name, poll.locked_date, end);
      g.target = '_blank';
      g.rel = 'noopener noreferrer';
      g.textContent = t('📆 Google Calendar');
      infoPollActions.append(g);
      const scrap = document.createElement('button');
      scrap.type = 'button';
      scrap.className = 'linklike';
      scrap.textContent = t('Scrap the date & poll again…');
      scrap.addEventListener('click', () => { pollEntryId = entry.id; scrapPoll(); });
      infoPollActions.append(scrap);
    } else if (poll.status === 'locked') {
      infoPollSummary.textContent = t(`🗓️ {date} — it's a date!`, { date: prettyDate(poll.locked_date) });
      // the entry only carries name + flag; the address lives on the
      // destination (gone if it was deleted since — the name still works)
      const dest = state.destinations.find((x) => x.id === entry.dest_id);
      const loc = destLocation(dest);
      ghostBtn(infoPollActions, t('⬇️ Add to calendar (.ics)'), () => downloadIcs(entry.name, poll.locked_date, loc));
      const g = document.createElement('a');
      g.className = 'btn btn-ghost btn-small';
      g.href = googleCalUrl(entry.name, poll.locked_date, loc);
      g.target = '_blank';
      g.rel = 'noopener noreferrer';
      g.textContent = t('📆 Google Calendar');
      infoPollActions.append(g);
      const scrap = document.createElement('button');
      scrap.type = 'button';
      scrap.className = 'linklike';
      scrap.textContent = t('Scrap the date & poll again…');
      scrap.addEventListener('click', () => { pollEntryId = entry.id; scrapPoll(); });
      infoPollActions.append(scrap);
    } else {
      const voted = poll.votes.length;
      infoPollSummary.textContent = (pollIsTrip(poll)
        ? t(`{count} start dates up · {voted} of {members} voted`, { count: poll.dates.length, voted, members: poll.members })
        : t(`{count} evenings up · {voted} of {members} voted`, { count: poll.dates.length, voted, members: poll.members }))
        + (poll.waiting_names.length ? ` · ${t('waiting for {names}', { names: poll.waiting_names.join(' & ') })}` : '');
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-primary btn-small';
      b.textContent = t('🗳️ Vote / view poll');
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

  function setPollLegend(el) {
    if (pollLinked) {
      el.innerHTML = t(`<span class="cal-dot busy"></span>busy that evening (from {hour})`, { hour: fmtHour(pollEveningFrom) })
        + t('<span class="cal-dot free"></span>free — <em>from your calendar; all still tappable</em>');
    } else if (pollCalEnabled) {
      el.textContent = t('📆 Link your calendar (top bar) and your busy evenings mark themselves.');
    } else {
      el.textContent = '';
    }
  }

  async function openPollModal(entry, mode) {
    pollEntryId = entry.id;
    pollError.textContent = '';
    // Open the modal right away with a loading note — polling live ICS
    // feeds can take a few seconds and a blank wait reads as a hang.
    const poll = entry.poll;
    pollTitle.textContent = mode !== 'propose' && poll
      ? (poll.status === 'locked' ? t("🗓️ It's a date") : t('🗳️ When can everyone make it?'))
      : (isTravelWheel() ? t('🗳️ Find dates for the trip') : t('🗳️ Find an evening'));
    pollSub.textContent = `${entry.flag} ${entry.name}`;
    pollPropose.hidden = true;
    pollVoteView.hidden = true;
    pollLoading.hidden = false;
    if (!pollModal.open) pollModal.showModal();
    // starting a poll → pull the freshest calendar; viewing an existing one
    // rides the cache (fast, and busy hints are only ever hints there)
    await loadPollAvailability(mode === 'propose');
    pollLoading.hidden = true;
    if (!pollModal.open) return;  // closed while the calendar was fetching
    const latest = currentPollEntry() || entry;
    if (mode === 'propose' && !latest.poll) showPollPropose(latest);
    else showPollVote(latest);
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
    const horizon = new Date(today); horizon.setDate(today.getDate() + currentPollHorizon());
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
        if (st === 'busy') { cell.classList.add('busy'); cell.title = t("you're busy that evening"); }
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

  // Trips are planned further ahead than dinners.
  function currentPollHorizon() {
    return isTravelWheel() ? Math.max(pollHorizonDays, TRIP_POLL_HORIZON_DAYS) : pollHorizonDays;
  }

  function shiftPollMonth(delta) {
    const base = pollViewMonth || monthStart(new Date());
    pollViewMonth = new Date(base.getFullYear(), base.getMonth() + delta, 1);
    buildDateGrid();
  }

  function syncCreateBtn() {
    pollCreateBtn.disabled = pollSelected.size < 2;
    if (isTravelWheel()) {
      pollCreateBtn.textContent = pollSelected.size < 2
        ? t('Pick at least two start dates') : t(`📅 Put {size} start dates up`, { size: pollSelected.size });
      return;
    }
    pollCreateBtn.textContent = pollSelected.size < 2
      ? t('Pick at least two evenings') : t(`📅 Put {size} evenings up`, { size: pollSelected.size });
  }

  function showPollPropose(entry) {
    const travel = isTravelWheel();
    pollTitle.textContent = travel ? t('🗳️ Find dates for the trip') : t('🗳️ Find an evening');
    pollSub.textContent = travel
      ? t(`Pick a trip length and a few start dates for {flag} {name}, then put them to the group.`, { flag: entry.flag, name: entry.name })
      : t(`Pick the evenings that could work for {flag} {name}, then put them to the group.`, { flag: entry.flag, name: entry.name });
    pollNightsRow.hidden = !travel;
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
    pollTitle.textContent = poll.status === 'locked' ? t("🗓️ It's a date") : t('🗳️ When can everyone make it?');
    pollSub.textContent = t(`{flag} {name} · started by {by_name}`, { flag: entry.flag, name: entry.name, by_name: poll.by_name });
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
      dl.textContent = pollIsTrip(poll) ? tripRange(date, poll.nights) : prettyDate(date);
      const st = pollIsTrip(poll) ? tripAvail(date, poll.nights) : pollAvail[date];
      if (st === 'busy' || st === 'free') {
        const dot = document.createElement('span');
        dot.className = `cal-dot ${st}`;
        dl.append(dot);
      }
      const voters = document.createElement('div');
      voters.className = 'poll-row-voters';
      const yes = poll.votes.filter((v) => v.dates.includes(date)).map((v) => v.name);
      voters.textContent = yes.length ? `👍 ${yes.join(' & ')}` : t('no one yet');
      main.append(dl, voters);
      li.append(main);
      if (poll.status === 'locked') {
        if (poll.locked_date === date) {
          const badge = document.createElement('span');
          badge.className = 'poll-locked';
          badge.textContent = t('🔒 locked');
          li.append(badge);
        }
      } else {
        const tick = document.createElement('button');
        tick.type = 'button';
        tick.className = 'poll-tick' + (myVote.has(date) ? ' on' : '');
        tick.textContent = myVote.has(date) ? t('✓ me') : t('I can');
        tick.addEventListener('click', () => toggleVote(date));
        li.append(tick);
        if (poll.unanimous.includes(date)) {
          const lock = document.createElement('button');
          lock.type = 'button';
          lock.className = 'btn btn-primary btn-small';
          lock.textContent = t('Lock it in 🔒');
          lock.addEventListener('click', () => lockDate(date));
          li.append(lock);
        }
      }
      pollRows.append(li);
    }
    if (poll.status === 'locked') {
      pollFoot.textContent = t(`Locked in by {locked_by_name}. Enjoy! 🎉`, { locked_by_name: poll.locked_by_name });
    } else if (poll.my_dates == null) {
      pollFoot.textContent = t('Tick the evenings you can make.');
    } else if (poll.waiting_names.length) {
      pollFoot.textContent = t('Waiting for {names} to vote.', { names: poll.waiting_names.join(' & ') });
    } else if (!poll.unanimous.length) {
      pollFoot.textContent = t("No evening works for everyone yet 😬 — scrap it and try different dates.");
    } else {
      pollFoot.textContent = t("Everyone's voted — lock in an evening that works! 🔒");
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
      if (isTravelWheel()) syncStatusControls();  // a locked trip fills in its dates
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
    if (!confirm(t('Scrap this date poll for everyone on the wheel?'))) return;
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
      const body = { dates };
      if (isTravelWheel()) body.nights = Number(pollNights.value) || 0;
      const res = await api(`/history/${pollEntryId}/poll`, {
        method: 'POST', body: JSON.stringify(body),
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
      if (updated) {
        infoEntry = updated;
        renderInfoPoll();
        if (isTravelWheel()) syncStatusControls();
      }
    }
    if (pollModal.open && pollPropose.hidden) {
      const entry = currentPollEntry();
      if (entry) showPollVote(entry); else pollModal.close();
    }
  }

  // ── Your account & calendars (account.js) ─────────────────────────
  const { openAccount, syncCalendarButton } = initAccount({
    api: rootApi,
    me: () => me,
    updateMe: (fresh) => { me = { ...me, ...fresh }; },
    startSso,
  });

  // ── Admin panel (admin.js) ────────────────────────────────────────
  const { loadVersion, checkForUpdate } = initAdmin({
    api: rootApi,
    me: () => me,
    token: () => token,
    refreshMe: async () => { me = await rootApi('/me'); applyMe(); },
    forceLogout,
    copyToClipboard,
    setPollWindow: (horizon, eveningFrom) => { pollHorizonDays = horizon; pollEveningFrom = eveningFrom; },
  });

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
        ? [t('No destinations match'), t('— loosen the filters!')]
        : (state.destinations.some((d) => d.enabled) && activeTags().length
          ? [t('Nothing has those tags'), t('— pick another tag!')]
          : [t('Nothing on the wheel yet'), isFoodWheel() ? t('— add some restaurants!') : t('— add some entries!')]);
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
    if (blockedByPending()) {
      const p = state.round.pending;
      wheelHint.textContent = t(`{by_name}'s pick {flag} {name} is still waiting — okay or veto it first.`, { by_name: p.by_name, flag: p.flag, name: p.name });
      return;
    }
    if (currentSegments.length === 1) {
      // Only one option left — no suspense needed.
      showResult(currentSegments[0]);
      return;
    }

    spinning = true;
    spinBtn.disabled = true;
    wheelHint.textContent = t('Round and round it goes… 🤞');

    // With reduced motion the wheel takes a shorter, calmer spin instead of
    // the long dramatic one — but it's still a real, several-turn spin, not a
    // twitch. (extraTurns must stay well above 0: with 0 the whole spin is
    // just Math.random()*2π of travel, which is sometimes a near-zero nudge
    // that looks like the wheel stops after a fraction of a second.)
    const reduce = prefersReducedMotion();
    const startRotation = rotation;
    const extraTurns = reduce ? 3 : 5 + Math.random() * 3;
    const targetRotation = startRotation + extraTurns * Math.PI * 2 + Math.random() * Math.PI * 2;
    const duration = reduce ? 2200 : 4400;
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
      { label: t('Wikivoyage travel guide'), url: `https://en.wikivoyage.org/wiki/${slug}` },
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

  function mapsSearchUrl(name, location) {
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(calendarSpot(name, location))}`;
  }

  // Booking-search links built from the name — the tabs you actually
  // open once a pick is made. Zero data to maintain. Restaurants get a
  // maps search instead of flights, sharpened by the address if one is set.
  function planLinks(name, location) {
    if (!isTravelWheel() && !isFoodWheel()) return [];
    if (!isTravelWheel()) {
      return [
        { label: t('📍 Find it on Maps'), url: mapsSearchUrl(name, location) },
      ];
    }
    return [
      { label: t('✈️ Flights'), url: `https://www.google.com/travel/flights?q=${encodeURIComponent(`flights to ${name}`)}` },
      { label: t('🛏️ Stays'), url: `https://www.booking.com/searchresults.html?ss=${encodeURIComponent(name)}` },
    ];
  }

  // The restaurant's address as a tappable maps link, or nothing.
  function setLocationLine(el, d) {
    const place = (d && isFoodWheel() && d.place) || null;
    const loc = (place && place.address) || (d && isFoodWheel() && d.location) || '';
    el.hidden = !loc;
    el.innerHTML = '';
    if (!loc) return;
    const a = document.createElement('a');
    // link to the exact pinned place when we have it, else a name search
    a.href = (place && place.url) || mapsSearchUrl(d.name, loc);
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = `📍 ${loc}`;
    el.append(a);
  }

  // The best address string we hold for a restaurant — the exact Google
  // place's address wins over the free-typed one. Used for the calendar.
  function destLocation(d) {
    return (d && ((d.place && d.place.address) || d.location)) || '';
  }

  // The info view: opened from a spin history entry (with trip status
  // controls) or from an entry in the manage list (without them).
  let infoEntry = null; // the history entry being viewed, if any

  function showInfoModal(d, historyItem) {
    infoEntry = historyItem || null;
    const name = d ? d.name : historyItem.name;
    infoFlag.textContent = d ? d.flag : historyItem.flag;
    infoName.textContent = name;
    infoTags.textContent = d ? describe(d) : t('No longer on this wheel');
    setLocationLine(infoLocation, d);
    const notes = d && d.notes;
    infoNotes.hidden = !notes;
    infoNotes.textContent = notes || '';
    const links = d ? destLinks(d) : fallbackLinks(name);
    const plan = planLinks(name, destLocation(d));
    renderLinkList(infoLinks, links);
    renderLinkList(infoPlan, plan);
    document.getElementById('info-links-title').hidden = !links.length;
    document.getElementById('info-plan-title').hidden = !plan.length;
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
    tripEnd.value = infoEntry.trip_end || '';
    tripEnd.min = infoEntry.trip_date || '';
    tripEnd.disabled = !infoEntry.trip_date;
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
      // marking "been there" took it off the wheel (or un-marking put it back)
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

  function saveTripDates(changes) {
    if (!infoEntry) return;
    updateHistoryEntry(changes).catch((err) => {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
      syncStatusControls();
    });
  }

  tripDate.addEventListener('change', () => {
    const changes = { trip_date: tripDate.value };
    // an end before the new start (or with no start at all) goes
    if (!tripDate.value || (tripEnd.value && tripEnd.value < tripDate.value)) changes.trip_end = '';
    saveTripDates(changes);
  });
  tripEnd.addEventListener('change', () => saveTripDates({ trip_end: tripEnd.value }));

  // ── Result modal ──────────────────────────────────────────────────
  function describe(d) {
    if (!isTravelWheel()) {
      const star = starCount(d) >= 2
        ? t(`🌟 starred by {count} of you`, { count: starCount(d) })
        : (isFavorite(d) ? t('⭐ favourite') : '');
      return [star, ...(d.tags || []).map((tag) => `#${tag}`)].filter(Boolean).join('  ·  ');
    }
    const parts = [
      starCount(d) >= 2 ? t(`🌟 starred by {count} of you`, { count: starCount(d) }) : (isFavorite(d) ? t('⭐ favourite') : ''),
      t(BUDGET_LABELS[d.budget]),
      t(DISTANCE_LABELS[d.distance]),
      d.vibes.map((v) => t(VIBE_LABELS[v])).join(' · '),
    ];
    return parts.filter(Boolean).join('  ·  ');
  }

  function showResult(destination) {
    pendingResult = destination;
    resultFlag.textContent = destination.flag;
    resultName.textContent = destination.name;
    resultTags.textContent = describe(destination);
    setLocationLine(resultLocation, destination);
    resultNotes.hidden = !destination.notes;
    resultNotes.textContent = destination.notes || '';
    renderLinkList(resultLinks, [...destLinks(destination), ...planLinks(destination.name, destLocation(destination))]);
    if (isTravelWheel()) {
      vetoBtn.disabled = state.round.my_veto_used;
      vetoNote.textContent = state.round.my_veto_used
        ? t('You\'ve already used your veto this round — the wheel has spoken!')
        : t('Each of you gets one veto per round — yours is still up your sleeve.');
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
        if (isFoodWheel()) {
          const entry = res.history.find((e) => e.dest_id === result.id);
          if (entry) openPollModal(entry, 'propose');
        }
      } else {
        wheelHint.textContent = t(`Waiting for {names} to okay {flag} {name} — they can still veto ✋`, { names: waitingNames(res.round.pending), flag: result.flag, name: result.name });
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
    const waiting = state.round.pending;
    let res;
    try {
      res = await api('/round/veto', {
        method: 'POST',
        body: JSON.stringify({ dest_id: result.id }),
      });
      applyRound(res);
    } catch (err) {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
      refresh();
      return;
    }
    if (res.settled) {
      // your veto was your last say on the waiting pick — so that one stands
      if (waiting) wheelHint.textContent = finalMessage(waiting);
      syncWheelData();
      return;
    }
    if (currentSegments.length > 0) {
      spin();
    } else {
      wheelHint.textContent = t('Nothing left after that veto — loosen the filters or start over.');
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
    pendingWithdrawBtn.hidden = !(p && p.mine);
    if (!p) return;
    if (p.mine) {
      pendingText.textContent = t(`⏳ You picked {flag} {name} — waiting for {names} to give it a thumbs-up.`, { flag: p.flag, name: p.name, names: waitingNames(p) });
      pendingActions.hidden = true;
    } else if (p.i_confirmed) {
      pendingText.textContent = t(`👍 You're in for {flag} {name} — waiting for {names}.`, { flag: p.flag, name: p.name, names: waitingNames(p) });
      pendingActions.hidden = true;
    } else {
      pendingText.textContent = t(`🎡 {by_name} spun {flag} {name}! Are you in — or is this your veto?`, { by_name: p.by_name, flag: p.flag, name: p.name });
      pendingActions.hidden = false;
      pendingVetoBtn.disabled = state.round.my_veto_used;
      pendingVetoBtn.title = state.round.my_veto_used
        ? t('You already used your veto this round') : '';
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
        wheelHint.textContent = t(`You're in! Still waiting for {names} to okay {flag} {name}.`, { names: waitingNames(res.round.pending), flag: p.flag, name: p.name });
      }
      applyRound(res.round);
    } catch (err) {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
    }
  });

  pendingWithdrawBtn.addEventListener('click', async () => {
    const p = state.round.pending;
    if (!p || !p.mine) return;
    try {
      const round = await api('/round/pending', { method: 'DELETE' });
      state.round = { ...state.round, pending: null };  // no "it's decided" message
      applyRound(round);
      wheelHint.textContent = t(`You took back {flag} {name} — spin again when you're ready.`, { flag: p.flag, name: p.name });
    } catch (err) {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
    }
  });

  // Someone else's pick is waiting: answer that first — a new spin would
  // otherwise trample their pick and the thumbs-ups it already has.
  function blockedByPending() {
    const p = state.round.pending;
    return !!(p && !p.mine);
  }

  pendingVetoBtn.addEventListener('click', async () => {
    const p = state.round.pending;
    if (!p || state.round.my_veto_used) return;
    try {
      applyRound(await api('/round/veto', {
        method: 'POST',
        body: JSON.stringify({ dest_id: p.dest_id }),
      }));
      wheelHint.textContent = t(`You vetoed {flag} {name} 🙅 — give it another spin!`, { flag: p.flag, name: p.name });
    } catch (err) {
      console.error(err);
      wheelHint.textContent = `⚠️ ${err.message}`;
    }
  });

  // Re-fetch the wheel's entries and history (someone else changed them).
  async function syncWheelData() {
    const id = wheelId;
    const [destinations, history] = await Promise.all([api('/destinations'), api('/history')]);
    if (id !== wheelId) return;  // switched wheels meanwhile
    state.destinations = withoutPendingDeletes('dest', destinations);
    const fresh = withoutPendingDeletes('history', history);
    if (JSON.stringify(fresh) !== JSON.stringify(state.history)) {
      state.history = fresh;
      renderHistory();
      reflectPollUpdates();  // a partner's vote or a fresh lock just landed
    }
    if (manageModal.open) renderDestList();
    refresh();
  }

  // Refresh the account summary quietly: wheel names, members, invites —
  // and notice being taken off the wheel we're looking at.
  async function syncMe() {
    const fresh = await rootApi('/me');
    const before = JSON.stringify([me.wheels, me.invites]);
    me = { ...fresh, prefs: me.prefs };
    if (!me.wheels.some((w) => w.id === wheelId)) {
      wheelId = '';
      applyMe();
      return;
    }
    if (JSON.stringify([me.wheels, me.invites]) !== before) {
      renderTabs();
      renderWheelInvites();
      if (shareModal.open) renderShareSettings(currentWheel());
    }
  }

  // Other members' devices find out about vetoes, pending picks, edits
  // and fresh history by polling — plenty for people deciding on a sofa.
  // The round carries the wheel's revision; only when that moves are the
  // entries and history fetched again.
  let pollTick = 0;
  setInterval(async () => {
    if (!me || !currentWheel() || appView.hidden || document.hidden || spinning) return;
    pollTick += 1;
    try {
      const round = await api('/round');
      if (round.rev !== state.rev) {
        state.rev = round.rev;
        await Promise.all([syncWheelData(), syncMe()]);
      } else if (pollTick % 6 === 0) {
        await syncMe();  // members and invites live on the accounts, not the wheel
      }
      applyRound(round);
    } catch (err) {
      // a 404 means this wheel isn't ours any more (removed, or it's gone)
      if (err.status === 404) syncMe().catch(() => {});
    }
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

  // ── Tag filter (restaurant & custom wheels) ──────────────────────
  // One chip per tag used on the wheel; picking some keeps only entries
  // carrying at least one of them. Saved per person, like trip filters.
  function renderTagFilter() {
    const tags = isTravelWheel() ? [] : wheelTags();
    tagFilter.hidden = !tags.length;
    const key = JSON.stringify([tags, activeTags()]);
    if (tagFilter.dataset.key === key) return;  // nothing changed — keep focus etc.
    tagFilter.dataset.key = key;
    tagFilterChips.innerHTML = '';
    if (!tags.length) return;
    const active = activeTags();
    const chip = (label, value, on) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'seg-btn';
      b.textContent = label;
      setActive(b, on);
      b.addEventListener('click', () => {
        if (spinning) return;
        if (value === null) {
          state.filters.tags = [];
        } else {
          const set = new Set(activeTags());
          set.has(value) ? set.delete(value) : set.add(value);
          state.filters.tags = [...set];
        }
        saveFilters();
        refresh();
      });
      tagFilterChips.append(b);
    };
    chip(t('Any'), null, active.length === 0);
    for (const tag of tags) chip(`#${tag}`, tag, active.includes(tag));
  }

  // ── Undo for deletions ────────────────────────────────────────────
  // Deleting an entry or a pick takes effect on screen at once, but only
  // reaches the server a few seconds later — "↩️ Undo" in the toast calls
  // it off. One deletion is in flight at a time: starting another sends
  // the previous one straight away, and so does closing the page.
  const undoToast = document.getElementById('undo-toast');
  const undoText = document.getElementById('undo-text');
  const undoBtn = document.getElementById('undo-btn');
  const UNDO_MS = 6000;
  const pendingDeletes = { dest: new Set(), history: new Set() };
  let undoJob = null;

  function withoutPendingDeletes(kind, list) {
    return list.filter((item) => !pendingDeletes[kind].has(item.id));
  }

  function deferDelete({ kind, id, text, hide, restore, path }) {
    flushDelete();
    pendingDeletes[kind].add(id);
    hide();
    const job = { kind, id, restore, path, wheel: wheelId };
    job.timer = setTimeout(() => flushDelete(), UNDO_MS);
    undoJob = job;
    undoText.textContent = text;
    showUndoToast(true);
  }

  // An open modal makes the rest of the page inert, so the toast moves
  // into whichever dialog is open — and back out when that one closes —
  // to keep Undo clickable while the manage list is up.
  function showUndoToast(on) {
    undoToast.hidden = !on;
    if (on) (document.querySelector('dialog[open]') || document.body).append(undoToast);
  }

  document.addEventListener('close', (e) => {
    if (e.target.contains(undoToast)) {
      (document.querySelector('dialog[open]') || document.body).append(undoToast);
    }
  }, true);  // 'close' doesn't bubble — catch it on the way down

  function flushDelete(keepalive = false) {
    const job = undoJob;
    if (!job) return;
    undoJob = null;
    clearTimeout(job.timer);
    showUndoToast(false);
    rootApi(`/wheels/${job.wheel}${job.path}`, { method: 'DELETE', keepalive })
      .catch((err) => {
        console.error(err);
        if (job.wheel === wheelId && err.status !== 404) {
          job.restore();
          wheelHint.textContent = `⚠️ ${err.message}`;
        }
      })
      .finally(() => pendingDeletes[job.kind].delete(job.id));
  }

  undoBtn.addEventListener('click', () => {
    const job = undoJob;
    if (!job) return;
    undoJob = null;
    clearTimeout(job.timer);
    showUndoToast(false);
    pendingDeletes[job.kind].delete(job.id);
    if (job.wheel === wheelId) job.restore();
  });

  window.addEventListener('pagehide', () => flushDelete(true));

  // ── Downloads that need the login token ───────────────────────────
  async function downloadFromApi(path, fallbackName) {
    const headers = { 'X-Lang': currentLanguage() };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`/api/wheels/${wheelId}${path}`, { headers });
    if (!res.ok) {
      let message = '';
      try { message = (await res.json()).error; } catch { /* not JSON */ }
      throw new Error(message || t(`Server said {status} for {path}`, { status: res.status, path }));
    }
    const disposition = res.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="([^"]+)"/);
    saveBlob(await res.blob(), match ? match[1] : fallbackName);
  }

  function exportButton(id, path, name) {
    document.getElementById(id).addEventListener('click', () => {
      downloadFromApi(path, name).catch((err) => {
        console.error(err);
        wheelHint.textContent = `⚠️ ${err.message}`;
      });
    });
  }
  exportButton('export-csv-btn', '/export/history.csv', 'history.csv');
  exportButton('export-ics-btn', '/export/history.ics', 'history.ics');
  exportButton('export-wheel-btn', '/export/wheel.json', 'wheel.json');

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
    syncCatalogStatus.textContent = t('Checking the catalogue…');
    try {
      const res = await api('/destinations/sync', { method: 'POST' });
      state.destinations = res.destinations;
      renderDestList();
      syncCatalogStatus.textContent = res.added
        ? tn(res.added, '✅ Added 1 new place — scroll down to find them.', '✅ Added {n} new places — scroll down to find them.')
        : t('👍 You’re already up to date — nothing new in the catalogue.');
    } catch (err) {
      console.error(err);
      syncCatalogStatus.textContent = t('⚠️ Could not reach the server — try again in a moment.');
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
      checkbox.title = t('On the wheel?');
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
      star.title = t('Star it (your star) — every member\'s star widens this slice of the wheel');
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
      name.title = t('Info & links about this place');
      name.textContent = `${d.flag} ${d.name}`;
      name.addEventListener('click', () => showInfoModal(d, null));

      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      meta.textContent = isTravelWheel()
        ? `${d.budget} · ${t(DISTANCE_LABELS[d.distance])}`
        : ((d.tags || []).length ? d.tags.map((tag) => `#${tag}`).join(' ') : (d.notes ? d.notes.slice(0, 40) : ''));

      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'edit-btn';
      edit.title = t('Edit this entry');
      edit.textContent = '✏️';
      edit.addEventListener('click', () => enterEditMode(d));

      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'del-btn';
      del.title = t('Remove this entry');
      del.textContent = '✕';
      del.addEventListener('click', () => {
        const index = state.destinations.findIndex((x) => x.id === d.id);
        deferDelete({
          kind: 'dest',
          id: d.id,
          path: `/destinations/${d.id}`,
          text: t('{name} removed', { name: `${d.flag} ${d.name}` }),
          hide: () => {
            state.destinations = state.destinations.filter((x) => x.id !== d.id);
            if (state.editingId === d.id) exitEditMode();
            renderDestList();
            refresh();
          },
          restore: () => {
            if (!state.destinations.some((x) => x.id === d.id)) {
              state.destinations.splice(Math.min(index, state.destinations.length), 0, d);
            }
            if (manageModal.open) renderDestList();
            refresh();
          },
        });
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

  // Location (restaurant wheels): an address field plus a Google Places
  // search. When the server has a Maps key, searching lists real places to
  // pick from — the pick fills the address and pins the exact place so its
  // precise address rides into the calendar. Without a key it falls back to
  // opening a Google Maps search in a new tab (what it did before).
  const addLocation = document.getElementById('add-location');
  const addLocationSearch = document.getElementById('add-location-search');
  const mapsResults = document.getElementById('maps-results');
  const mapsNote = document.getElementById('maps-note');
  let addPlace = null;  // the Google place chosen for the entry, or null

  function clearMapsResults() {
    mapsResults.innerHTML = '';
    mapsResults.hidden = true;
  }

  function setMapsNote(text, cls) {
    mapsNote.textContent = text || '';
    mapsNote.hidden = !text;
    mapsNote.className = 'maps-note' + (cls ? ` ${cls}` : '');
  }

  function resetLocationSearch() {
    addPlace = null;
    clearMapsResults();
    setMapsNote('');
  }

  function pickPlace(place) {
    addPlace = place;
    if (place.address) addLocation.value = place.address;  // programmatic → no 'input' event
    clearMapsResults();
    setMapsNote(t(`📍 Linked to {address}`, { address: place.name || place.address }), 'ok');
  }

  async function runMapsSearch() {
    const q = addLocation.value.trim() || addNameInput.value.trim();
    if (q.length < 2) { addNameInput.focus(); return; }
    // no key on this server → the old behaviour: open Maps in a new tab
    if (!me || !me.maps_enabled) {
      window.open(mapsSearchUrl(addNameInput.value.trim() || q, addLocation.value.trim()), '_blank', 'noopener');
      return;
    }
    clearMapsResults();
    setMapsNote(t('🔍 Searching…'));
    addLocationSearch.disabled = true;
    try {
      const { results } = await rootApi(`/maps/search?q=${encodeURIComponent(q)}`);
      if (!results.length) { setMapsNote(t('No places found — try the name with the town.')); return; }
      setMapsNote('');
      for (const r of results) {
        const li = document.createElement('li');
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'maps-result';
        const nm = document.createElement('span');
        nm.className = 'maps-result-name';
        nm.textContent = r.name || r.address;
        const ad = document.createElement('span');
        ad.className = 'maps-result-addr';
        ad.textContent = r.address || '';
        b.append(nm, ad);
        b.addEventListener('click', () => pickPlace(r));
        li.append(b);
        mapsResults.append(li);
      }
      mapsResults.hidden = false;
    } catch (err) {
      setMapsNote(`⚠️ ${err.message}`, 'err');
    } finally {
      addLocationSearch.disabled = false;
    }
  }

  addLocationSearch.addEventListener('click', runMapsSearch);

  // Enter in the address field searches rather than submitting the form
  addLocation.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); runMapsSearch(); }
  });

  // Hand-editing the address unlinks the pinned place (its address no
  // longer matches) so a stale pin isn't saved with the entry
  addLocation.addEventListener('input', () => {
    if (addPlace && addLocation.value.trim() !== (addPlace.address || '')) {
      addPlace = null;
      setMapsNote('');
    }
  });

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
    label.placeholder = t('Label, e.g. Our hotel');
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
    del.title = t('Remove this link');
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
    formTitle.textContent = t(`Edit {name}`, { name: d.name });
    formSubmit.textContent = t('💾 Save changes');
    cancelEditBtn.hidden = false;
    addNameInput.value = d.name;
    addFlagInput.value = d.flag;
    if (isTravelWheel()) {
      document.getElementById('add-budget').value = d.budget;
      document.getElementById('add-distance').value = d.distance;
      setCheckedValues('add-vibes', d.vibes);
      setCheckedValues('add-seasons', d.seasons);
      setCheckedValues('add-party', d.party);
    } else {
      addTagsInput.value = (d.tags || []).join(', ');
    }
    if (isFoodWheel()) {
      addLocation.value = d.location || '';
      addPlace = d.place || null;
      clearMapsResults();
      setMapsNote(addPlace ? t(`📍 Linked to {address}`, { address: addPlace.name || addPlace.address }) : '', addPlace ? 'ok' : '');
    }
    addNotes.value = d.notes || '';
    linkRows.innerHTML = '';
    (d.links || []).forEach(addLinkRow);
    addForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
    addNameInput.focus();
  }

  function exitEditMode() {
    state.editingId = null;
    formTitle.textContent = t('Add your own');
    formSubmit.textContent = t('➕ Add it');
    cancelEditBtn.hidden = true;
    addForm.reset();
    addError.textContent = '';
    linkRows.innerHTML = '';
    resetLocationSearch();
  }

  cancelEditBtn.addEventListener('click', exitEditMode);

  addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = addNameInput.value.trim();
    if (!name) return;
    const payload = {
      name,
      flag: addFlagInput.value.trim() || (isTravelWheel() ? '📍' : (isFoodWheel() ? '🍽️' : '🎯')),
      notes: addNotes.value.trim(),
      links: collectLinks(),
    };
    if (isTravelWheel()) {
      payload.budget = document.getElementById('add-budget').value;
      payload.distance = document.getElementById('add-distance').value;
      payload.vibes = checkedValues('add-vibes');
      payload.seasons = checkedValues('add-seasons');
      payload.party = checkedValues('add-party');
    } else {
      payload.tags = addTagsInput.value.split(',').map((tag) => tag.trim()).filter(Boolean);
    }
    if (isFoodWheel()) {
      payload.location = addLocation.value.trim();
      payload.place = addPlace;  // null unless a place was picked from search
    }
    addError.textContent = '';
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
      addError.textContent = `⚠️ ${err.message || t('Could not save it — is the server still running?')}`;
    }
  });

  // ── Bulk add (a pasted list, one entry per line) ──────────────────
  const bulkLines = document.getElementById('bulk-lines');
  const bulkAddBtn = document.getElementById('bulk-add-btn');
  const bulkStatus = document.getElementById('bulk-status');

  bulkAddBtn.addEventListener('click', async () => {
    const lines = bulkLines.value;
    if (!lines.trim()) { bulkLines.focus(); return; }
    bulkAddBtn.disabled = true;
    bulkStatus.textContent = '';
    try {
      const res = await api('/destinations/bulk', { method: 'POST', body: JSON.stringify({ lines }) });
      state.destinations = withoutPendingDeletes('dest', res.destinations);
      renderDestList();
      refresh();
      bulkLines.value = '';
      const added = res.added.length;
      const skipped = res.skipped.length;
      bulkStatus.textContent = tn(added, '✅ Added 1 entry.', '✅ Added {n} entries.')
        + (skipped ? ` ${tn(skipped, '1 was already on the wheel.', '{n} were already on the wheel.')}` : '');
    } catch (err) {
      console.error(err);
      bulkStatus.textContent = `⚠️ ${err.message}`;
    } finally {
      bulkAddBtn.disabled = false;
    }
  });

  // ── History ───────────────────────────────────────────────────────
  function renderHistory() {
    historyList.innerHTML = '';
    clearHistoryBtn.hidden = state.history.length === 0;
    historyExport.hidden = state.history.length === 0;
    if (state.history.length === 0) {
      const li = document.createElement('li');
      li.className = 'history-empty';
      li.textContent = t('Nothing picked yet — give it a spin!');
      historyList.append(li);
      return;
    }
    for (const item of state.history) {
      const li = document.createElement('li');
      const main = document.createElement('div');
      main.className = 'history-main';
      const name = document.createElement('button');
      name.type = 'button';
      name.className = 'history-name';
      name.title = t('Info & links for this pick');
      name.textContent = `${item.flag} ${item.name}`;
      name.addEventListener('click', () => openDestInfo(item));
      main.append(name);
      // Restaurant picks settle a date via a poll; surface where it's at
      // right in the history so it reads at a glance — no notification needed.
      // (travel picks only once a poll is running — most are booked another way)
      const chip = isFoodWheel() || (isTravelWheel() && item.poll) ? pollStatusChip(item) : null;
      if (chip) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = `history-status ${chip.cls}`;
        b.textContent = chip.text;
        b.addEventListener('click', chip.act);
        main.append(b);
      }
      const when = document.createElement('span');
      when.className = 'when';
      const badge = item.status === 'booked' ? '📅 ' : (item.status === 'visited' ? '✅ ' : '');
      when.textContent = badge + new Date(item.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
      const titleBits = [];
      if (item.by) titleBits.push(t(`Picked by {by}`, { by: item.by }));
      if (item.status === 'booked') titleBits.push(t('booked'));
      if (item.status === 'visited') titleBits.push(t('been there'));
      if (item.trip_date) {
        titleBits.push(item.trip_end
          ? t('trip: {start} → {end}', { start: prettyDate(item.trip_date), end: prettyDate(item.trip_end) })
          : t('trip: {start}', { start: prettyDate(item.trip_date) }));
      }
      if (titleBits.length) when.title = titleBits.join(' · ');
      const side = document.createElement('div');
      side.className = 'history-side';
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'history-del';
      del.title = t('Remove this pick from the history');
      del.setAttribute('aria-label', del.title);
      del.textContent = '✕';
      del.addEventListener('click', () => deleteHistoryItem(item));
      side.append(when, del);
      li.append(main, side);
      historyList.append(li);
    }
  }

  // A restaurant history entry's date-poll state as a tappable chip:
  // {text, cls, act}. Keeps the "you still owe a vote" and "it's a date"
  // states visible for members who don't have notifications on.
  function pollStatusChip(item) {
    const poll = item.poll;
    if (!poll) {
      return { text: t('📅 No date yet — start a poll'), cls: 'todo',
        act: () => openPollModal(item, 'propose') };
    }
    if (poll.status === 'locked') {
      return { text: t(`🗓️ {date} — it's a date`, { date: prettyDate(poll.locked_date) }), cls: 'locked',
        act: () => openDestInfo(item) };
    }
    if (!Array.isArray(poll.my_dates)) {  // this member hasn't voted yet
      return { text: t('🗳️ Vote on a date'), cls: 'vote',
        act: () => openPollModal(item, 'vote') };
    }
    if (poll.unanimous && poll.unanimous.length && !poll.waiting_names.length) {
      return { text: t('✅ Everyone\'s free — lock a date in'), cls: 'vote',
        act: () => openPollModal(item, 'vote') };
    }
    const waiting = poll.waiting_names.length
      ? t('waiting for {names}', { names: poll.waiting_names.join(' & ') })
      : t('no evening works for all yet');
    return { text: t(`🗳️ Poll open — {waiting}`, { waiting }), cls: 'waiting',
      act: () => openPollModal(item, 'vote') };
  }

  function deleteHistoryItem(item) {
    const index = state.history.findIndex((e) => e.id === item.id);
    deferDelete({
      kind: 'history',
      id: item.id,
      path: `/history/${item.id}`,
      text: t('{name} removed from the history', { name: `${item.flag} ${item.name}` }),
      hide: () => {
        state.history = state.history.filter((e) => e.id !== item.id);
        renderHistory();
        if (infoEntry && infoEntry.id === item.id) infoModal.close();
      },
      restore: () => {
        if (!state.history.some((e) => e.id === item.id)) {
          state.history.splice(Math.min(index, state.history.length), 0, item);
        }
        renderHistory();
      },
    });
  }

  clearHistoryBtn.addEventListener('click', async () => {
    if (!confirm(t('Clear the whole spin history? This clears it for everyone on this wheel.'))) return;
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
    let empty;
    if (isTravelWheel()) empty = t('No matches — loosen a filter or two');
    else if (activeTags().length && state.destinations.some((d) => d.enabled)) empty = t('Nothing has those tags — pick another');
    else empty = isFoodWheel() ? t('Nothing on the wheel yet — add some spots below') : t('Nothing on the wheel yet — add some entries below');
    let count;
    if (isTravelWheel()) count = tn(n, '1 destination on the wheel', '{n} destinations on the wheel');
    else if (isFoodWheel()) count = tn(n, '1 restaurant on the wheel', '{n} restaurants on the wheel');
    else count = tn(n, '1 entry on the wheel', '{n} entries on the wheel');
    matchCount.textContent = n === 0
      ? empty
      : count + (favs > 0 ? ` · ${tn(favs, '⭐ 1 favourite with extra chance', '⭐ {n} favourites with extra chance')}` : '');
    spinBtn.disabled = n === 0 || spinning || blockedByPending();
    renderTagFilter();
    drawWheel();
  }

  spinBtn.addEventListener('click', spin);

  // ── Init ──────────────────────────────────────────────────────────
  loadVersion();
  if (ssoResult && ssoResult !== 'linked') {
    finishSso(ssoResult);
  } else if (token) {
    fetchMe().then(() => {
      // back from linking single sign-on (or failing to) — show the outcome
      if (me && (ssoResult === 'linked' || ssoError)) openAccount(ssoError ? '' : t('✅ Single sign-on linked'), ssoError);
    });
  } else {
    if (ssoError) authError.textContent = `⚠️ ${ssoError}`;
    showView('auth');
    if (inviteCode) {
      document.querySelector('.auth-tabs .tab[data-mode="register"]').click();
      inviteBanner.textContent = accountInvite
        ? t(`🎟️ You've been invited to Wheel of Choice — create your account below.`)
        : t(`🎟️ You've been invited to a shared wheel (code {inviteCode}) — create an account and you'll join it automatically.`, { inviteCode });
      inviteBanner.hidden = false;
      rootApi('/config').then(applyAuthConfig).catch(() => {});
    } else {
      applyRegistrationGate();
    }
  }

  // Brand-new server: restore a backup before anyone has an account, so
  // moving to a fresh container is a single upload.
  const setupRestoreBtn = document.getElementById('setup-restore-btn');
  const setupRestoreFile = document.getElementById('setup-restore-file');
  const setupRestoreStatus = document.getElementById('setup-restore-status');
  setupRestoreBtn.addEventListener('click', () => {
    setupRestoreFile.value = '';
    setupRestoreFile.click();
  });
  setupRestoreFile.addEventListener('change', async () => {
    const file = setupRestoreFile.files[0];
    if (!file) return;
    setupRestoreStatus.textContent = t('⏳ Restoring…');
    setupRestoreBtn.disabled = true;
    try {
      const text = await file.text();
      JSON.parse(text); // catch garbage before it travels
      const res = await rootApi('/setup/restore', { method: 'POST', body: text });
      setupRestoreStatus.textContent = res.files.includes('vapid-private-key.pem')
        ? t('✅ Restored, push key included — log in with your usual account.')
        : t('✅ Restored — log in with your usual account. (This older backup had no push key: phones need to switch notifications on again.)');
      document.querySelector('.auth-tabs .tab[data-mode="login"]').click();
      applyRegistrationGate();
      setupRestore.hidden = false; // keep the success message in view
      setupRestoreBtn.hidden = true;
    } catch (err) {
      setupRestoreStatus.textContent = err instanceof SyntaxError
        ? t('⚠️ That file isn\'t a Wheel of Choice backup.')
        : `⚠️ ${err.message}`;
    } finally {
      setupRestoreBtn.disabled = false;
    }
  });

  // On the logged-out screen, hide the "Create account" tab when the
  // server has registration closed — signing up is invite-only then, and
  // an invite link (handled above) is the way in. Idempotent, so it also
  // corrects the tab after a later log-out. Fails open: if the check
  // can't run, the tab is left as it is.
  async function applyRegistrationGate() {
    let open = true;
    try {
      const config = await rootApi('/config');
      open = config.registration_open;
      if (!applyAuthConfig(config)) return; // passwords off: no tabs to juggle
    } catch { return; }
    const registerTab = document.querySelector('.auth-tabs .tab[data-mode="register"]');
    if (registerTab) registerTab.hidden = !open;
    if (!open) {
      document.querySelector('.auth-tabs .tab[data-mode="login"]').click(); // land on the login form
      authHint.textContent = t('This server is invite-only — ask an admin for an invite link to create an account.');
    }
  }
})();
