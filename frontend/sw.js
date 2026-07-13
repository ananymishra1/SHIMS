const CACHE_NAME = "shims-app-v6";
const APP_SHELL = [
  "/",
  "/offline.html",
  "/manifest.webmanifest",
  "/static/shims_omni.html",
  "/static/favicon.svg",
  "/static/icon.svg",
  "/static/maskable-icon.svg"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET") return;
  if (url.pathname.startsWith("/chat") || url.pathname.startsWith("/agent") || url.pathname.startsWith("/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put("/", copy));
          return response;
        })
        .catch(() => caches.match("/").then((response) => response || caches.match("/offline.html")))
    );
    return;
  }

  // Always fetch JS/CSS fresh so updates apply immediately
  if (url.pathname.endsWith(".js") || url.pathname.endsWith(".css")) {
    event.respondWith(fetch(request));
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request))
  );
});
