/* Wheel of Choice — your own account: the 🔑 modal (e-mail, single
 * sign-on link, password, logged-in devices) and the 📆 My calendars
 * modal.
 *
 * Split out of app.js; state is reached through the `app` handle passed to
 * initAccount(). Returns openAccount (app.js opens it after a sign-on
 * round trip) and syncCalendarButton (shows 📆 when the server can read
 * calendars).
 */
import { t, tn } from './i18n.js';
import { formatDateTime } from './utils.js';
import { actionBtn } from './ui.js';

/**
 * @param {object} app
 * @param {Function} app.api        the app's JSON fetch helper (rootApi)
 * @param {Function} app.me         the current /me payload
 * @param {Function} app.updateMe   merge a fresh /me payload into it
 * @param {Function} app.startSso   start a sign-on round trip ('link')
 */
export function initAccount(app) {
  const api = app.api;
  const passwordBtn = document.getElementById('password-btn');
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

  // ── My calendars modal ────────────────────────────────────────────
  async function syncCalendarButton() {
    try {
      const info = await api('/me/calendars');
      calendarBtn.hidden = !info.enabled;
    } catch {
      calendarBtn.hidden = true;
    }
  }

  async function renderCalendarModal() {
    calendarError.textContent = '';
    try {
      const info = await api('/me/calendars');
      if (!info.enabled) {
        calendarState.textContent = t("📴 This server can't read calendars yet — polls still work, just without the busy-evening hints.");
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
        rm.title = t('Remove this calendar');
        rm.textContent = '✕';
        rm.addEventListener('click', () => removeFeed(feed.id));
        li.append(main, rm);
        feedList.append(li);
      }
      feedForm.hidden = info.feeds.length >= 4;
      calendarState.textContent = info.feeds.length
        ? tn(info.feeds.length, '1 calendar linked — only you see your busy evenings.', '{n} calendars linked — only you see your busy evenings.')
        : t('No calendars linked yet.');
    } catch (err) {
      calendarState.textContent = `⚠️ ${err.message}`;
    }
  }

  async function removeFeed(id) {
    calendarError.textContent = '';
    try {
      await api(`/me/calendars/${id}`, { method: 'DELETE' });
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

  const passwordCurrentRow = document.getElementById('password-current-row');
  const passwordHint = document.getElementById('password-hint');
  const emailForm = document.getElementById('email-form');
  const accountEmail = document.getElementById('account-email');
  const emailPasswordRow = document.getElementById('email-password-row');
  const emailPassword = document.getElementById('email-password');
  const emailSubmit = document.getElementById('email-submit');
  const emailState = document.getElementById('email-state');
  const emailError = document.getElementById('email-error');
  const accountSso = document.getElementById('account-sso');
  const accountSsoStatus = document.getElementById('account-sso-status');
  const accountSsoLink = document.getElementById('account-sso-link');
  const accountSsoUnlink = document.getElementById('account-sso-unlink');
  const accountSsoState = document.getElementById('account-sso-state');
  const accountSsoError = document.getElementById('account-sso-error');

  // Fill the account modal from `me` — e-mail, sign-on link, password.
  // Accounts created through single sign-on have no password yet: they
  // skip the "current password" boxes and can set a first one.
  function renderAccount() {
    const u = app.me().user;
    const hasPw = !!u.has_password;
    accountEmail.value = u.email || '';
    emailPasswordRow.hidden = !hasPw;
    emailPassword.required = hasPw;
    passwordCurrentRow.hidden = !hasPw;
    passwordCurrent.required = hasPw;
    passwordHint.textContent = hasPw
      ? t('Pick a new password for your account. You\'ll stay logged in on this device; other devices are logged out.')
      : t('Your account signs in through single sign-on and has no password. Set one to also be able to log in with your name or e-mail.');
    passwordSubmit.textContent = hasPw ? t('Change password') : t('Set password');
    const sso = app.me().sso || {};
    const passwordsOn = sso.password_login !== false;
    // with passwords switched off there's nothing to set or confirm with
    emailPasswordRow.hidden = !hasPw || !passwordsOn;
    emailPassword.required = hasPw && passwordsOn;
    passwordForm.hidden = !passwordsOn;
    passwordHint.hidden = !passwordsOn;
    passwordHint.previousElementSibling.hidden = !passwordsOn; // the "Password" heading
    accountSso.hidden = !(sso.enabled || u.sso_linked);
    accountSsoStatus.textContent = u.sso_linked
      ? t('🔗 Your account is linked — you can log in with the sign-on button.')
      : t('Not linked yet. Link it to log in with the sign-on button from now on — handy when your e-mail address here differs from the one your identity provider has.');
    accountSsoLink.hidden = u.sso_linked || !sso.enabled;
    accountSsoLink.textContent = t('🔗 Link sign-on');
    accountSsoUnlink.hidden = !u.sso_linked || !passwordsOn;
    accountSsoUnlink.disabled = !hasPw;
    accountSsoUnlink.title = hasPw ? t('Detach your account from single sign-on') : t('Set a password first, or you couldn\'t log in anymore');
  }

  function openAccount(ssoMessage = '', ssoErr = '') {
    passwordForm.reset();
    emailForm.reset();
    for (const el of [passwordState, passwordError, emailState, emailError]) el.textContent = '';
    accountSsoState.textContent = ssoMessage;
    accountSsoError.textContent = ssoErr ? `⚠️ ${ssoErr}` : '';
    renderAccount();
    loadSessions();
    passwordModal.showModal();
    if (!ssoMessage && !ssoErr) accountEmail.focus();
  }

  passwordBtn.addEventListener('click', () => openAccount());

  // ── Logged-in devices ─────────────────────────────────────────────
  const sessionList = document.getElementById('session-list');
  const endOthersBtn = document.getElementById('end-others-btn');
  const sessionError = document.getElementById('session-error');

  function renderSessions(sessions) {
    sessionList.innerHTML = '';
    for (const s of sessions) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'dest-name';
      name.textContent = s.current ? t('📍 {device} (this device)', { device: t(s.device) }) : `💻 ${t(s.device)}`;
      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      meta.textContent = t(`last active {last_seen} · since {created}`, { last_seen: formatDateTime(s.last_seen), created: formatDateTime(s.created) });
      li.append(name, meta);
      if (!s.current) {
        li.append(actionBtn(t('Log out'), t('Log this device out'), async () => {
          sessionError.textContent = '';
          try {
            renderSessions(await api(`/me/sessions/${encodeURIComponent(s.id)}`, { method: 'DELETE' }));
          } catch (err) {
            sessionError.textContent = `⚠️ ${err.message}`;
          }
        }));
      }
      sessionList.append(li);
    }
    endOthersBtn.hidden = sessions.length < 2;
  }

  async function loadSessions() {
    sessionError.textContent = '';
    try {
      renderSessions(await api('/me/sessions'));
    } catch (err) {
      sessionError.textContent = `⚠️ ${err.message}`;
    }
  }

  endOthersBtn.addEventListener('click', async () => {
    if (!confirm(t('Log out every other device? They\'ll need to log in again.'))) return;
    sessionError.textContent = '';
    try {
      renderSessions(await api('/me/sessions/end-others', { method: 'POST' }));
    } catch (err) {
      sessionError.textContent = `⚠️ ${err.message}`;
    }
  });

  emailForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    emailError.textContent = '';
    emailState.textContent = '';
    emailSubmit.disabled = true;
    try {
      app.updateMe(await api('/me/email', {
        method: 'PUT',
        body: JSON.stringify({ email: accountEmail.value.trim(), password: emailPassword.value }),
      }));
      emailPassword.value = '';
      renderAccount();
      emailState.textContent = app.me().user.email ? t('✅ E-mail saved') : t('✅ E-mail removed');
    } catch (err) {
      emailError.textContent = `⚠️ ${err.message}`;
    } finally {
      emailSubmit.disabled = false;
    }
  });

  accountSsoLink.addEventListener('click', async () => {
    accountSsoError.textContent = '';
    accountSsoLink.disabled = true;
    try {
      await app.startSso('link');
    } catch (err) {
      accountSsoError.textContent = `⚠️ ${err.message}`;
      accountSsoLink.disabled = false;
    }
  });

  accountSsoUnlink.addEventListener('click', async () => {
    if (!confirm(t('Unlink single sign-on? You\'ll log in with your password from now on.'))) return;
    accountSsoError.textContent = '';
    try {
      app.updateMe(await api('/me/sso/unlink', { method: 'POST' }));
      renderAccount();
      accountSsoState.textContent = t('✅ Unlinked');
    } catch (err) {
      accountSsoError.textContent = `⚠️ ${err.message}`;
    }
  });
  closePasswordBtn.addEventListener('click', () => passwordModal.close());

  passwordForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    passwordError.textContent = '';
    passwordState.textContent = '';
    if (passwordNew.value !== passwordConfirm.value) {
      passwordError.textContent = t('⚠️ The two new passwords don\'t match');
      return;
    }
    passwordSubmit.disabled = true;
    try {
      await api('/me/password', {
        method: 'PUT',
        body: JSON.stringify({ current: passwordCurrent.value, new: passwordNew.value }),
      });
      passwordForm.reset();
      passwordState.textContent = app.me().user.has_password ? t('✅ Password changed') : t('✅ Password set');
      app.me().user.has_password = true;
      renderAccount();
      loadSessions(); // the other devices were just logged out
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
      const res = await api('/me/calendars', {
        method: 'POST',
        body: JSON.stringify({ url: feedUrl.value.trim(), label: feedLabel.value.trim() }),
      });
      feedUrl.value = '';
      feedLabel.value = '';
      await renderCalendarModal();
      calendarState.textContent = tn(res.busy_days, 'Linked ✓ — 1 busy evening spotted in the next couple of months.', 'Linked ✓ — {n} busy evenings spotted in the next couple of months.');
    } catch (err) {
      calendarError.textContent = `⚠️ ${err.message}`;
    } finally {
      feedAddBtn.disabled = false;
    }
  });

  return { openAccount, syncCalendarButton };
}
