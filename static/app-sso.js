// The "Connect" button on /app/sso: ask for a code bound to the app's
// challenge, then hand it back to the app through roamlight://.
//
// ⚠ Script and not a form: a form POST redirecting to roamlight:// is blocked
//   by the site's `form-action 'self'`. A page navigating is not a form.
// ⚠ The "Open Roamlight" link is the way on if the browser will not follow a
//   scripted jump into an app (Chrome is strict about that without a fresh
//   tap). Tapping a link always counts.
(function () {
  var box = document.getElementById("sso");
  var go = document.getElementById("go");
  var say = document.getElementById("say");
  var open = document.getElementById("open");
  if (!box || !go) return;
  go.addEventListener("click", function () {
    go.disabled = true;
    say.textContent = "…";
    fetch("/api/app/sso", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ challenge: box.dataset.challenge })
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (res) {
      if (!res.ok || !res.j.url || res.j.url.indexOf("roamlight://") !== 0) {
        say.textContent = (res.j && res.j.detail) || "That did not work.";
        go.disabled = false;
        return;
      }
      open.href = res.j.url;
      open.hidden = false;
      say.textContent = "Back to the app…";
      window.location.href = res.j.url;
    }).catch(function () {
      say.textContent = "No connection to the site.";
      go.disabled = false;
    });
  });
})();
