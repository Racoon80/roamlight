/* Settings: fetch the list from the directory, and set the album permissions. */
(function () {
  "use strict";
  var rsay = document.getElementById("rsay");
  var btn = document.getElementById("refresh");
  if (btn) {
    btn.addEventListener("click", function () {
      btn.disabled = true; rsay.textContent = "asking Authentik…";
      window.famPost("/api/members/refresh", {}).then(function (d) {
        if (!d.ok) { rsay.textContent = "✗ " + (d.error || "failed"); btn.disabled = false; return; }
        rsay.textContent = d.members + " members · " + d.groups + " groups — reloading";
        setTimeout(function () { location.reload(); }, 900);
      }).catch(function (e) { rsay.textContent = "✗ " + e.message; btn.disabled = false; });
    });
  }

  /* ---- Tags a Persounen verwalten ---- */
  Array.prototype.forEach.call(document.querySelectorAll(".names__col"), function (col) {
    var kind = col.dataset.kind;

    function ruff(body, dann) {
      window.famPost("/api/names", body).then(dann).catch(function (e) {
        window.alert(e.message);
      });
    }

    col.querySelector("[data-add]").addEventListener("click", function () {
      var inp = col.querySelector(".names__new");
      var name = inp.value.trim();
      if (!name) { inp.focus(); return; }
      /* ⚠ A name that sits on no photograph is created here all the same --
         that is the whole point of "add by hand". It then stands in the lists
         and the dropdowns, with the count 0. */
      ruff({ kind: kind, action: "new", name: name }, function () { location.reload(); });
    });
    col.querySelector(".names__new").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); col.querySelector("[data-add]").click(); }
    });

    Array.prototype.forEach.call(col.querySelectorAll("[data-suggest]"), function (b) {
      b.addEventListener("click", function () {
        ruff({ kind: kind, action: "new", name: b.dataset.suggest },
             function () { location.reload(); });
      });
    });

    Array.prototype.forEach.call(col.querySelectorAll(".names__list li[data-id]"),
      function (li) {
        var say = li.querySelector(".names__say");
        li.querySelector("[data-save]").addEventListener("click", function () {
          var neu = li.querySelector(".names__name").value.trim();
          if (!neu) return;
          ruff({ kind: kind, action: "rename", id: +li.dataset.id, name: neu },
               function (d) {
                 say.textContent = d.merged_into ? "merged" : "saved";
                 if (d.merged_into) setTimeout(function () { location.reload(); }, 900);
               });
        });
        li.querySelector("[data-del]").addEventListener("click", function () {
          var name = li.querySelector(".names__name").value.trim();
          if (!window.confirm("Take “" + name + "” away?\n\nIt comes off every " +
                              "photograph it is on. The photographs stay exactly " +
                              "where they are.")) return;
          ruff({ kind: kind, action: "forget", id: +li.dataset.id },
               function () { location.reload(); });
        });
      });
  });

  Array.prototype.forEach.call(document.querySelectorAll(".aclrow"), function (form) {
    var say = form.querySelector(".alb__say");
    var state = form.querySelector(".aclrow__state");

    function send(liste) {
      say.textContent = "…";
      return window.famPost("/api/albums/audience", {
        year: form.dataset.year, country: form.dataset.country,
        event: form.dataset.event, audience: liste
      }).then(function (d) {
        say.textContent = d.admin_only ? "only you" : "saved";
        state.textContent = d.admin_only ? "only you" : d.audience.length + " allowed";
      }).catch(function (e) { say.textContent = "✗ " + e.message; });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var liste = Array.prototype.filter.call(
        form.querySelectorAll('input[type="checkbox"]'), function (c) { return c.checked; })
        .map(function (c) { return c.value; });
      /* An empty list opens the album to everybody -- that is a change somebody
         should notice, so it asks first. */
      if (!liste.length && !window.confirm(
          "Nothing is ticked.\n\nThis album will then be seen by nobody but you.")) return;
      send(liste);
    });

    form.querySelector("[data-open]").addEventListener("click", function () {
      Array.prototype.forEach.call(form.querySelectorAll('input[type="checkbox"]'),
        function (c) { c.checked = false; });
      send([]);
    });
  });
})();
