/**
 * TopMark Service Worker
 * Handles caching, offline fallback, and background sync
 *
 * v2 — Fixed offline page caching:
 *   - Separated STATIC_ASSETS (files) from PAGE_ASSETS (HTML pages)
 *   - PAGE_ASSETS now pre-cached into CACHE_PAGES at install (not CACHE_STATIC)
 *   - Auth-required pages (/home, /cbt/*, etc.) are NOT pre-cached at install
 *     because Django redirects unauthenticated requests — they get cached at
 *     runtime the first time the logged-in user visits them online
 *   - networkFirstWithOfflineFallback now uses global caches.match() so it
 *     finds pages across ALL caches (CACHE_PAGES + CACHE_STATIC)
 */

const APP_VERSION   = 'v2';
const CACHE_STATIC  = `topmark-static-${APP_VERSION}`;
const CACHE_PAGES   = `topmark-pages-${APP_VERSION}`;
const CACHE_IMAGES  = `topmark-images-${APP_VERSION}`;
const ALL_CACHES    = [CACHE_STATIC, CACHE_PAGES, CACHE_IMAGES];

// ─── Static file assets (pre-cached into CACHE_STATIC on install) ────────────
// Only include files that are always publicly accessible — no auth needed.
const STATIC_ASSETS = [
  '/static/css/dashboard.css',
  '/static/images/slide1.png',
  '/static/images/slide2.png',
  '/static/images/slide3.png',
  '/static/images/slide4.png',
  // FontAwesome and Google Fonts — cached at runtime via CDN_HOSTS rule
];

// ─── Public pages (pre-cached into CACHE_PAGES on install) ───────────────────
// Only include pages Django will serve WITHOUT login (no 302 redirect).
// Auth-required pages like /home, /cbt/* are cached at RUNTIME the first time
// the logged-in user visits them — see networkFirstWithOfflineFallback().
const PAGE_ASSETS = [
  '/',          // landing / index — public
  '/register',  // registration — public
];

// ─── CDN hosts we cache at runtime ───────────────────────────────────────────
const CDN_HOSTS = [
  'fonts.googleapis.com',
  'fonts.gstatic.com',
  'cdnjs.cloudflare.com',
];

// ─── Routes that should NEVER be served from cache ───────────────────────────
const NETWORK_ONLY = [
  '/logout',
  '/post',
  '/cbt/submit/',
  '/chat/create/',
  '/chat/post/',        // comment / like sub-paths
  '/live/',
  '/follow/',
  '/like/',
  '/comment_like/',
  '/postcomment/',
];

// ─── Offline fallback page (inline HTML) ─────────────────────────────────────
const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>TopMark — You're Offline</title>
  <style>
    :root{--ac-primary:#0095f6;--ac-text:#262626;--ac-light:#e8f4fd}
    *{margin:0;padding:0;box-sizing:border-box}
    body{font-family:'Plus Jakarta Sans',-apple-system,sans-serif;
         background:var(--ac-light);color:var(--ac-text);
         display:flex;flex-direction:column;align-items:center;
         justify-content:center;min-height:100vh;text-align:center;padding:24px}
    .logo{width:64px;height:64px;background:var(--ac-primary);border-radius:16px;
          display:flex;align-items:center;justify-content:center;
          font-size:28px;font-weight:800;color:#fff;margin:0 auto 24px}
    h1{font-size:24px;font-weight:800;margin-bottom:10px}
    p{font-size:15px;color:#737373;line-height:1.65;max-width:320px;margin:0 auto 28px}
    button{background:var(--ac-primary);color:#fff;border:none;
           padding:14px 32px;border-radius:12px;font-size:15px;
           font-weight:700;cursor:pointer}
    button:active{opacity:.85}
  </style>
</head>
<body>
  <div class="logo">T</div>
  <h1>You're offline</h1>
  <p>Check your internet connection and try again. Cached pages and questions are still available.</p>
  <button onclick="location.reload()">Try again</button>
</body>
</html>`;

// ═════════════════════════════════════════════════════════════════════════════
// INSTALL — pre-cache shell assets and public pages into their correct caches
// ═════════════════════════════════════════════════════════════════════════════
self.addEventListener('install', (event) => {
  event.waitUntil(
    Promise.all([
      // Static files → CACHE_STATIC
      caches.open(CACHE_STATIC).then(async (cache) => {
        const results = await Promise.allSettled(
          STATIC_ASSETS.map((url) => cache.add(url).catch(() => null))
        );
        results.forEach((r, i) => {
          if (r.status === 'rejected') {
            console.warn('[SW] Failed to pre-cache static asset:', STATIC_ASSETS[i]);
          }
        });
      }),

      // Public pages → CACHE_PAGES
      caches.open(CACHE_PAGES).then(async (cache) => {
        const results = await Promise.allSettled(
          PAGE_ASSETS.map((url) => cache.add(url).catch(() => null))
        );
        results.forEach((r, i) => {
          if (r.status === 'rejected') {
            console.warn('[SW] Failed to pre-cache page:', PAGE_ASSETS[i]);
          }
        });
      }),
    ])
  );
  self.skipWaiting();
});

// ═════════════════════════════════════════════════════════════════════════════
// ACTIVATE — clean up old caches and take control immediately
// ═════════════════════════════════════════════════════════════════════════════
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !ALL_CACHES.includes(k))
          .map((k) => {
            console.log('[SW] Deleting old cache:', k);
            return caches.delete(k);
          })
      )
    )
  );
  self.clients.claim();
});

// ═════════════════════════════════════════════════════════════════════════════
// FETCH — routing logic
// ═════════════════════════════════════════════════════════════════════════════
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Ignore non-GET, chrome-extension, and non-http(s) requests
  if (request.method !== 'GET') return;
  if (!['http:', 'https:'].includes(url.protocol)) return;

  // ── 1. Network-only routes (mutations, auth, live) ───────────────────────
  if (NETWORK_ONLY.some((path) => url.pathname.startsWith(path))) {
    event.respondWith(networkOnly(request));
    return;
  }

  // ── 2. CDN assets (fonts, icons) – Cache-first ───────────────────────────
  if (CDN_HOSTS.includes(url.hostname)) {
    event.respondWith(cacheFirst(request, CACHE_STATIC));
    return;
  }

  // ── 3. Local static files – Cache-first ──────────────────────────────────
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(request, CACHE_STATIC));
    return;
  }

  // ── 4. Images (any origin) – Cache-first ─────────────────────────────────
  if (request.destination === 'image') {
    event.respondWith(cacheFirst(request, CACHE_IMAGES));
    return;
  }

  // ── 5. HTML navigation requests – Network-first with offline fallback ─────
  if (request.mode === 'navigate' || request.headers.get('Accept')?.includes('text/html')) {
    event.respondWith(networkFirstWithOfflineFallback(request));
    return;
  }

  // ── 6. Everything else – Stale-while-revalidate ──────────────────────────
  event.respondWith(staleWhileRevalidate(request, CACHE_STATIC));
});

// ═════════════════════════════════════════════════════════════════════════════
// CACHING STRATEGIES
// ═════════════════════════════════════════════════════════════════════════════

/** Always go to network; no cache fallback */
async function networkOnly(request) {
  try {
    return await fetch(request);
  } catch {
    return new Response('Offline — this action requires a network connection.', {
      status: 503,
      headers: { 'Content-Type': 'text/plain' },
    });
  }
}

/** Cache first → network fallback → empty 408 on failure */
async function cacheFirst(request, cacheName) {
  // Global caches.match searches all caches — faster than opening a specific one
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('', { status: 408, statusText: 'Offline' });
  }
}

/**
 * Network first → cache fallback → inline offline page
 *
 * KEY FIX: Uses global caches.match() (no specific cache arg) so it finds
 * pages stored in CACHE_PAGES *or* CACHE_STATIC — whichever has them.
 * Auth-required pages (/home, /cbt/*, etc.) are saved here at runtime the
 * first time the user visits while online, so they're available next offline.
 */
async function networkFirstWithOfflineFallback(request) {
  try {
    const response = await fetch(request);
    // Save every successfully-loaded HTML page for offline use
    if (response.ok) {
      const cache = await caches.open(CACHE_PAGES);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Search ALL caches globally — finds pages regardless of which cache they
    // were stored in (runtime CACHE_PAGES or install-time CACHE_STATIC).
    // ignoreSearch: true means /home?next=/ still matches /home in the cache.
    const cached = await caches.match(request, { ignoreSearch: true });
    if (cached) return cached;

    // Last resort: serve the landing page as a generic offline shell
    const rootCached = await caches.match('/');
    if (rootCached) return rootCached;

    // Nothing cached at all — show the inline offline page
    return new Response(OFFLINE_HTML, {
      status: 503,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  }
}

/** Serve from cache immediately; update cache in background */
async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);

  const networkFetch = fetch(request).then((response) => {
    if (response.ok) cache.put(request, response.clone());
    return response;
  }).catch(() => null);

  return cached || (await networkFetch) || new Response('', { status: 408 });
}

// ═════════════════════════════════════════════════════════════════════════════
// MESSAGE — allow pages to skip waiting / clear caches on demand
// ═════════════════════════════════════════════════════════════════════════════
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();

  if (event.data === 'CLEAR_CACHE') {
    caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k))));
  }
});
