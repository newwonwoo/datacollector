// Collector service worker — cache only the static shell (HTML/manifest).
// API calls (/api/*), status.json, and data_store/vault content always hit the
// network so the dashboard never serves stale pipeline state.
const CACHE = 'collector-shell-v1';
const SHELL = ['./', './index.html', './manifest.json'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Never cache API or live data.
  if (url.pathname.startsWith('/api/') ||
      url.pathname.endsWith('/status.json') ||
      url.pathname.startsWith('/vault/') ||
      url.pathname.startsWith('/data_store/') ||
      url.pathname.startsWith('/index/')) {
    return;  // default network handling
  }
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then((hit) => {
      if (hit) return hit;
      return fetch(e.request).then((resp) => {
        // Only cache successful same-origin GETs of shell assets.
        if (resp.ok && url.origin === self.location.origin) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return resp;
      }).catch(() => hit);
    })
  );
});
