/* ═══════════════════════════════════════════════════════════
   SeltaPrep — Service Worker (PWA)
   Provides offline caching + fast loads
   ═══════════════════════════════════════════════════════════ */

const CACHE_VERSION = 'seltaprep-v1';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const PAGES_CACHE = `${CACHE_VERSION}-pages`;
const IMAGES_CACHE = `${CACHE_VERSION}-images`;

// Static assets to cache on install
const STATIC_ASSETS = [
    '/',
    '/static/css/mobile.css',
    '/static/css/style.css',
    '/static/manifest.json',
    '/static/icon-192.png',
    '/static/icon-512.png',
    '/static/favicon-32.png',
    '/static/apple-touch-icon.png',
];

// ─── Install: Cache static assets ───
self.addEventListener('install', event => {
    console.log('[SW] Installing...');
    event.waitUntil(
        caches.open(STATIC_CACHE)
            .then(cache => {
                console.log('[SW] Caching static assets');
                return cache.addAll(STATIC_ASSETS).catch(err => {
                    console.warn('[SW] Some assets failed:', err);
                });
            })
            .then(() => self.skipWaiting())
    );
});

// ─── Activate: Clean old caches ───
self.addEventListener('activate', event => {
    console.log('[SW] Activating...');
    event.waitUntil(
        caches.keys()
            .then(cacheNames => {
                return Promise.all(
                    cacheNames
                        .filter(name => !name.startsWith(CACHE_VERSION))
                        .map(name => {
                            console.log('[SW] Deleting old cache:', name);
                            return caches.delete(name);
                        })
                );
            })
            .then(() => self.clients.claim())
    );
});

// ─── Fetch: Strategy per request type ───
self.addEventListener('fetch', event => {
    const { request } = event;
    const url = new URL(request.url);

    // Skip non-GET requests
    if (request.method !== 'GET') return;

    // Skip API requests (always fresh)
    if (url.pathname.startsWith('/api/')) {
        return; // Let network handle it
    }

    // Skip admin routes (always fresh)
    if (url.pathname.startsWith('/admin/')) {
        return;
    }

    // Skip auth routes
    if (url.pathname.startsWith('/login') ||
        url.pathname.startsWith('/logout') ||
        url.pathname.startsWith('/register')) {
        return;
    }

    // ─── Static assets: Cache-First ───
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(request).then(cached => {
                if (cached) return cached;
                return fetch(request).then(response => {
                    if (!response || response.status !== 200) return response;
                    const responseClone = response.clone();
                    caches.open(STATIC_CACHE).then(cache => {
                        cache.put(request, responseClone);
                    });
                    return response;
                });
            })
        );
        return;
    }

    // ─── Images: Cache-First ───
    if (/\.(png|jpg|jpeg|gif|webp|svg|ico)$/i.test(url.pathname)) {
        event.respondWith(
            caches.match(request).then(cached => {
                if (cached) return cached;
                return fetch(request).then(response => {
                    if (!response || response.status !== 200) return response;
                    const responseClone = response.clone();
                    caches.open(IMAGES_CACHE).then(cache => {
                        cache.put(request, responseClone);
                    });
                    return response;
                }).catch(() => caches.match('/static/icon-192.png'));
            })
        );
        return;
    }

    // ─── HTML pages: Network-First with Cache Fallback ───
    event.respondWith(
        fetch(request)
            .then(response => {
                if (!response || response.status !== 200) return response;
                const responseClone = response.clone();
                caches.open(PAGES_CACHE).then(cache => {
                    cache.put(request, responseClone);
                });
                return response;
            })
            .catch(() => {
                return caches.match(request).then(cached => {
                    if (cached) return cached;
                    // Offline fallback
                    return caches.match('/').then(rootCache => {
                        if (rootCache) return rootCache;
                        return new Response(
                            '<!DOCTYPE html><html><head><meta charset="UTF-8">' +
                            '<title>Offline</title></head><body style="font-family:sans-serif;' +
                            'text-align:center;padding:50px;">' +
                            '<h1>📶 You are offline</h1>' +
                            '<p>Please check your internet connection.</p>' +
                            '<button onclick="location.reload()" style="padding:10px 20px;' +
                            'font-size:16px;background:#4f46e5;color:white;border:none;' +
                            'border-radius:6px;cursor:pointer;">Retry</button>' +
                            '</body></html>',
                            {
                                status: 200,
                                headers: { 'Content-Type': 'text/html' }
                            }
                        );
                    });
                });
            })
    );
});

// ─── Message handler ───
self.addEventListener('message', event => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    if (event.data && event.data.type === 'CLEAR_CACHE') {
        caches.keys().then(names => {
            return Promise.all(names.map(n => caches.delete(n)));
        }).then(() => {
            event.ports[0].postMessage({ success: true });
        });
    }
});

// ─── Push notifications (optional) ───
self.addEventListener('push', event => {
    if (!event.data) return;
    try {
        const data = event.data.json();
        const options = {
            body: data.body || 'New notification',
            icon: '/static/icon-192.png',
            badge: '/static/favicon-32.png',
            vibrate: [100, 50, 100],
            data: { url: data.url || '/' },
        };
        event.waitUntil(
            self.registration.showNotification(data.title || 'SeltaPrep', options)
        );
    } catch (e) {
        console.warn('[SW] Push error:', e);
    }
});

// ─── Notification click ───
self.addEventListener('notificationclick', event => {
    event.notification.close();
    const url = event.notification.data?.url || '/';
    event.waitUntil(
        clients.matchAll({ type: 'window' }).then(clientList => {
            for (const client of clientList) {
                if (client.url.includes(self.location.origin) &&
                    'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) return clients.openWindow(url);
        })
    );
});
