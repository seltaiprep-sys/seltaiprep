/* SeltaPrep Service Worker ? Assets Only (Never caches HTML routes) */
const CACHE_VERSION = 'seltaprep-assets-v3';

self.addEventListener('install', event => {
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_VERSION).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;

    // NEVER intercept HTML navigations or dynamic routes
    if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html') || !url.pathname.startsWith('/static/')) {
        return;
    }

    // Cache only static assets (CSS, JS, icons, fonts)
    event.respondWith(
        fetch(req).then(res => {
            if (res && res.status === 200) {
                const copy = res.clone();
                caches.open(CACHE_VERSION).then(c => c.put(req, copy));
            }
            return res;
        }).catch(() => caches.match(req))
    );
});
