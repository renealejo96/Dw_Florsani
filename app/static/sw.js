const CACHE_NAME = 'florsani-pwa-v9';
const STATIC_ASSETS = [
  '/static/css/main.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(async (cache) => {
      await Promise.allSettled(
        STATIC_ASSETS.map((url) =>
          fetch(url)
            .then((res) => {
              if (res.ok) return cache.put(url, res);
            })
            .catch(() => {})
        )
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    (async () => {
      // Activar Navigation Preload para que las peticiones al servidor salgan en paralelo sin esperar al Service Worker
      if (self.registration.navigationPreload) {
        try {
          await self.registration.navigationPreload.enable();
        } catch (_) {}
      }
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)));
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;

  const url = new URL(e.request.url);

  // 1. Navegación HTML:
  // Solo cachear en disco los módulos de campo offline (/fenogramas y /cosechas/nuevo).
  // El resto de módulos del ERP (/fumigacion, /fertilizacion, /bodega, /tpsr, etc.) pasan directo a red con Navigation Preload sin clonar ni escribir MBs en disco.
  if (e.request.mode === 'navigate') {
    const isFieldOfflineRoute =
      url.pathname.startsWith('/fenogramas') ||
      url.pathname.startsWith('/cosechas/nuevo');

    e.respondWith(
      (async () => {
        try {
          const preloadRes = await e.preloadResponse;
          const netRes = preloadRes || (await fetch(e.request));
          if (isFieldOfflineRoute && netRes && netRes.status === 200) {
            const clone = netRes.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(e.request, clone)).catch(() => {});
          }
          return netRes;
        } catch (err) {
          const cachedRes = await caches.match(e.request);
          if (cachedRes) return cachedRes;
          if (url.pathname.startsWith('/fenogramas')) {
            return (await caches.match('/fenogramas/')) || (await caches.match('/fenogramas'));
          }
          if (url.pathname.startsWith('/cosechas')) {
            return (await caches.match('/cosechas/nuevo')) || (await caches.match('/cosechas/nuevo-registro'));
          }
          return (await caches.match('/')) || Response.error();
        }
      })()
    );
    return;
  }

  // 2. Recursos estáticos (CSS, JS, Fuentes, CDN): Cache-First ultrarrápido
  const isStaticOrCdn =
    url.pathname.startsWith('/static/') ||
    url.hostname.includes('jsdelivr.net') ||
    url.hostname.includes('cloudflare.com') ||
    url.hostname.includes('googleapis.com') ||
    url.hostname.includes('gstatic.com');

  if (isStaticOrCdn) {
    e.respondWith(
      caches.match(e.request).then((cached) => {
        if (cached) return cached;
        return fetch(e.request).then((res) => {
          if (res && res.status === 200) {
            const resClone = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(e.request, resClone)).catch(() => {});
          }
          return res;
        });
      })
    );
  }
});
