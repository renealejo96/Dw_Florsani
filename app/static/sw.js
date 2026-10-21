const CACHE_NAME = 'florsani-pwa-v6';
const STATIC_ASSETS = [
  '/',
  '/fenogramas/',
  '/cosechas/nuevo',
  '/cosechas/nuevo-registro',
  '/static/css/main.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(async (cache) => {
      // Usar Promise.allSettled para que un redireccionamiento 302 o fallo puntual no aborte el resto de assets
      await Promise.allSettled(
        STATIC_ASSETS.map((url) =>
          fetch(url, { cache: 'no-cache' })
            .then((res) => {
              if (res.ok) return cache.put(url, res);
            })
            .catch((err) => console.warn('[SW] Pre-caching asset warning:', url, err))
        )
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  return self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  // Only process GET requests; POST/PUT/DELETE are handled by our offline queue in localStorage
  if (e.request.method !== 'GET') return;

  // 1. Manejo de navegación HTML (Páginas de la App) - Network First con Fallback a Cache
  if (e.request.mode === 'navigate' || e.request.headers.get('accept')?.includes('text/html')) {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          if (res.status === 200) {
            const resClone = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(e.request, resClone));
          }
          return res;
        })
        .catch(() => {
          return caches.match(e.request).then((cachedRes) => {
            if (cachedRes) return cachedRes;
            // Si la URL exacta no está, intentar páginas de trabajo según el módulo
            if (e.request.url.includes('/fenogramas')) {
              return caches.match('/fenogramas/').then(r => r || caches.match('/fenogramas'));
            }
            if (e.request.url.includes('/cosechas')) {
              return caches.match('/cosechas/nuevo').then(r => r || caches.match('/cosechas/nuevo-registro'));
            }
            return caches.match('/');
          });
        })
    );
    return;
  }

  // 2. Archivos estáticos (CSS, JS, Fuentes, CDN) - Stale-While-Revalidate
  e.respondWith(
    caches.match(e.request).then((cached) => {
      const fetchPromise = fetch(e.request)
        .then((res) => {
          if (res.status === 200 && (e.request.url.includes('/static/') || e.request.url.includes('cdn') || e.request.url.includes('font'))) {
            const resClone = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(e.request, resClone));
          }
          return res;
        })
        .catch(() => cached);

      return cached || fetchPromise;
    })
  );
});
