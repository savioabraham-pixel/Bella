/* Bella — the push service worker. Phase 2, 2026-11-10.
 *
 * IT CACHES NOTHING, AND THAT IS DELIBERATE.
 * There is no `fetch` handler in this file and there must never be one. A
 * caching service worker would serve a stale index.html, and Savio has already
 * lost time twice to a cached page that looked identical to a fresh one — the
 * reason the build marker exists at all. This worker has ONE job: to receive a
 * push while the tab is closed.
 *
 * Scope is /Bella/ because GitHub Pages serves the site from that path; a
 * worker cannot control pages above its own directory.
 *
 * index.html registers it ONLY when a Web Push key is configured. With no key
 * this file is never installed and the browser is never asked for permission.
 *
 * The config below is the SAME project as FIREBASE_CONFIG in index.html and
 * must stay byte-identical to it — messagingSenderId is what ties a push token
 * to the project, so a mismatch fails silently with tokens that never arrive.
 * A web apiKey is a project identifier, not a secret.
 */
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey: "AIzaSyB8Y_ZPYTN2P2YQXt3gHKRpG74IKH3O9DQ",
  authDomain: "gen-lang-client-0540368613.firebaseapp.com",
  projectId: "gen-lang-client-0540368613",
  storageBucket: "gen-lang-client-0540368613.firebasestorage.app",
  messagingSenderId: "299648732006",
  appId: "1:299648732006:web:02ee65362d92fc924c56a7"
});

const messaging = firebase.messaging();

/* A data-only push lands here. The notification is built HERE rather than by
 * the server so the wording stays in Bella's voice, and so a payload from
 * anywhere else cannot choose the title shown on his phone.
 * No icon is named: the repo has no PNG at a stable path (the manifest carries
 * its icons inline as data URIs), and a 404 icon is worse than the default. */
messaging.onBackgroundMessage(function (payload) {
  const d = (payload && payload.data) || {};
  const body = String(d.body || 'You asked me to remind you about something.').slice(0, 300);
  return self.registration.showNotification('Bella', {
    body: body,
    tag: String(d.tag || 'bella-reminder'),
    renotify: false,
    data: { url: d.url || './' }
  });
});

/* Tapping it FOCUSES an existing Bella tab rather than opening a second copy —
 * two copies of the app is a confusing result from tapping a reminder. */
self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || './';
  event.waitUntil((async function () {
    const all = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if (c.url.indexOf('/Bella/') !== -1) { await c.focus(); return; }
    }
    await clients.openWindow(target);
  })());
});
