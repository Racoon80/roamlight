/* Collections: create, rename, remove. */
(function () {
  "use strict";
  var say = document.getElementById("csay");
  if (!say) return;

  document.getElementById("newset").addEventListener("click", function () {
    var inp = document.getElementById("newtitle");
    var titel = inp.value.trim();
    if (!titel) { inp.focus(); return; }
    say.textContent = "…";
    window.famPost("/api/collections", { action: "new", title: titel })
      .then(function () { location.reload(); })
      .catch(function (e) { say.textContent = "✗ " + e.message; });
  });
  document.getElementById("newtitle").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); document.getElementById("newset").click(); }
  });

  Array.prototype.forEach.call(document.querySelectorAll("#sets .alb__row"),
    function (form) {
      var s = form.querySelector(".alb__say");
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        s.textContent = "…";
        window.famPost("/api/collections", {
          action: "rename", id: +form.dataset.id,
          title: form.querySelector('[name="title"]').value.trim()
        }).then(function (d) { s.textContent = "saved — " + d.title; })
          .catch(function (err) { s.textContent = "✗ " + err.message; });
      });
      var del = form.querySelector("[data-del]");
      del.addEventListener("click", function () {
        if (!window.confirm(
            "Delete this collection?\n\nThe photographs stay exactly where they are — " +
            "a collection only lists them.")) return;
        window.famPost("/api/collections", { action: "remove", id: +form.dataset.id })
          .then(function () { location.reload(); })
          .catch(function (err) { s.textContent = "✗ " + err.message; });
      });
    });
})();
