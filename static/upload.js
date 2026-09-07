/* Upload.
   The order is part of how it works: the four fields first -- PICK one of the
   ones that exist, or make a NEW one -- and the photographs after that. As
   long as the path is not settled, the drop zone is locked. */
(function () {
  "use strict";
  var CHUNK = 5 * 1024 * 1024;
  var $ = function (id) { return document.getElementById(id); };
  var drop = $("drop"), pick = $("pick"), hint = $("drop-hint"), ready = $("drop-ready");
  var after = $("after"), queue = $("queue"), paths = $("paths"), summary = $("summary");
  var notes = $("notes"), result = $("result"), step2 = $("step2note");
  var F = {}, batch = null, rows = {};
  var known = { years: {}, countries: [], places: [], all_events: [] };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; });
  }
  var j = window.famFetch;        // does not read an error page as JSON
  function val(k) { return (F[k] ? F[k].value : "").trim(); }
  /* Has to agree with tree.strip_year() on the server: a year at the end of
     the name comes off, because it is already the folder above. */
  function noYear(s) {
    var out = String(s || "").replace(/[\s._-]+(19|20)\d{2}$/, "").trim();
    return out || String(s || "").trim();
  }

  function complete() { return val("year") && val("country") && val("event"); }

  /* ---- The picker ------------------------------------------------------
     Nobody sees a `datalist`, and "pick one or make a new one" has to be
     visible. So: a real list -- what exists is shown, with the number of
     photographs in it -- and underneath stands what you are building new. */
  function Combo(box) {
    var input = box.querySelector("input");
    var list = box.querySelector(".combo__list");
    var toggle = box.querySelector(".combo__toggle");
    var self = { input: input, options: function () { return []; }, last: [] };
    var at = -1;

    function render() {
      var q = input.value.trim().toLowerCase();
      var opts = self.options().filter(function (o) {
        var hay = (String(o.label) + " " + (o.note || "")).toLowerCase();
        return !q || hay.indexOf(q) >= 0; });
      self.last = opts;
      var html = "";
      if (!opts.length) {
        html += '<div class="combo__none">' +
          (self.options().length ? "Nothing matches — it will be created."
                                 : "No folders here yet — type a new one.") + "</div>";
      }
      html += opts.map(function (o, i) {
        return '<button type="button" class="combo__opt" data-i="' + i + '" role="option">' +
          esc(o.label) + (o.note ? "<em>" + esc(o.note) + "</em>" : "") + "</button>";
      }).join("");
      var typed = input.value.trim();
      if (typed && !opts.some(function (o) { return o.label === typed; })) {
        html += '<div class="combo__new"><button type="button" class="combo__opt" ' +
          'data-new="1">Create “' + esc(typed) + '”</button></div>';
      }
      list.innerHTML = html;
      at = -1;
    }
    function open() { render(); list.hidden = false; input.setAttribute("aria-expanded", "true"); }
    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); }
    function choose(el) {
      if (!el.dataset.new) {
        var o = self.last[+el.dataset.i];
        input.value = o ? o.label : el.textContent.trim();
        // One choice can fill in more than that one field: pick an album
        // before a year is set, and the year and country belong to it.
        if (o && o.fill) o.fill(o);
      }
      close(); input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    input.addEventListener("focus", open);
    input.addEventListener("input", function () { if (list.hidden) open(); else render(); });
    toggle.addEventListener("click", function () { list.hidden ? (input.focus(), open()) : close(); });
    list.addEventListener("mousedown", function (e) {
      var b = e.target.closest(".combo__opt"); if (!b) return;
      e.preventDefault(); choose(b);
    });
    input.addEventListener("keydown", function (e) {
      var items = list.querySelectorAll(".combo__opt");
      if (e.key === "Escape") { close(); return; }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (list.hidden) { open(); return; }
        e.preventDefault();
        at += (e.key === "ArrowDown" ? 1 : -1);
        if (at < 0) at = items.length - 1;
        if (at >= items.length) at = 0;
        items.forEach(function (it, i) { it.setAttribute("aria-selected", i === at); });
        if (items[at]) items[at].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && !list.hidden && at >= 0 && items[at]) {
        e.preventDefault(); choose(items[at]);
      }
    });
    document.addEventListener("click", function (e) { if (!box.contains(e.target)) close(); });
    return self;
  }

  /* Who sees the album -- set once it is committed. */
  function acl() {
    return Array.prototype.filter.call(document.querySelectorAll("[data-acl]"),
      function (c) { return c.checked; }).map(function (c) { return c.value; });
  }
  var whostate = document.getElementById("whostate");
  Array.prototype.forEach.call(document.querySelectorAll("[data-acl]"), function (c) {
    c.addEventListener("change", function () {
      var n = acl().length;
      whostate.textContent = n ? n + " picked" : "only you";
    });
  });

  var combos = {};
  Array.prototype.forEach.call(document.querySelectorAll("[data-combo]"), function (box) {
    combos[box.dataset.combo] = Combo(box);
  });
  ["year", "country", "event", "place"].forEach(function (k) {
    F[k] = $(k);
    F[k].addEventListener("input", update);
  });

  combos.year.options = function () {
    return Object.keys(known.years).sort().reverse().map(function (y) {
      var n = 0, cs = known.years[y];
      Object.keys(cs).forEach(function (c) { n += (cs[c] || []).length; });
      return { label: y, note: n + (n === 1 ? " folder" : " folders") };
    });
  };
  combos.country.options = function () {
    var y = known.years[val("year")];
    var names = y ? Object.keys(y) : known.countries;
    return names.map(function (c) {
      var n = y && y[c] ? y[c].length : 0;
      return { label: c, note: y ? n + (n === 1 ? " folder" : " folders") : "" };
    });
  };
  combos.event.options = function () {
    if (val("year") && val("country")) {
      var y = known.years[val("year")] || {};
      return (y[val("country")] || []).map(function (e) { return { label: e }; });
    }
    // No year/country picked yet: show ALL albums -- otherwise it says
    // "nothing here" when there is. Picking one brings year and country along.
    return (known.all_events || []).map(function (e) {
      return {
        label: e.event, note: e.year + " · " + e.country,
        fill: function () {
          F.year.value = e.year; F.country.value = e.country;
          if (e.place && !val("place")) F.place.value = e.place;
        }
      };
    });
  };
  combos.place.options = function () {
    return (known.places || []).map(function (p) { return { label: p }; });
  };

  function update() {
    ["year", "country", "event"].forEach(function (k) {
      F[k].closest(".field").classList.toggle("is-set", !!val(k));
    });
    if (complete()) {
      paths.className = "paths";
      paths.innerHTML =
        '<div><b>Original</b> originals / <span class="seg">' + esc(val("year")) +
        '</span> / <span class="seg">' + esc(val("country")) + '</span> / <span class="seg">' +
        esc(noYear(val("event"))) + "</span></div>" +
        '<div><b>Website</b> library / <span class="seg">' + esc(val("year")) +
        '</span> / <span class="seg">' + esc(val("country")) + '</span> / <span class="seg">' +
        esc(noYear(val("event"))) + "</span></div>";
      drop.classList.remove("is-locked");
      hint.hidden = true; ready.hidden = false; step2.textContent = "ready";
    } else {
      paths.className = "paths paths--wait";
      paths.textContent = "The path appears once year, country and name are set.";
      drop.classList.add("is-locked");
      hint.hidden = false; ready.hidden = true; step2.textContent = "locked";
    }
  }

  /* The album list is rendered into the page by the server. After a save it is
     stale -- so it is rebuilt from the same data, without a reload. */
  function redrawDrawer() {
    var d = document.getElementById("drawer");
    if (!d) return;
    var head = d.querySelector("h2");
    Array.prototype.forEach.call(d.querySelectorAll("details, .meta"), function (n) {
      if (n !== head) n.remove(); });
    var years = Object.keys(known.years).sort().reverse();
    years.forEach(function (y, i) {
      var cs = known.years[y], det = document.createElement("details");
      if (i === 0) det.open = true;
      var n = 0;
      Object.keys(cs).forEach(function (c) { n += (cs[c] || []).length; });
      var html = "<summary>" + esc(y) + "<em>" + n + "</em></summary>";
      Object.keys(cs).forEach(function (c) {
        html += '<p class="country">' + esc(c) + "</p>";
        (cs[c] || []).forEach(function (ev) {
          html += '<a class="ev" href="/y/' + encodeURIComponent(y) + "/" +
                  encodeURIComponent(c) + "/" + encodeURIComponent(ev) + '">' + esc(ev) + "</a>";
        });
      });
      det.innerHTML = html;
      d.appendChild(det);
    });
  }

  /* ⚠ `/api/tree` is admin work -- it carries the paths on disk. A contributor
     gets a 403 there, and the combos used to stay empty for them: no year, no
     country, no album to pick. That made "pick one or type a new one" into
     "type it and hope you spell it the way you did last year", and one typo is
     a second album that looks like the first.

     So: fall back to what every viewer may see anyway (`/api/albums` and
     `/api/facets`) and build the same shape out of it. Nothing new is exposed
     -- both of those are already filtered by what this person may look at. */
  function fromAlbums() {
    return Promise.all([j("/api/albums"), j("/api/facets").catch(function () {
      return { places: [] }; })]).then(function (r) {
      var years = {}, all = [];
      (r[0].albums || []).forEach(function (a) {
        (years[a.year] = years[a.year] || {})[a.country] =
          (years[a.year][a.country] || []).concat([a.event]);
        all.push({ event: a.event, year: a.year, country: a.country, place: "" });
      });
      known = { years: years, countries: Object.keys(years).reduce(function (acc, y) {
        Object.keys(years[y]).forEach(function (c) {
          if (acc.indexOf(c) < 0) acc.push(c); });
        return acc; }, []).sort(),
        places: r[1].places || [], all_events: all };
      update();
    });
  }

  function loadTree() {
    return j("/api/tree").then(function (t) { known = t; update(); })
                         .catch(function () { return fromAlbums(); })
                         .catch(function () { update(); });
  }
  loadTree();

  function row(id, name) {
    var el = document.createElement("div");
    el.className = "queue__row"; el.dataset.state = "uploading";
    el.innerHTML = "<span>" + esc(name) + '</span><span class="bar"><i></i></span>' +
                   '<span class="st">uploading</span>';
    queue.appendChild(el); queue.hidden = false; rows[id] = el;
  }
  function setRow(id, pct, state, text) {
    var el = rows[id]; if (!el) return;
    if (pct != null) el.querySelector(".bar i").style.width = pct + "%";
    if (state) el.dataset.state = state;
    if (text) el.querySelector(".st").textContent = text;
  }

  function uploadOne(file) {
    return j("/api/upload/" + batch + "/file", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: file.name, size: file.size })
    }).then(function (f) {
      row(f.file_id, file.name);
      var off = 0;
      function next() {
        if (off >= file.size) {
          setRow(f.file_id, 100, "checking", "checking");
          return j("/api/upload/" + batch + "/file/" + f.file_id + "/done", { method: "POST" })
            .then(function (d) {
              if (d.state === "rejected") setRow(f.file_id, 100, "rejected", d.note || "rejected");
              else if (d.duplicate_of) setRow(f.file_id, 100, "ready", "already known");
              else setRow(f.file_id, 100, "ready", "ready");
              return d;
            });
        }
        var end = Math.min(off + CHUNK, file.size);
        return fetch("/api/upload/" + batch + "/file/" + f.file_id + "/chunk?offset=" + off, {
          method: "PUT", headers: { "Content-Type": "application/octet-stream" },
          body: file.slice(off, end)
        }).then(function (r) {
          if (!r.ok) throw new Error("chunk " + r.status);
          off = end;
          setRow(f.file_id, Math.round(off / file.size * 100), null, "uploading");
          return next();
        });
      }
      return next();
    });
  }

  function handle(list) {
    if (!complete()) { update(); return; }
    if (!list.length) return;
    result.innerHTML = "";
    (batch ? Promise.resolve({ batch: batch }) : j("/api/upload/batch", { method: "POST" }))
      .then(function (b) {
        batch = b.batch;
        return Array.prototype.reduce.call(list, function (chain, f) {
          return chain.then(function () { return uploadOne(f); });
        }, Promise.resolve());
      })
      .then(function () { return j("/api/upload/" + batch + "/proposal"); })
      .then(function (p) {
        after.hidden = false;
        summary.textContent = p.files + " photo" + (p.files === 1 ? "" : "s") +
          (p.from ? " · " + p.from.slice(0, 10) + " → " + p.to.slice(0, 10) : "") +
          " · " + (p.bytes / 1048576).toFixed(0) + " MB";
        var n = "", exifYear = (p.proposal && p.proposal.year) || "";
        if (exifYear && exifYear !== val("year"))
          n += '<p class="warn">These were taken in <b>' + esc(exifYear) + "</b>, you chose <b>" +
               esc(val("year")) + '</b>. <button class="btn" id="useyear">use ' +
               esc(exifYear) + "</button></p>";
        if (p.date_guessed)
          n += '<p class="warn">Some carry no capture date — the file date was used.</p>';
        if (p.duplicates)
          n += '<p class="warn">' + p.duplicates + " already on the site — skipped unless you " +
               'tick this. <label><input type="checkbox" id="dups"> take them anyway</label></p>';
        notes.innerHTML = n;
        var uy = $("useyear");
        if (uy) uy.addEventListener("click", function () { F.year.value = exifYear; update(); });
      })
      .catch(function (e) { result.innerHTML = '<p class="warn">' + esc(e.message) + "</p>"; });
  }

  pick.addEventListener("change", function () { handle(pick.files); });
  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) {
      e.preventDefault(); if (complete()) drop.classList.add("is-over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("is-over"); });
  });
  drop.addEventListener("drop", function (e) { e.preventDefault(); handle(e.dataTransfer.files); });

  $("commit").addEventListener("click", function () {
    var dups = $("dups");
    j("/api/upload/" + batch + "/commit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        year: val("year"), country: val("country"), event: val("event"),
        place: val("place"), include_duplicates: !!(dups && dups.checked)
      })
    }).then(function (out) {
      out.stored.forEach(function (s) { setRow(s.photo_id, 100, "stored", "on the site"); });
      /* ⚠ Only now: before the commit the album does not exist yet, and a
         viewing list on an album that does not exist would be a row hanging
         nowhere. */
      var wien = acl();
      if (wien.length) {
        window.famPost("/api/albums/audience", {
          year: out.year, country: out.country, event: out.event, audience: wien
        }).catch(function (e) {
          result.innerHTML += '<p class="warn">The photographs are in, but the ' +
            'viewing list could not be saved: ' + esc(e.message) + '</p>';
        });
      }
      var html = '<div class="done"><b>' + out.stored.length + " stored</b><br>" +
        esc(out.folder) + "<br>" + esc(out.web_folder);
      if (out.skipped.length) html += "<br>" + out.skipped.length + " skipped (duplicates)";
      if (out.failed.length)
        html += '<br><span style="color:var(--danger)">' + out.failed.length + " failed: " +
                esc(out.failed.map(function (f) { return f.error; }).join("; ")) + "</span>";
      if (out.stored.length) {
        var y = out.year || val("year"), c = out.country || val("country"),
            e = out.event || noYear(val("event"));
        html += '<br><a class="btn" style="margin-top:.8rem" href="/y/' + encodeURIComponent(y) +
                "/" + encodeURIComponent(c) + "/" + encodeURIComponent(e) + '">see the album</a>';
      }
      result.innerHTML = html + "</div>";
      after.hidden = true; batch = null; rows = {};
      loadTree().then(redrawDrawer);
    }).catch(function (e) { result.innerHTML = '<p class="warn">' + esc(e.message) + "</p>"; });
  });

  $("reset").addEventListener("click", function () {
    batch = null; rows = {}; queue.innerHTML = ""; queue.hidden = true;
    after.hidden = true; result.innerHTML = ""; notes.innerHTML = "";
  });
})();
