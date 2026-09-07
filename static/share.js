/* The guest upload. One file after another -- a batch request with 50
   photographs would only notice the limits at the very end. */
(function () {
  "use strict";
  var f = document.getElementById("gfile");
  if (!f) return;
  var out = document.getElementById("gout");
  var name = document.getElementById("gname");
  var token = location.pathname.split("/")[2];

  f.addEventListener("change", function () {
    var files = Array.prototype.slice.call(f.files);
    f.value = "";
    if (!files.length) return;
    if (!name.value.trim()) { out.textContent = "Please put your name in first."; name.focus(); return; }
    var ok = 0, bad = 0;
    out.textContent = "sending 0/" + files.length + "…";
    files.reduce(function (chain, file) {
      return chain.then(function () {
        var fd = new FormData();
        fd.append("guest", name.value.trim());
        fd.append("file", file);
        return fetch("/s/" + token + "/upload", { method: "POST", body: fd })
          .then(function (r) {
            return r.text().then(function (txt) {
              var d = null; try { d = JSON.parse(txt); } catch (e) { d = null; }
              if (r.ok) { ok++; return; }
              bad++;
              out.dataset.last = (d && d.detail) || ("error " + r.status);
            });
          }).then(function () {
            out.textContent = "sending " + (ok + bad) + "/" + files.length + "…";
          });
      });
    }, Promise.resolve()).then(function () {
      out.textContent = ok + " sent" + (bad ? ", " + bad + " refused" : "") +
        (out.dataset.last ? " — " + out.dataset.last : "") +
        ". They are waiting for the owner.";
    });
  });
})();
