/* The service worker. Served under `/sw.js` (see main.py) and not under
   `/static/`, because otherwise it would only be responsible for `/static/`.

   ⚠ **NO photograph and NO page is stored here.** That is deliberate, and it
     is the most important line in this file:

     · A photograph in the cache is still on the device after signing out. On a
       site where "who may see what" is the whole concept, that would be the
       quietest data leak you could imagine.
     · A stored HTML page shows albums that have since been taken away from
       somebody -- and they would not notice they are looking at something old.

   Only the **shell** is stored: stylesheet, scripts, fonts, icons. Those are
   the things that cost time on a mobile network, and there is nothing personal
   in them.

   Everything else goes straight to the server -- as without a service worker. */
const CACHE = "family-shell-v1";

/* ⚠ Only things with `?v=` are taken cache-first. That parameter comes from
   `static_ver`, and it changes with every deploy -- so no old file is left
   hanging around. */
const SHELL = [
  "/static/site.css",
  "/static/site.js",
  "/static/json.js",
  "/static/fonts/bodoni-moda-400.woff2",
  "/static/fonts/bodoni-moda-500.woff2",
  "/static/fonts/bodoni-moda-600.woff2",
  "/static/fonts/spectral-300.woff2",
  "/static/fonts/spectral-400.woff2",
  "/static/fonts/ibm-plex-mono-400.woff2",
  "/static/icons/icon-192.png",
];

self.addEventListener("install", (e) => {
  /* `addAll` fails completely if just ONE file is missing -- so one at a time,
     and a failure on one must not kill the others. */
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await Promise.all(SHELL.map((u) => c.add(u).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  /* ⚠ ONLY `/static/` -- and no photographs out of it. `/photos/` is
     deliberately outside, and so is `/s/` (share links). */
  if (!url.pathname.startsWith("/static/")) return;

  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    const hit = await c.match(req, { ignoreSearch: false });
    if (hit) return hit;
    const res = await fetch(req);
    /* Only what succeeded, and only our own things. */
    if (res && res.ok && res.type === "basic") c.put(req, res.clone());
    return res;
  })());
});
