/**
 * Self-destructing service worker.
 *
 * A pre-2026 deployment of cc.1pwrafrica.com (before the repo move from Email
 * Overlord) registered a service worker that kept serving a stale bundle —
 * affected browsers showed a months-old commissioning form (and other pages)
 * even after a hard refresh, because the orphaned SW intercepts every load.
 * The current app registers no SW; this file exists so browsers carrying the
 * orphaned worker fetch it on their next visit, unregister it, clear its
 * caches, and reload onto the live app.
 *
 * Same rescue pattern as the DR app (doc.1pwrafrica.com), 2026-08-20.
 * Do not remove: harmless to SW-free browsers (never fetched), and the only
 * rescue path for orphaned ones. If CC ever intentionally registers a SW,
 * replace this file.
 */
self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      try {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      } catch (_) {
        /* cache API may be unavailable; unregister regardless */
      }
      await self.registration.unregister();
      const clients = await self.clients.matchAll({ type: 'window' });
      for (const client of clients) {
        client.navigate(client.url);
      }
    })(),
  );
});
