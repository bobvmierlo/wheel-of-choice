/* Wheel of Choice — service worker.
 *
 * Exists for push notifications only: it deliberately has no fetch
 * handler and caches nothing, so the app always loads fresh from the
 * server (the admin "update & restart" flow stays trustworthy). Being
 * registered also makes the site installable, which is what unlocks
 * Web Push on iOS (16.4+, after Add to Home Screen).
 */
'use strict';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

// The server sends {title, body, tag, url, gcal_url?} as JSON. Always
// show a notification: platforms (iOS especially) revoke push permission
// from sites whose pushes stay silent.
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : '' };
  }
  const options = {
    body: data.body || '',
    tag: data.tag || 'wheel-of-choice', // one notification per wheel — new spins replace old ones
    icon: 'icons/icon-192.png',
    badge: 'icons/badge-96.png',
    data: { url: data.url || '/', gcal_url: data.gcal_url || null },
  };
  // A locked-in dinner date carries a Google Calendar template URL —
  // offer it as an action button. Platforms without action support
  // (iOS among them) simply show the plain notification.
  if (data.gcal_url) options.actions = [{ action: 'add-to-calendar', title: '📆 Add to calendar' }];
  event.waitUntil(self.registration.showNotification(data.title || '🎡 Wheel of Choice', options));
});

// Tapping the notification focuses the app if it's open, otherwise
// opens it on the wheel the notification came from (the /#wheel-id url).
// The 'add to calendar' action instead goes straight to Google Calendar
// with the dinner pre-filled.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  if (event.action === 'add-to-calendar' && data.gcal_url) {
    event.waitUntil(self.clients.openWindow(data.gcal_url));
    return;
  }
  const url = data.url || '/';
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    if (windows.length) return windows[0].focus();
    return self.clients.openWindow(url);
  })());
});

// Browsers rotate subscriptions now and then. The page can't help here
// (no login token in the worker), so the server offers a tokenless swap:
// it only works if you know the old endpoint — an unguessable URL.
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    const old = event.oldSubscription;
    let sub = event.newSubscription;
    if (!sub && old && old.options) {
      try { sub = await self.registration.pushManager.subscribe(old.options); } catch { return; }
    }
    if (!sub) return;
    await fetch('/api/push/resubscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        old_endpoint: old ? old.endpoint : null,
        subscription: sub.toJSON(),
      }),
    }).catch(() => {});
  })());
});
