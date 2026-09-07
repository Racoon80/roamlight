/* Register the service worker. Here and not inline: `script-src 'self'`.

   ⚠ `/sw.js` and not `/static/sw.js`: a service worker is only responsible for
   the path it is served under. From `/static/` it would not see the shell of
   the site at all. */
(function () {
  "use strict";
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js").catch(function () {
      /* No service worker (a private window, an old browser, no HTTPS) is not
         an error -- the site simply runs without one. */
    });
  });
})();
