/* Your own cloud: connect it, and the originals of your uploads go there.
   See app/cloud.py. */
(function () {
  "use strict";
  var say = document.getElementById("cl-say");
  if (!say) return;
  function tell(t) { say.textContent = t; }
  function fail(e) { tell("✗ " + e.message); }
  function on(id, fn) {
    var b = document.getElementById(id);
    if (b) b.addEventListener("click", function () { fn(b); });
  }

  /* Login Flow v2: the cloud's own sign-in page opens in a new tab, the
     person approves there, and this page asks every two seconds whether
     they have. ⚠ The tab is opened BEFORE the request, in the click itself:
     a window opened later, from a promise, is what pop-up blockers stop. */
  on("cl-on", function (b) {
    var win = window.open("about:blank", "_blank");
    b.disabled = true; tell("asking your cloud…");
    window.famPost("/api/cloud/start", {}).then(function (d) {
      if (win) { win.location = d.login_url; } else { window.location = d.login_url; return; }
      tell("approve it in the other tab — waiting…");
      var until = Date.now() + 20 * 60 * 1000;
      (function ask() {
        window.famPost("/api/cloud/poll", {}).then(function (p) {
          if (p.state === "connected") { tell("✓ connected"); setTimeout(function () { location.reload(); }, 700); return; }
          if (p.state === "expired" || p.state === "none" || Date.now() > until) {
            tell("✗ that took too long — try again"); b.disabled = false; return;
          }
          setTimeout(ask, 2000);
        }).catch(function (e) { fail(e); b.disabled = false; });
      })();
    }).catch(function (e) { if (win) win.close(); fail(e); b.disabled = false; });
  });

  on("cl-manual", function (b) {
    b.disabled = true; tell("checking…");
    window.famPost("/api/cloud/manual", {
      login: document.getElementById("cl-login").value,
      password: document.getElementById("cl-pass").value
    }).then(function () { tell("✓ connected"); setTimeout(function () { location.reload(); }, 700); })
      .catch(function (e) { fail(e); b.disabled = false; });
  });

  on("cl-folder-save", function (b) {
    b.disabled = true; tell("saving…");
    window.famPost("/api/cloud/folder", { folder: document.getElementById("cl-folder").value })
      .then(function (d) { document.getElementById("cl-folder").value = d.folder; tell("✓ saved"); })
      .catch(fail).then(function () { b.disabled = false; });
  });

  on("cl-retry", function (b) {
    b.disabled = true; tell("trying…");
    window.famPost("/api/cloud/retry", {}).then(function (d) {
      tell(d.queued + " on their way");
    }).catch(fail).then(function () { b.disabled = false; });
  });

  on("cl-off", function (b) {
    if (!window.confirm("Stop copying your originals to your cloud?")) return;
    b.disabled = true;
    window.famPost("/api/cloud/disconnect", {}).then(function () { location.reload(); })
      .catch(function (e) { fail(e); b.disabled = false; });
  });
})();
