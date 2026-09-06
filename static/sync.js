/* Start the sync and watch how far it has got.

   The run happens as a job in the background -- so nothing is waited for here,
   it is asked for. Here and not inline: `script-src 'self'`. */
(function () {
  "use strict";
  var out = document.getElementById("syncout");
  if (!out) return;
  var timer = null;

  function post(kind, confirm_missing) {
    Array.prototype.forEach.call(document.querySelectorAll(".syncbar .btn, .halt .btn"),
      function (b) { b.disabled = true; });
    out.textContent = "running…";
    window.famPost("/api/sync", { kind: kind, confirm_missing: !!confirm_missing })
      .then(function () { watch(0); })
      .catch(function (e) { out.textContent = e.message; frei(); });
  }

  function frei() {
    Array.prototype.forEach.call(document.querySelectorAll(".syncbar .btn, .halt .btn"),
      function (b) { b.disabled = false; });
  }

  /* Ask until the last run has an end date. After three minutes it gives up --
     a deep run through 30,000 photographs takes longer, and at that point
     reloading the page is more honest than a counter that lies. */
  function watch(n) {
    clearTimeout(timer);
    timer = setTimeout(function () {
      window.famFetch("/api/sync").then(function (d) {
        var r = (d.runs || [])[0];
        if (!r) { return watch(n + 1); }
        if (!r.finished_at) {
          out.textContent = "running… (" + (n + 1) + ")";
          return n < 90 ? watch(n + 1) : (out.textContent =
            "still running — reload the page to see where it got to", frei());
        }
        out.textContent = r.halted_reason
          ? "stopped: " + r.halted_reason
          : r.n_new + " new · " + r.n_changed + " changed · " +
            r.n_moved + " moved · " + r.n_missing + " missing — reloading";
        frei();
        setTimeout(function () { location.reload(); }, 1500);
      }).catch(function (e) { out.textContent = e.message; frei(); });
    }, 2000);
  }

  var q = document.getElementById("go-quick");
  var dp = document.getElementById("go-deep");
  var c = document.getElementById("go-quick-confirm");
  if (q) q.addEventListener("click", function () { post("quick", false); });
  if (dp) dp.addEventListener("click", function () { post("deep", false); });
  if (c) c.addEventListener("click", function () { post("quick", true); });
})();
