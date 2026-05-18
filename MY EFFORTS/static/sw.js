const CACHE_NAME = 'career-pwa-v2';
const PRECACHE_URLS = [
  '/', 
  '/career-form',
  '/manifest.json',
  '/offline',
  '/static/css/chip_select.css',
  '/static/js/chip_select.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

// install: cache shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

// activate: clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.map(k => (k !== CACHE_NAME ? caches.delete(k) : null))
    ))
  );
  self.clients.claim();
});

// fetch: cache-first for assets, network-first for navigation with offline fallback
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  const requestURL = new URL(event.request.url);

  // navigation requests (pages)
  if (event.request.mode === 'navigate' || (requestURL.origin === location.origin && requestURL.pathname === '/')) {
    event.respondWith(
      fetch(event.request).then(resp => {
        // optionally update cache
        const copy = resp.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        return resp;
      }).catch(() => {
        // network failed -> return cached page or offline fallback
        return caches.match(event.request).then(match => match || caches.match('/offline'));
      })
    );
    return;
  }

  // for other requests (assets) use cache-first
  event.respondWith(
    caches.match(event.request).then(cached => {
      return cached || fetch(event.request).then(resp => {
        // cache asset for later
        const copy = resp.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        return resp;
      }).catch(() => {
        // if asset not cached and network failed, optionally return a fallback image or nothing
        return caches.match('/static/icons/icon-192.png');
      });
    })
  );
});
