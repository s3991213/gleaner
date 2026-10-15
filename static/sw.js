/* Service worker: caches the app shell only. Feed data (/api/) always goes to the network. */
const CACHE = "gleaner-shell-v1";
const SHELL = [
  "/",
  "/static/styles.css",
  "/static/app.js",
  "/manifest.json",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname === "/healthz") return;

  if (req.mode === "navigate") {
    // Network first so updates show up; fall back to the cached shell offline.
    event.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }

  if (!SHELL.includes(url.pathname)) return;

  // Stale while revalidate for shell assets.
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(req);
      const network = fetch(req)
        .then((res) => {
          if (res.ok) cache.put(req, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
