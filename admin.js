/* Wheel of Choice — the admin panel (🛠️ Admin) and the version/update
 * line in the footer.
 *
 * Split out of app.js: everything here talks to the server through the
 * `app` handle passed to initAdmin(), so the rest of the app's state stays
 * in app.js. Returns the two functions app.js needs: loadVersion (footer +
 * panel version line) and checkForUpdate (the 🆕 dot on the Admin button).
 */
import { t, tn } from './i18n.js';
import { prettyDate, formatDateTime } from './utils.js';
import { actionBtn } from './ui.js';

/**
 * @param {object} app
 * @param {Function} app.api            the app's JSON fetch helper (rootApi)
 * @param {Function} app.me             the current /me payload (or null)
 * @param {Function} app.token          the session token
 * @param {Function} app.refreshMe      re-fetch /me and re-render the app
 * @param {Function} app.forceLogout    back to the login screen
 * @param {Function} app.copyToClipboard
 * @param {Function} app.setPollWindow  (horizonDays, eveningFrom) after a settings change
 */
export function initAdmin(app) {
  const api = app.api;
  const adminBtn = document.getElementById('admin-btn');
  const adminModal = document.getElementById('admin-modal');
  const closeAdminBtn = document.getElementById('close-admin-btn');
  const adminUserList = document.getElementById('admin-user-list');
  const adminError = document.getElementById('admin-error');
  const adminUpdateBtn = document.getElementById('admin-update-btn');
  const adminVersion = document.getElementById('admin-version');
  const adminUpdateAvailable = document.getElementById('admin-update-available');
  const adminUpdateHint = document.getElementById('admin-update-hint');
  const adminUpdateCmd = document.getElementById('admin-update-cmd');
  const adminUpdateDot = document.getElementById('admin-update-dot');
  const updateStatus = document.getElementById('update-status');
  const footerVersion = document.getElementById('footer-version');

  // ── Admin modal ───────────────────────────────────────────────────
  adminBtn.addEventListener('click', async () => {
    adminError.textContent = '';
    adminUserList.innerHTML = '';
    adminModal.showModal();
    loadVersion(); // another admin may have updated in the meantime
    loadAdminSettings(); // another admin may have retuned the polls
    loadSsoSettings();
    loadAutoBackups();
    registrationStatus.textContent = '';
    loadInvites(); // list any pending invite links
    startUpdatePolling(); // watch git for a newer commit while the panel's open
    try {
      renderAdminUsers(await api('/admin/users'));
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
  const settingMapsKey = document.getElementById('setting-maps-key');
  const mapsKeySaveBtn = document.getElementById('maps-key-save-btn');
  const mapsKeyStatus = document.getElementById('maps-key-status');
  const settingRegistrationOpen = document.getElementById('setting-registration-open');
  const registrationOpenLabel = document.getElementById('registration-open-label');
  let tzListLoaded = false;

  function fillSettings(s) {
    settingTimezone.value = s.timezone;
    settingEveningFrom.value = s.evening_from;
    settingHorizon.value = s.poll_horizon_days;
    settingRegistrationOpen.checked = !!s.registration_open;
    registrationOpenLabel.textContent = s.registration_open ? t('open to everyone') : t('invite-only');
    // the raw Maps key only rides on admin settings responses
    if ('google_maps_api_key' in s) settingMapsKey.value = s.google_maps_api_key || '';
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
    app.setPollWindow(s.poll_horizon_days, s.evening_from);
  }

  async function loadAdminSettings() {
    settingsStatus.textContent = '';
    try {
      fillSettings(await api('/admin/settings'));
    } catch (err) {
      settingsStatus.textContent = `⚠️ ${err.message}`;
    }
  }

  settingsSaveBtn.addEventListener('click', async () => {
    settingsStatus.textContent = t('⏳ Saving…');
    settingsSaveBtn.disabled = true;
    try {
      const s = await api('/admin/settings', {
        method: 'PUT',
        body: JSON.stringify({
          timezone: settingTimezone.value.trim(),
          evening_from: Number(settingEveningFrom.value),
          poll_horizon_days: Number(settingHorizon.value),
        }),
      });
      fillSettings(s);
      settingsStatus.textContent = t('✅ Saved — polls use these straight away.');
    } catch (err) {
      settingsStatus.textContent = `⚠️ ${err.message}`;
    } finally {
      settingsSaveBtn.disabled = false;
    }
  });

  mapsKeySaveBtn.addEventListener('click', async () => {
    mapsKeyStatus.textContent = t('⏳ Saving…');
    mapsKeySaveBtn.disabled = true;
    const key = settingMapsKey.value.trim();
    try {
      fillSettings(await api('/admin/settings', {
        method: 'PUT',
        body: JSON.stringify({ google_maps_api_key: key }),
      }));
      mapsKeyStatus.textContent = key
        ? t('✅ Saved — members can search for restaurant addresses.')
        : t('✅ Cleared — location search is off; members type addresses by hand.');
      if (app.me()) app.me().maps_enabled = !!key;  // reflect in the add form without a reload
    } catch (err) {
      mapsKeyStatus.textContent = `⚠️ ${err.message}`;
    } finally {
      mapsKeySaveBtn.disabled = false;
    }
  });

  // ── Single sign-on settings ───────────────────────────────────────
  const ssoIssuer = document.getElementById('sso-issuer');
  const ssoClientId = document.getElementById('sso-client-id');
  const ssoClientSecret = document.getElementById('sso-client-secret');
  const ssoSecretHint = document.getElementById('sso-secret-hint');
  const ssoPublicUrl = document.getElementById('sso-public-url');
  const ssoButtonLabel = document.getElementById('sso-button-label');
  const ssoMatchEmail = document.getElementById('sso-match-email');
  const ssoRequireVerified = document.getElementById('sso-require-verified');
  const ssoAllowSignup = document.getElementById('sso-allow-signup');
  const ssoRedirectUri = document.getElementById('sso-redirect-uri');
  const ssoDisablePasswords = document.getElementById('sso-disable-passwords');
  const ssoDisablePasswordsHint = document.getElementById('sso-disable-passwords-hint');
  const SSO_DISABLE_HINT = ssoDisablePasswordsHint.innerHTML;
  const ssoSaveBtn = document.getElementById('sso-save-btn');
  const ssoTestBtn = document.getElementById('sso-test-btn');
  const ssoStatus = document.getElementById('sso-status');

  function fillSso(v) {
    ssoIssuer.value = v.issuer || '';
    ssoClientId.value = v.client_id || '';
    ssoClientSecret.value = '';
    ssoClientSecret.placeholder = v.client_secret_set ? t('•••••• (stored)') : t('Paste the client secret');
    ssoSecretHint.textContent = v.client_secret_set
      ? t('A secret is stored. Leave empty to keep it, or paste a new one to replace it.')
      : t('Stored on the server, never shown again.');
    ssoPublicUrl.value = v.public_url || '';
    ssoButtonLabel.value = v.button_label || '';
    ssoMatchEmail.checked = !!v.match_email;
    ssoRequireVerified.checked = !!v.require_verified_email;
    ssoAllowSignup.checked = !!v.allow_signup;
    ssoRedirectUri.textContent = v.redirect_uri || '—';
    ssoDisablePasswords.checked = !!v.disable_password_login;
    ssoDisablePasswordsHint.innerHTML = SSO_DISABLE_HINT;
    const extra = [];
    if (v.password_login_forced) {
      extra.push(t('⚠️ WHEEL_FORCE_PASSWORD_LOGIN is set on the server, so passwords work anyway right now.'));
    } else if (v.disable_password_login && !v.enabled) {
      extra.push(t('⚠️ Sign-on isn\'t fully configured, so passwords stay on until it is.'));
    }
    if (v.unlinked_users && v.unlinked_users.length) {
      const names = v.unlinked_users.join(', ');
      extra.push(v.disable_password_login
        ? t('Not linked to sign-on yet: {names} — they can only get in through sign-on now (matched by e-mail).', { names })
        : t('Not linked to sign-on yet: {names}.', { names }));
    }
    for (const line of extra) {
      const span = document.createElement('span');
      span.className = 'sso-extra';
      span.textContent = line;
      ssoDisablePasswordsHint.append(document.createElement('br'), span);
    }
    if (app.me()) app.me().sso = { enabled: !!v.enabled, label: v.button_label || t('Log in with SSO'), password_login: !!v.password_login };
    return v;
  }

  async function loadSsoSettings() {
    ssoStatus.textContent = '';
    try {
      fillSso(await api('/admin/sso'));
    } catch (err) {
      ssoStatus.textContent = `⚠️ ${err.message}`;
    }
  }

  ssoSaveBtn.addEventListener('click', async () => {
    ssoStatus.textContent = t('⏳ Saving…');
    ssoSaveBtn.disabled = true;
    const body = {
      issuer: ssoIssuer.value.trim(),
      client_id: ssoClientId.value.trim(),
      public_url: ssoPublicUrl.value.trim(),
      button_label: ssoButtonLabel.value.trim(),
      match_email: ssoMatchEmail.checked,
      require_verified_email: ssoRequireVerified.checked,
      allow_signup: ssoAllowSignup.checked,
      disable_password_login: ssoDisablePasswords.checked,
    };
    if (ssoClientSecret.value.trim()) body.client_secret = ssoClientSecret.value.trim();
    try {
      const v = fillSso(await api('/admin/sso', { method: 'PUT', body: JSON.stringify(body) }));
      ssoStatus.textContent = v.enabled
        ? t('✅ Saved — the sign-on button is on the login screen.')
        : t('✅ Saved — fill in issuer, client ID and secret to switch sign-on on.');
      if (v.warning) ssoStatus.textContent += t(` ⚠️ But {warning}.`, { warning: v.warning });
      if (app.me() && app.me().sso) renderAdminUsers(await api('/admin/users')); // reset-password buttons follow the switch
    } catch (err) {
      ssoStatus.textContent = `⚠️ ${err.message}`;
    } finally {
      ssoSaveBtn.disabled = false;
    }
  });

  ssoTestBtn.addEventListener('click', async () => {
    ssoStatus.textContent = t('⏳ Contacting the identity provider…');
    try {
      const r = await api('/admin/sso/test', { method: 'POST' });
      ssoStatus.textContent = t(`✅ Reached {issuer} (save first if you changed the issuer).`, { issuer: r.issuer });
    } catch (err) {
      ssoStatus.textContent = `⚠️ ${err.message}`;
    }
  });

  // ── Registration gate & account invites ───────────────────────────
  const inviteList = document.getElementById('invite-list');
  const createInviteBtn = document.getElementById('create-invite-btn');
  const registrationStatus = document.getElementById('registration-status');

  function accountInviteLink(code) {
    return `${location.origin}${location.pathname}?invite=${encodeURIComponent(code)}`;
  }

  settingRegistrationOpen.addEventListener('change', async () => {
    const open = settingRegistrationOpen.checked;
    registrationStatus.textContent = t('⏳ Saving…');
    try {
      fillSettings(await api('/admin/settings', {
        method: 'PUT',
        body: JSON.stringify({ registration_open: open }),
      }));
      registrationStatus.textContent = open
        ? t('✅ Registration is open — anyone can create an account.')
        : t('✅ Registration is invite-only — hand out invite links below.');
    } catch (err) {
      settingRegistrationOpen.checked = !open; // roll the switch back on failure
      registrationStatus.textContent = `⚠️ ${err.message}`;
    }
  });

  function renderInvites(invites) {
    inviteList.innerHTML = '';
    if (!invites.length) {
      const li = document.createElement('li');
      li.className = 'dest-meta';
      li.textContent = t('No invite links yet — create one to invite someone.');
      inviteList.append(li);
      return;
    }
    for (const inv of invites) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'dest-name';
      name.textContent = `🎟️ ${inv.code}`;
      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      meta.textContent = inv.by ? t(`made by {by}`, { by: inv.by }) : '';
      li.append(name, meta);

      li.append(actionBtn(t('🔗 Copy link'), t('Copy this invite link to share'), async (e) => {
        const ok = await app.copyToClipboard(accountInviteLink(inv.code));
        e.target.textContent = ok ? t('✅ Copied') : t('⚠️ Copy failed');
        setTimeout(() => { e.target.textContent = t('🔗 Copy link'); }, 1500);
      }));
      li.append(actionBtn(t('✕ Revoke'), t('Delete this invite so it can no longer be used'), async () => {
        registrationStatus.textContent = '';
        try {
          renderInvites(await api(`/admin/invites/${encodeURIComponent(inv.code)}`, { method: 'DELETE' }));
        } catch (err) {
          registrationStatus.textContent = `⚠️ ${err.message}`;
        }
      }));
      inviteList.append(li);
    }
  }

  async function loadInvites() {
    try {
      renderInvites(await api('/admin/invites'));
    } catch (err) {
      registrationStatus.textContent = `⚠️ ${err.message}`;
    }
  }

  createInviteBtn.addEventListener('click', async () => {
    registrationStatus.textContent = '';
    createInviteBtn.disabled = true;
    try {
      const res = await api('/admin/invites', { method: 'POST' });
      renderInvites(res.invites);
      const link = accountInviteLink(res.code);
      const copied = await app.copyToClipboard(link);
      registrationStatus.textContent = copied
        ? t(`✅ Invite link copied — send it to whoever you're inviting.`)
        : t(`✅ Invite {code} created — copy its link below.`, { code: res.code });
    } catch (err) {
      registrationStatus.textContent = `⚠️ ${err.message}`;
    } finally {
      createInviteBtn.disabled = false;
    }
  });

  // ── Backup & restore ──────────────────────────────────────────────
  const adminBackupBtn = document.getElementById('admin-backup-btn');
  const adminRestoreBtn = document.getElementById('admin-restore-btn');
  const adminRestoreFile = document.getElementById('admin-restore-file');
  const backupStatus = document.getElementById('backup-status');

  // fetch by hand: a plain download link can't carry the auth header
  async function downloadBackup(url, filename, statusEl) {
    statusEl.textContent = '';
    try {
      const res = await fetch(url, { headers: { Authorization: `Bearer ${app.token()}` } });
      if (!res.ok) throw new Error(t(`Server said {status}`, { status: res.status }));
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
      statusEl.textContent = t('✅ Backup downloaded — tuck it away somewhere safe.');
    } catch (err) {
      statusEl.textContent = `⚠️ ${err.message}`;
    }
  }

  adminBackupBtn.addEventListener('click', () => downloadBackup('/api/admin/backup',
    `wheel-of-choice-backup-${new Date().toISOString().slice(0, 10)}.json`, backupStatus));

  adminRestoreBtn.addEventListener('click', () => {
    adminRestoreFile.value = '';
    adminRestoreFile.click();
  });

  adminRestoreFile.addEventListener('change', async () => {
    const file = adminRestoreFile.files[0];
    if (!file) return;
    if (!confirm(t(`Restore "{name}"? This replaces ALL current data — accounts, wheels, history and settings — with the backup's contents.`, { name: file.name }))) return;
    backupStatus.textContent = t('⏳ Restoring…');
    try {
      const text = await file.text();
      JSON.parse(text); // catch garbage before it travels
      const res = await api('/admin/restore', { method: 'POST', body: text });
      if (res.relogin) {
        alert(t('Backup restored. Your account isn\'t in this backup, so please log in again.'));
        app.forceLogout();
        return;
      }
      backupStatus.textContent = res.files.includes('vapid-private-key.pem')
        ? t('✅ Backup restored, push key included.')
        : t('✅ Backup restored. (Older backup without a push key — the current one was kept.)');
      await app.refreshMe();
      renderAdminUsers(await api('/admin/users'));
    } catch (err) {
      backupStatus.textContent = `⚠️ ${err.message}`;
    }
  });

  // ── Automatic daily backups ───────────────────────────────────────
  const autoBackupKeep = document.getElementById('auto-backup-keep');
  const autoBackupSaveBtn = document.getElementById('auto-backup-save-btn');
  const autoBackupList = document.getElementById('auto-backup-list');
  const autoBackupStatus = document.getElementById('auto-backup-status');

  function renderAutoBackups(v) {
    autoBackupKeep.value = v.keep;
    autoBackupKeep.max = v.max_keep;
    autoBackupList.innerHTML = '';
    if (!v.backups.length) {
      const li = document.createElement('li');
      li.className = 'dest-meta';
      li.textContent = v.keep ? t('No automatic backups yet — the first one is made within the hour.')
        : t('Automatic backups are off.');
      autoBackupList.append(li);
      return;
    }
    for (const b of v.backups) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'dest-name';
      name.textContent = `🗄️ ${prettyDate(b.date)}`;
      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      meta.textContent = `${Math.max(1, Math.round(b.size / 1024))} KB`;
      li.append(name, meta);
      li.append(actionBtn(t('💾 Download'), t('Download this backup'), () => downloadBackup(
        `/api/admin/backups/${encodeURIComponent(b.name)}`, b.name, autoBackupStatus)));
      li.append(actionBtn(t('↩️ Restore'), t('Roll the whole server back to this backup'), async () => {
        if (!confirm(t(`Roll the whole server back to {date}? Everything since then — accounts, wheels, history, settings — is replaced.`, { date: prettyDate(b.date) }))) return;
        autoBackupStatus.textContent = t('⏳ Restoring…');
        try {
          const res = await api(`/admin/backups/${encodeURIComponent(b.name)}/restore`, { method: 'POST' });
          if (res.relogin) {
            alert(t('Backup restored. Your account isn\'t in this backup, so please log in again.'));
            app.forceLogout();
            return;
          }
          autoBackupStatus.textContent = t('✅ Restored.');
          await app.refreshMe();
          renderAdminUsers(await api('/admin/users'));
        } catch (err) {
          autoBackupStatus.textContent = `⚠️ ${err.message}`;
        }
      }));
      autoBackupList.append(li);
    }
  }

  async function loadAutoBackups() {
    autoBackupStatus.textContent = '';
    try {
      renderAutoBackups(await api('/admin/backups'));
    } catch (err) {
      autoBackupStatus.textContent = `⚠️ ${err.message}`;
    }
  }

  autoBackupSaveBtn.addEventListener('click', async () => {
    autoBackupStatus.textContent = t('⏳ Saving…');
    try {
      renderAutoBackups(await api('/admin/backups', {
        method: 'PUT', body: JSON.stringify({ keep: Number(autoBackupKeep.value) }),
      }));
      autoBackupStatus.textContent = t('✅ Saved.');
    } catch (err) {
      autoBackupStatus.textContent = `⚠️ ${err.message}`;
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


  function renderAdminUsers(users) {
    adminUserList.innerHTML = '';
    for (const u of users) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'dest-name';
      name.textContent = `${u.admin ? '🛡️' : '👤'} ${u.id === app.me().user.id ? t('{name} (you)', { name: u.name }) : u.name}`;
      const meta = document.createElement('span');
      meta.className = 'dest-meta';
      const wheels = tn(u.wheel_count, '1 wheel', '{n} wheels');
      const bits = [u.email ? `✉️ ${u.email}` : t('✉️ no e-mail'), wheels];
      if (u.sso_linked) bits.push(t('🔗 SSO'));
      if (!u.has_password) bits.push(t('no password'));
      if (u.sharing_with.length) bits.push(t('shares with {names}', { names: u.sharing_with.join(', ') }));
      meta.textContent = bits.join(' · ');
      li.append(name, meta);

      // E-mail first: it's what matches an account to its sign-on identity
      li.append(actionBtn(t('✉️ E-mail'), t('Set or change this account\'s e-mail address'), () => {
        const email = prompt(t('E-mail address for {name} (leave empty to remove it).\nThey can log in with it, and it links their account the first time they use single sign-on.', { name: u.name }),
        u.email || '');
        if (email === null) return;
        adminAction(api(`/admin/users/${u.id}/email`, {
          method: 'PUT',
          body: JSON.stringify({ email: email.trim() }),
        }));
      }));
      if (u.sso_linked) {
        li.append(actionBtn(t('Unlink SSO'), t('Detach this account from its sign-on identity'), () => {
          if (!confirm(t(`Unlink {name} from single sign-on? Their next sign-on matches by e-mail again.`, { name: u.name }))) return;
          adminAction(api(`/admin/users/${u.id}/unlink-sso`, { method: 'POST' }));
        }));
      }

      if (u.id !== app.me().user.id) {
        li.append(actionBtn(
          u.admin ? t('Remove admin') : t('Make admin'),
          u.admin ? t('Take away admin rights') : t('Give admin rights'),
          () => adminAction(api(`/admin/users/${u.id}`, {
            method: 'PUT',
            body: JSON.stringify({ admin: !u.admin }),
          }))
        ));
        if (!app.me().sso || app.me().sso.password_login !== false) li.append(actionBtn(t('🔑 Reset password'),
          t('Set a temporary password and log them out everywhere'), async () => {
            const pw = prompt(t("Set a temporary password for {name} (at least 8 characters).\nThey'll be logged out on every device and come back in with it — then they can change it themselves.", { name: u.name }));
            if (pw === null) return; // cancelled
            if (pw.length < 8) {
              adminError.textContent = t('⚠️ That password is too short — at least 8 characters.');
              return;
            }
            adminError.textContent = '';
            try {
              await api(`/admin/users/${u.id}/password`, {
                method: 'PUT',
                body: JSON.stringify({ new: pw }),
              });
              adminError.textContent = t(`✅ {name}'s password was reset — pass the new one along.`, { name: u.name });
            } catch (err) {
              adminError.textContent = `⚠️ ${err.message}`;
            }
          }));
        if (u.sharing_with.length) {
          li.append(actionBtn(t('Unshare'), t('Pull them out of every wheel they share'), () => {
            if (!confirm(t('Pull {name} out of every wheel they share with {names}? They keep their account and any wheels of their own.', { name: u.name, names: u.sharing_with.join(', ') }))) return;
            adminAction(api(`/admin/users/${u.id}/unshare`, { method: 'POST' }));
          }));
        }
        li.append(actionBtn(t('✕ Delete'), t('Delete this account for good'), () => {
          if (!confirm(t(`Delete {name}'s account for good? Wheels they share with others stay with the others.`, { name: u.name }))) return;
          adminAction(api(`/admin/users/${u.id}`, { method: 'DELETE' }));
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
    if (!res.ok) throw new Error(t(`Server said {status}`, { status: res.status }));
    return res.json();
  }

  function renderVersion() {
    const v = serverVersion;
    if (!v) {
      footerVersion.textContent = '';
      adminVersion.textContent = '';
      return;
    }
    const ver = v.version && v.version !== 'unknown' ? `v${v.version}` : '';
    const when = v.commit_date ? formatDateTime(v.commit_date) : '';

    // Footer: prefer the release number; fall back to the commit hash so a
    // git checkout without a version file still shows something.
    const footerBits = [];
    if (ver) footerBits.push(t('running {ver}', { ver }));
    else if (v.commit) footerBits.push(t('running {ver}', { ver: v.commit }));
    if (when) footerBits.push(t(`last updated {when}`, { when }));
    footerVersion.textContent = footerBits.join(' · ');

    // Admin panel: the fuller picture — commit detail only matters for a
    // source checkout; a container is fully described by its version.
    let line = ver ? t('Currently running {ver}', { ver }) : t('Currently running');
    if (v.deployment === 'docker') {
      line += ` ${t('(container)')}.`;
    } else {
      if (v.commit) {
        line += ` · ${t('commit {commit}', { commit: v.commit })}`;
        if (v.commit_subject) line += ` (“${v.commit_subject}”)`;
      }
      if (when) line += ` · ${t('last updated {when}', { when })}`;
      line += '.';
    }
    adminVersion.textContent = line;

    applyDeployment(v);
  }

  function applyDeployment(v) {
    const docker = !!(v && v.deployment === 'docker');
    // The git "Update & restart" button only makes sense for a source/systemd
    // checkout; a container updates by pulling a newer image instead.
    adminUpdateBtn.hidden = docker;
    adminUpdateCmd.hidden = !docker;
    adminUpdateHint.textContent = docker
      ? t('🐳 Container deployment — to update, pull the newer image and recreate the container:')
      : t('Fetch the latest version from git and restart the server. Needs the updater units from deploy/ installed.');
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
    updateStatus.textContent = t('⏳ Update requested — waiting for the server to pull the latest version and restart…');
    updateTimer = setInterval(async () => {
      let v;
      try {
        v = await fetchVersion();
      } catch {
        // unreachable mid-restart — that's expected, keep polling
        updateStatus.textContent = t('🔄 Server is restarting…');
        return;
      }
      if (!baseline || v.server_started !== baseline.server_started) {
        clearInterval(updateTimer);
        adminUpdateBtn.disabled = false;
        serverVersion = v;
        renderVersion();
        updateStatus.textContent = !baseline || v.commit !== baseline.commit
          ? t('✅ Updated! Now running {commit}.', { commit: v.commit + (v.commit_subject ? ` (“${v.commit_subject}”)` : '') })
          : t('✅ Server restarted — it was already on the latest version.');
        checkForUpdate(true); // refresh the badge now we've moved
        return;
      }
      if (Date.now() - startedAt > UPDATE_TIMEOUT_MS) {
        clearInterval(updateTimer);
        adminUpdateBtn.disabled = false;
        updateStatus.textContent = v.update_pending
          ? t('⚠️ Nothing picked up the update request — are the updater units from deploy/ installed and enabled?')
          : t('⚠️ The update request was picked up, but the server never restarted — the git pull may have failed. Check `journalctl -u wheel-of-choice-update` on the server.');
      }
    }, UPDATE_POLL_MS);
  }

  adminUpdateBtn.addEventListener('click', async () => {
    if (!confirm(t('Pull the latest version from git and restart the server? Everyone is briefly disconnected.'))) return;
    adminError.textContent = '';
    updateStatus.textContent = '';
    adminUpdateBtn.disabled = true;
    let baseline = serverVersion;
    try {
      baseline = await fetchVersion(); // fresh baseline to compare against
    } catch { /* keep the last known one */ }
    try {
      await api('/admin/update', { method: 'POST' });
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
    const docker = info.deployment === 'docker';
    adminUpdateAvailable.hidden = false;
    if (available) {
      adminUpdateAvailable.textContent = docker
        ? t(`🆕 Version {latest} is available — you're running {current}. Pull the new image to update (see below).`, { latest: info.latest, current: info.current })
        : t(`🆕 A newer version is on git ({latest} on {branch}) — you're running {current}. Hit “Update & restart” to pull it.`, { latest: info.latest, branch: info.branch, current: info.current });
    } else {
      adminUpdateAvailable.textContent = docker
        ? t('✅ Running the latest release.')
        : t('✅ Up to date with the git remote.');
    }
  }

  async function checkForUpdate(force = false) {
    try {
      const info = await api(`/admin/update-check${force ? '?force=1' : ''}`);
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

  return { loadVersion, checkForUpdate };
}
