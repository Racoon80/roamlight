/* The album workshop: scan, put right, who may look, and tag straight away.

   It lives here and not inline in the page -- `script-src 'self'` blocks an
   inline script, and the button would have done nothing without anybody
   noticing. */
(function () {
  "use strict";
  var post = window.famPost;

  /* ---- 01 scannen ---- */
  var scan = document.getElementById("scan");
  var scanout = document.getElementById("scanout");
  if (scan) {
    var stimer = null, wasRunning = false;
    function spoll() {
      window.famFetch("/api/scan", {}).then(function (d) {
        var p = (d && d.progress) || {};
        if (p.running) {
          wasRunning = true;
          scanout.textContent = "scanning " + (p.pct || 0) + "% — " + (p.done || 0)
            + " / " + (p.total || "?") + (p.current ? " · " + p.current : "")
            + " · " + (p.new || 0) + " new";
        } else {
          scanout.textContent = "done — " + (p.seen || 0) + " seen · " + (p.new || 0) + " new";
          scan.disabled = false;
          if (stimer) { clearInterval(stimer); stimer = null; }
          // New photographs? Reload the list so the new albums turn up.
          if (wasRunning && p.new) setTimeout(function () { location.reload(); }, 1200);
        }
      }).catch(function () {});
    }
    scan.addEventListener("click", function () {
      scan.disabled = true; scanout.textContent = "starting…";
      post("/api/scan", {}).then(function () {
        if (stimer) clearInterval(stimer);
        spoll(); stimer = setInterval(spoll, 1500);
      }).catch(function (e) { scanout.textContent = "✗ " + (e.message || e); scan.disabled = false; });
    });
    // Is a scan already running? Then show it straight away.
    window.famFetch("/api/scan", {}).then(function (d) {
      if (d && d.progress && d.progress.running) {
        scan.disabled = true; wasRunning = true; spoll(); stimer = setInterval(spoll, 1500);
      }
    }).catch(function () {});
  }

  /* The same rule as tree.strip_year() on the server. For the preview only. */
  function noYear(s) {
    var out = String(s || "").replace(/[\s._-]+(19|20)\d{2}$/, "").trim();
    return out || String(s || "").trim();
  }

  Array.prototype.forEach.call(document.querySelectorAll(".alb2__row"), function (form) {
    var say = form.querySelector(".alb__say");
    var btn = form.querySelector('button[type="submit"]');
    var path = form.querySelector(".alb__path");
    function v(n) { return form.querySelector('[name="' + n + '"]').value.trim(); }

    /* --- The journey: departure + means of transport (+ optional legs) --- */
    var jBtn = form.querySelector("[data-journey]");
    var legsBox = form.querySelector(".alb2__legs");
    function legRow(mode, name) {
      var div = document.createElement("div"); div.className = "alb2__leg";
      div.innerHTML = '<select class="alb2__leg-mode">'
        + ["car", "bus", "train", "plane"].map(function (m) {
            return '<option value="' + m + '"' + (m === mode ? " selected" : "") + ">" + m + "</option>";
          }).join("")
        + '</select><input class="alb2__leg-name" placeholder="to… (e.g. Findel)" autocomplete="off">'
        + '<button class="btn" type="button" data-leg-del title="remove this stop">&times;</button>';
      div.querySelector(".alb2__leg-name").value = name || "";
      return div;
    }
    /* The multi-stop switch. Off = the box is gone and the simple trip counts
       when saving; the legs themselves stay (server: `legs_on`). */
    var multiBox = form.querySelector("[data-multi]");
    var legsWrap = form.querySelector(".alb2__legs-wrap");
    if (multiBox && legsWrap) multiBox.addEventListener("change", function () {
      legsWrap.hidden = !multiBox.checked;
      /* On and no leg yet: one empty row, or you look at an empty box and have
         no idea what is expected of you. */
      if (multiBox.checked && legsBox && !legsBox.querySelector(".alb2__leg"))
        legsBox.appendChild(legRow("car", ""));
    });

    var addBtn = form.querySelector("[data-leg-add]");
    if (addBtn && legsBox) addBtn.addEventListener("click", function () { legsBox.appendChild(legRow("car", "")); });
    if (legsBox) legsBox.addEventListener("click", function (e) {
      var d = e.target.closest && e.target.closest("[data-leg-del]");
      if (d && d.parentNode) d.parentNode.remove();
    });
    if (jBtn) jBtn.addEventListener("click", function () {
        var dep = form.querySelector(".alb2__dep");
        var mode = form.querySelector(".alb2__mode");
        var jsay = form.querySelector(".alb2__jsay");
        var legs = legsBox ? Array.prototype.map.call(legsBox.querySelectorAll(".alb2__leg"), function (r) {
          return { transport: r.querySelector(".alb2__leg-mode").value,
                   name: r.querySelector(".alb2__leg-name").value.trim() };
        }).filter(function (l) { return l.name; }) : [];
        jBtn.disabled = true; jsay.textContent = "…";
        post("/api/albums/journey", {
          year: form.dataset.year, country: form.dataset.country, event: form.dataset.event,
          departure: dep.value, transport: mode.value, legs: legs,
          multi: multiBox ? multiBox.checked : null
        }).then(function (d) {
          jBtn.disabled = false;
          jsay.textContent = d.cleared ? "trip cleared"
            : (d.multi === false && legs.length ? "saved — simple trip (stops kept)"
            : d.legs ? ("saved — " + d.legs + " stop" + (d.legs === 1 ? "" : "s")
                         + (d.located ? "" : " (a place was not found)"))
            : (d.located ? "saved" : "saved — but that place was not found on the map"));
        }).catch(function (e) { jBtn.disabled = false; jsay.textContent = "✗ " + (e.message || e); });
      });

    /* --- ganzen Album vum Site huelen (Originaler bleiwen) --- */
    var rmBtn = form.querySelector("[data-remove]");
    if (rmBtn) rmBtn.addEventListener("click", function () {
      if (!window.confirm(
          "Take this WHOLE album off the site?\n\n  "
          + form.dataset.year + " / " + form.dataset.country + " / " + form.dataset.event
          + "\n\nThe originals are NOT touched — only the "
          + "site's own copy is removed, and a later scan will NOT bring it back.")) return;
      rmBtn.disabled = true; say.textContent = "removing…";
      post("/api/albums/remove", {
        year: form.dataset.year, country: form.dataset.country, event: form.dataset.event
      }).then(function (d) {
        say.textContent = "removed " + (d.removed || 0) + " — originals untouched";
        form.style.opacity = "0.4";
        setTimeout(function () { location.reload(); }, 1100);
      }).catch(function (e) { rmBtn.disabled = false; say.textContent = "✗ " + (e.message || e); });
    });

    /* --- what it will be called afterwards, and whether something is there --- */
    var alleWeeer = Array.prototype.map.call(
      document.querySelectorAll(".alb2__row"), function (f) {
        return f.dataset.year + "/" + f.dataset.country + "/" + f.dataset.event;
      });
    var hinweis = document.createElement("span");
    hinweis.className = "meta alb2__merge";
    path.parentNode.insertBefore(hinweis, path.nextSibling);

    function preview() {
      var neu = v("new_year") + "/" + v("new_country") + "/" + noYear(v("new_event"));
      path.textContent = neu;
      var eegen = form.dataset.year + "/" + form.dataset.country + "/" + form.dataset.event;
      hinweis.textContent = (neu !== eegen && alleWeeer.indexOf(neu) >= 0)
        ? "↳ this album already exists — the two will be put together"
        : "";
    }
    Array.prototype.forEach.call(form.querySelectorAll(".alb2__fields input"),
      function (i) { i.addEventListener("input", preview); });

    /* --- wien dierf kucken --- */
    function acl() {
      return Array.prototype.filter.call(form.querySelectorAll("[data-acl]"),
        function (c) { return c.checked; }).map(function (c) { return c.value; });
    }
    var whoState = form.querySelector(".alb2__who-state");
    Array.prototype.forEach.call(form.querySelectorAll("[data-acl]"), function (c) {
      c.addEventListener("change", function () {
        var n = acl().length;
        whoState.textContent = n ? n + " picked" : "only you";
      });
    });

    /* --- saving: the fields first, then the permissions ---
       ⚠ In that order. The permissions hang off the album key, and that key
       changes with the fields -- the other way round they would stay on the
       old key and the album would suddenly be open to everybody. */
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var alt = form.dataset.year + "/" + form.dataset.country + "/" + form.dataset.event;
      var neu = v("new_year") + "/" + v("new_country") + "/" + noYear(v("new_event"));
      var zesummen = alleWeeer.indexOf(neu) >= 0 && neu !== alt;
      if (neu !== alt && !window.confirm(
            (zesummen
              ? "Put this album INTO the one that is already there?\n\n  " + alt +
                "\n  →  " + neu +
                "\n\nThe photographs are moved into it. Nothing is deleted." +
                "\n\nCareful: the two viewing lists are put together as well, " +
                "so these photographs may become visible to more people."
              : "Move the folder in BOTH trees?\n\n  " + alt + "\n  →  " + neu +
                "\n\nNothing is deleted — the photographs are moved."))) return;
      var liste = acl();
      if (!liste.length && form.dataset.hadAcl === "1" && !window.confirm(
            "Nobody is ticked.\n\nThis album will then be seen by nobody but you.")) return;
      btn.disabled = true;
      say.textContent = "…";
      post("/api/albums/edit", {
        year: form.dataset.year, country: form.dataset.country, event: form.dataset.event,
        new_year: v("new_year"), new_country: v("new_country"),
        new_event: v("new_event"), new_place: v("new_place")
      }).then(function (d) {
        form.dataset.year = d.to.split("/")[0];
        form.dataset.country = d.to.split("/")[1];
        form.dataset.event = d.to.split("/").slice(2).join("/");
        var enc = encodeURIComponent;
        var a = form.querySelectorAll(".alb2__act a");
        a[0].href = "/admin/album/" + form.dataset.year + "/" + enc(form.dataset.country) +
                    "/" + enc(form.dataset.event);
        a[1].href = "/y/" + form.dataset.year + "/" + enc(form.dataset.country) +
                    "/" + enc(form.dataset.event);
        return post("/api/albums/audience", {
          year: form.dataset.year, country: form.dataset.country,
          event: form.dataset.event, audience: liste
        }).then(function (r) {
          say.textContent = (d.from === d.to ? "saved" : d.from + " → " + d.to) +
            " · " + d.photos + " · " +
            (r.admin_only ? "only you" : r.audience.length + " may see") +
            (d.was_crooked ? " · folder straightened" : "");
          form.dataset.hadAcl = r.admin_only ? "0" : "1";
          btn.disabled = false;
          /* Undo: turns the whole step back (files, database, permissions). */
          if (d.batch) {
            var u = document.createElement("button");
            u.type = "button"; u.className = "btn"; u.textContent = "undo";
            u.style.marginLeft = "0.6rem"; u.style.padding = "0.15rem 0.6rem";
            u.style.fontSize = "0.75rem";
            u.addEventListener("click", function () {
              u.disabled = true;
              post("/api/albums/undo", { batch: d.batch })
                .then(function () { location.reload(); })
                .catch(function (e) { u.disabled = false; say.textContent = "✗ " + e.message; });
            });
            say.appendChild(document.createTextNode(" "));
            say.appendChild(u);
          }
          /* After saving, a row from "New scanned albums" belongs down in
             "Published". That cannot be re-hung in the browser without the
             counts and the headings going wrong -- so the page is reloaded. */
          if (form.dataset.new === "1") {
            say.textContent += " — moving to published";
            setTimeout(function () { location.reload(); }, 1100);
          }
        });
      }).catch(function (err) {
        say.textContent = "✗ " + err.message;
        btn.disabled = false;
      });
    });

    /* --- tag straight away: on EVERY photograph of the album --- */
    Array.prototype.forEach.call(form.querySelectorAll("[data-mark]"), function (b) {
      b.addEventListener("click", function () {
        var kind = b.dataset.mark;
        var inp = form.querySelector(kind === "tag" ? ".alb2__whotag" : ".alb2__who");
        var name = inp.value.trim();
        if (!name) { inp.focus(); return; }
        b.disabled = true; say.textContent = "…";
        /* More than one name at once: separated by commas (or semicolons).
           The server does the splitting -- so exactly the same rule applies,
           whichever way it comes in. */
        post("/api/mark", {
          kind: kind, names: name, on: true,
          album: { year: form.dataset.year, country: form.dataset.country,
                   event: form.dataset.event }
        }).then(function (d) {
          say.textContent = "＋ " + d.names.join(", ") + " on " + d.photos;
          var have = form.querySelector(".alb2__have");
          if (have.querySelector(".meta")) have.innerHTML = "";
          d.names.forEach(function (n) {
            var s = document.createElement("span");
            s.className = kind === "tag" ? "pill pill--tag" : "pill";
            s.textContent = n + " " + d.photos;
            have.appendChild(s);
          });
          inp.value = ""; b.disabled = false;
        }).catch(function (err) { say.textContent = "✗ " + err.message; b.disabled = false; });
      });
    });
    form.querySelectorAll(".alb2__who, .alb2__whotag").forEach(function (inp) {
      inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          form.querySelector('[data-mark="' +
            (inp.classList.contains("alb2__whotag") ? "tag" : "person") + '"]').click();
        }
      });
    });

    form.dataset.hadAcl = acl().length ? "1" : "0";
  });
})();
