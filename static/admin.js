/* The admin's "Scan now" button. It lives here and not inline in the page:
   `script-src 'self'` blocks an inline script -- the button would have done
   nothing, and nobody would have noticed. */
(function () {
  "use strict";
  var btn = document.getElementById("scan");
  if (!btn) return;
  var out = document.getElementById("scan-out");
  var timer = null;
  function poll() {
    window.famFetch("/api/scan", {}).then(function (d) {
      var p = (d && d.progress) || {};
      if (p.running) {
        out.textContent = "scanning " + (p.pct || 0) + "% — "
          + (p.done || 0) + " / " + (p.total || "?")
          + (p.current ? " · " + p.current : "")
          + " · " + (p.new || 0) + " new";
      } else {
        // Done: show the final numbers and stop polling.
        out.textContent = "done — " + (p.seen || 0) + " seen · "
          + (p.new || 0) + " new" + (p.filled ? " · " + p.filled + " completed" : "");
        btn.disabled = false;
        clearInterval(timer); timer = null;
      }
    }).catch(function () { /* keep polling, one failure is not fatal */ });
  }
  btn.addEventListener("click", function () {
    btn.disabled = true;
    out.textContent = "starting…";
    window.famPost("/api/scan", {})
      .then(function () {
        if (timer) clearInterval(timer);
        poll();                              // straight away, the first time
        timer = setInterval(poll, 1500);     // duerno all 1,5 s
      })
      .catch(function (e) { out.textContent = "✗ " + e.message; btn.disabled = false; });
  });
  // Is a scan already running (after a reload, say)? Then show it straight away.
  window.famFetch("/api/scan", {}).then(function (d) {
    if (d && d.progress && d.progress.running) {
      btn.disabled = true; poll(); timer = setInterval(poll, 1500);
    }
  }).catch(function () {});
})();
