/* Share links: create, retire, and the quarantine. */
(function () {
  "use strict";

  var copy = document.getElementById("copy");
  if (copy) {
    copy.addEventListener("click", function () {
      var txt = document.getElementById("freshtext").textContent;
      var say = document.getElementById("copysay");
      /* ⚠ navigator.clipboard only works over https. Over a plain address it
         is `undefined` -- hence the fallback with a selection. */
      function fallback() {
        var t = document.createElement("textarea");
        t.value = txt; document.body.appendChild(t); t.select();
        try { document.execCommand("copy"); say.textContent = "copied"; }
        catch (e) { say.textContent = "select the text above and copy it"; }
        t.remove();
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt)
          .then(function () { say.textContent = "copied"; })
          .catch(fallback);
      } else { fallback(); }
    });
  }

  var form = document.getElementById("newshare");
  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var say = document.getElementById("nsay");
      var v = function (n) { return form.querySelector('[name="' + n + '"]'); };
      if (!v("collection_id").value) { say.textContent = "pick a collection first"; return; }
      say.textContent = "…";
      window.famPost("/api/shares", {
        action: "new", collection_id: +v("collection_id").value,
        days: +v("days").value,
        allow_download: v("allow_download").checked,
        allow_upload: v("allow_upload").checked,
        keep_gps: v("keep_gps").checked
      }).then(function () { location.reload(); })
        .catch(function (err) { say.textContent = "✗ " + err.message; });
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".lrow"), function (row) {
    var say = row.querySelector(".lrow__say");
    var pw = row.querySelector("[data-pw]");
    var rv = row.querySelector("[data-revoke]");
    if (pw) pw.addEventListener("click", function () {
      if (!window.confirm("Give this link a new password?\n\nThe link stays the same. " +
                          "Anyone holding the old password can no longer open it.")) return;
      window.famPost("/api/shares", { action: "password", id: +row.dataset.id })
        .then(function () { location.reload(); })
        .catch(function (e) { say.textContent = "✗ " + e.message; });
    });
    if (rv) rv.addEventListener("click", function () {
      if (!window.confirm("Turn this link off?\n\nIt stops working straight away, " +
                          "for everyone who has it.")) return;
      window.famPost("/api/shares", { action: "revoke", id: +row.dataset.id })
        .then(function () { location.reload(); })
        .catch(function (e) { say.textContent = "✗ " + e.message; });
    });
    var vw = row.querySelector("[data-views]");
    if (vw) vw.addEventListener("change", function () {
      say.textContent = "…";
      window.famPost("/api/shares", { action: "views", id: +row.dataset.id, max_views: vw.value })
        .then(function () { location.reload(); })   // recompute the state (used up / live)
        .catch(function (e) { say.textContent = "✗ " + e.message; });
    });
  });

  function quar(action, frage) {
    var q = document.getElementById("quar");
    if (!q) return;
    var ids = Array.prototype.filter.call(q.querySelectorAll('input[type="checkbox"]'),
      function (c) { return c.checked; }).map(function (c) { return c.value; });
    var say = document.getElementById("qsay");
    if (!ids.length) { say.textContent = "tick some first"; return; }
    if (frage && !window.confirm(frage)) return;
    say.textContent = "…";
    window.famPost("/api/shares", { action: action, ids: ids })
      .then(function () { location.reload(); })
      .catch(function (e) { say.textContent = "✗ " + e.message; });
  }
  var a = document.getElementById("acc"), r = document.getElementById("rej");
  if (a) a.addEventListener("click", function () { quar("accept", null); });
  if (r) r.addEventListener("click", function () {
    quar("reject", "Throw these away?\n\nThey are deleted from this machine. " +
                   "Nothing in your library is touched."); });
})();
