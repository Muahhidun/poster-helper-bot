const CACHE_NAME = 'pizzburg-pwa-v1';
const ASSETS = [
    '/',
    '/static/manifest.json',
    '/static/icon-192.png',
    '/static/icon-512.png'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(ASSETS))
            .catch(err => console.warn('PWA Cache install skipped or failed', err))
    );
});

self.addEventListener('fetch', (event) => {
    // Network first, fallback to cache for PWA offline capability (basic)
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
