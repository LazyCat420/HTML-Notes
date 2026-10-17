// ═══════════════════════════════════════════════════════════
// HTML Notes PWA — Service Worker
// Caches the app shell; network-first for everything else.
// ═══════════════════════════════════════════════════════════

// DEV GUARD: Self-destruct on localhost to prevent stale cache issues
if (
    self.location.hostname === 'localhost' ||
    self.location.hostname === '127.0.0.1'
) {
    self.addEventListener('install', () => self.skipWaiting());
    self.addEventListener('activate', (event) => {
        event.waitUntil(self.registration.unregister());
    });
    // Block ALL fetch interception in dev mode
    self.addEventListener('fetch', () => {});
} else {
    const CACHE_NAME = 'html-notes-v1';
    const APP_SHELL = [
        '/',
        '/manifest.json',
    ];

    // ── Install: Pre-cache the app shell ──
    self.addEventListener('install', (event) => {
        event.waitUntil(
            caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
        );
        self.skipWaiting();
    });

    // ── Activate: Clean up old caches ──
    self.addEventListener('activate', (event) => {
        event.waitUntil(
            caches.keys().then((keys) =>
                Promise.all(
                    keys
                        .filter((key) => key !== CACHE_NAME)
                        .map((key) => caches.delete(key))
                )
            )
        );
        self.clients.claim();
    });

    // ── Fetch: network-first for everything; fall back to cache offline ──
    self.addEventListener('fetch', (event) => {
        if (event.request.method !== 'GET') return;
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    if (response && response.ok && new URL(event.request.url).origin === self.location.origin) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                    }
                    return response;
                })
                .catch(() =>
                    caches.match(event.request).then(
                        (cached) => cached || caches.match('/')
                    )
                )
        );
    });
}
