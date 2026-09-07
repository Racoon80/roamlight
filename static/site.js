/* Album grid and lightbox. No framework -- the page is rendered on the server;
   only what HTML cannot do is here. */
(function () {
  "use strict";

  /* --- Riddel ------------------------------------------------------------ */
  var drawer = document.getElementById("drawer");
  var open = document.getElementById("drawer-open");
  var close = document.getElementById("drawer-close");
  var scrim = document.getElementById("drawer-scrim");
  if (drawer && open) {
    var set = function (on) {
      drawer.dataset.open = on ? "1" : "0";
      open.setAttribute("aria-expanded", on ? "true" : "false");
      try { localStorage.setItem("family.drawer", on ? "1" : "0"); } catch (e) {}
    };
    var saved = "0";
    try { saved = localStorage.getItem("family.drawer") || "0"; } catch (e) {}
    set(saved === "1");
    open.addEventListener("click", function () { set(drawer.dataset.open === "0"); });
    if (close) close.addEventListener("click", function () { set(false); });
    if (scrim) scrim.addEventListener("click", function () { set(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer.dataset.open === "1") set(false);
    });
  }

  /* --- Lightbox ---------------------------------------------------------- */
  var lb = document.getElementById("lightbox");
  if (!lb) return;
  var img = document.getElementById("lb-img");
  var video = document.getElementById("lb-video");
  var plate = document.getElementById("lb-plate");
  var caption = document.getElementById("lb-caption");
  var exif = document.getElementById("lb-exif");
  var dl = document.getElementById("lb-dl");
  var ids = [], kinds = [], at = -1;

  function collect() {
    var els = document.querySelectorAll("[data-lightbox]");
    ids = Array.prototype.map.call(els, function (el) { return el.dataset.lightbox; });
    kinds = Array.prototype.map.call(els, function (el) { return el.dataset.kind || "photo"; });
  }
  function stopVideo() {
    if (!video) return;
    try { video.pause(); } catch (e) {}
    video.removeAttribute("src"); video.load && video.load(); video.hidden = true;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; });
  }
  function show(i) {
    if (i < 0 || i >= ids.length) return;
    at = i;
    var id = ids[i];
    if (kinds[i] === "video") {
      img.hidden = true;
      video.hidden = false;
      video.poster = "/photos/" + id + "/1200.webp";
      video.src = "/photos/" + id + "/video.mp4";
      if (video.load) video.load();
      dl.href = "/photos/" + id + "/video.mp4";
    } else {
      stopVideo();
      img.hidden = false;
      img.src = "/photos/" + id + "/2000.webp";
      dl.href = "/photos/" + id + "/master.jpg";
    }
    plate.textContent = "PL. " + ("00" + (i + 1)).slice(-3);
    caption.textContent = "";
    exif.textContent = "";
    fetch("/api/photo/" + id, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (p) {
        if (!p || ids[at] !== id) return;
        caption.textContent = [p.title, p.place, p.country].filter(Boolean).join(" · ");
        exif.textContent = [
          p.taken_at ? p.taken_at.slice(0, 16) : null,
          p.camera, p.lens,
          [p.aperture, p.shutter, p.iso ? "ISO " + p.iso : null].filter(Boolean).join(" "),
          (p.width && p.height) ? p.width + "×" + p.height : null,
          (p.people && p.people.length) ? p.people.join(", ") : null,
          (p.tags && p.tags.length) ? p.tags.join(", ") : null
        ].filter(Boolean).join("  ·  ");
      });
  }
  function openLb(i) {
    collect(); lb.classList.add("is-open");
    document.body.style.overflow = "hidden"; show(i);
  }
  function closeLb() {
    lb.classList.remove("is-open"); img.src = ""; img.hidden = false;
    stopVideo(); document.body.style.overflow = "";
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-lightbox]");
    if (!el) return;
    collect(); openLb(ids.indexOf(el.dataset.lightbox));
  });
  document.addEventListener("keydown", function (e) {
    var el = document.activeElement;
    if ((e.key === "Enter" || e.key === " ") && el && el.dataset && el.dataset.lightbox) {
      e.preventDefault(); collect(); openLb(ids.indexOf(el.dataset.lightbox)); return;
    }
    if (!lb.classList.contains("is-open")) return;
    if (e.key === "Escape") closeLb();
    else if (e.key === "ArrowLeft") show(at - 1);
    else if (e.key === "ArrowRight") show(at + 1);
  });
  document.getElementById("lb-close").addEventListener("click", closeLb);
  document.getElementById("lb-prev").addEventListener("click", function () { show(at - 1); });
  document.getElementById("lb-next").addEventListener("click", function () { show(at + 1); });
})();

/* --- The map --------------------------------------------------------------
   ⚠ The tiles come STRAIGHT from OpenStreetMap, not through this site. That is
   a decision, and it has a price -- see the note at the tile layer below. This
   comment used to say the opposite, which was simply out of date: the /tiles/
   proxy (app/tiles.py) still exists but nothing calls it. */
(function () {
  "use strict";
  var el = document.getElementById("map");
  var data = document.getElementById("map-data");
  if (!el || !data || !window.L) return;
  var points;
  try { points = JSON.parse(data.textContent || "[]"); } catch (e) { return; }
  if (!points.length) return;

  /* Offline, the tiles only go down to a certain level in the container (see
     app/tileseed.py). Closer in there would be nothing but empty squares --
     so the map is not allowed to zoom deeper than what was seeded. */
  var maxZoom = parseInt(el.dataset.maxZoom, 10) || 18;

  /* ⚠ `scrollWheelZoom` used to be `false`, because a map sitting in the middle
     of a page otherwise catches the scroll. The answer is not "switch it off"
     -- it is "zoom with the wheel, and the page keeps scrolling as long as the
     map does not have the focus". Leaflet can do both: `scrollWheelZoom` on,
     and the map only takes the wheel once it has been clicked or hovered. */
  var map = L.map(el, {
    scrollWheelZoom: true,
    wheelDebounceTime: 25,       // reacts at once, not after 40 ms
    wheelPxPerZoomLevel: 45,     // less scrolling to zoom in (quicker)
    zoomSnap: 0.25,              // weich, net vun 6 op 7
    zoomDelta: 0.5,
    zoomControl: true,
    maxZoom: maxZoom,
    worldCopyJump: true
  });
  // ⚠ The tiles come DIRECTLY from OSM here -- chosen deliberately: always
  //   there, no holes, no upkeep. The price: the browser talks to OSM. That is
  //   why OSM is in the CSP (img-src) as well. The /tiles/ proxy stays in the
  //   code but is no longer used.
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 18,
    keepBuffer: 3                // a margin ahead, so dragging does not tear
  }).addTo(map);

  /* The map only takes the wheel once you are really on it. Otherwise you
     scroll past the page and the map pulls the page out from under you. */
  map.scrollWheelZoom.disable();
  el.addEventListener("mouseenter", function () { map.scrollWheelZoom.enable(); });
  el.addEventListener("mouseleave", function () { map.scrollWheelZoom.disable(); });
  el.addEventListener("touchstart", function () { map.scrollWheelZoom.enable(); },
                      { passive: true });

  /* Double-click zooms in, shift+double-click out -- and the keyboard works
     too, once the map has the focus. */
  map.doubleClickZoom.enable();
  map.keyboard.enable();

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; });
  }

  var bounds = [];
  points.forEach(function (p) {
    bounds.push([p.lat, p.lon]);
    L.marker([p.lat, p.lon], {
      icon: L.divIcon({ className: "", html: '<div class="map-pin"></div>', iconSize: [12, 12] }),
      title: p.title
    }).addTo(map).bindPopup(
      '<a class="map-popup" href="' + esc(p.url) + '">' +
      '<img src="/photos/' + p.cover + '/400.webp" alt="">' +
      "<b>" + esc(p.title) + "</b>" +
      '<span class="meta">' + esc(p.where) + " · " + p.n +
      " plate" + (p.n === 1 ? "" : "s") + "</span>" +
      '<span class="meta">' + esc(p.source) + "</span></a>",
      { className: "map-popup-wrap" });
  });
  if (bounds.length === 1) map.setView(bounds[0], 9);
  else map.fitBounds(bounds, { padding: [56, 56] });

  /* A button that shows everything again -- after three zooms nobody knows
     where they were. */
  var resetBtn = L.control({ position: "topright" });
  resetBtn.onAdd = function () {
    var d = L.DomUtil.create("div", "leaflet-bar map-reset");
    d.innerHTML = '<a href="#" title="Show everything again" '
                + 'aria-label="Show everything again">&#9633;</a>';
    L.DomEvent.on(d, "click", function (e) {
      L.DomEvent.stop(e);
      if (bounds.length === 1) map.setView(bounds[0], 9);
      else map.fitBounds(bounds, { padding: [56, 56] });
    });
    return d;
  };
  resetBtn.addTo(map);

  /* The map should fit its box again when the window changes size --
     otherwise a grey area is left standing. */
  window.addEventListener("resize", function () { map.invalidateSize(); });
})();

/* --- SPLIT: the photograph on the left follows the list on the right. ------ */
(function () {
  "use strict";
  var stage = document.getElementById("sp-img");
  if (!stage) return;
  var plate = document.getElementById("sp-plate"), meta = document.getElementById("sp-meta");
  document.addEventListener("mouseover", function (e) {
    var el = e.target.closest("[data-img]");
    if (!el) return;
    stage.src = el.dataset.img;
    if (plate && el.dataset.plate) plate.textContent = "PL. " + el.dataset.plate;
    if (meta && el.dataset.meta) meta.textContent = el.dataset.meta;
  });
})();

/* --- Collage: justified rows, scattered and turned ------------------------
   The photographs stay in the order they come in (strictly by date). Per row
   as many go side by side as fit the width, all computed to the same height --
   so every row fills the width completely, with no holes. A slight tilt and an
   overlap (a negative margin) turn that into a scattered pile. */
(function () {
  "use strict";
  var el = document.getElementById("collage");
  if (!el) return;
  var snaps = Array.prototype.slice.call(el.querySelectorAll(".snap"));
  if (!snaps.length) return;

  var MARGIN = 7;             /* ⚠ == margin: -7px op .snap am site.css */

  /* Fixe „Zoufall" pro Index: de Kipp bleift datselwecht iwwer e Resize. */
  function rnd(i) { var x = Math.sin(i * 99.13 + 0.7) * 10000; return x - Math.floor(x); }
  snaps.forEach(function (s, i) {
    s.style.setProperty("--rot", ((rnd(i) * 2 - 1) * 5).toFixed(2) + "deg");
    s.style.setProperty("--z", (Math.floor(rnd(i + 7) * 8) + 1));
  });

  function layout() {
    var W = el.clientWidth;
    if (W <= 0) return;
    /* Taller rows = bigger photographs; on wide screens a little more again. */
    var targetH = W < 640 ? 170 : (W < 1100 ? 250 : (W < 1700 ? 300 : 340));
    var gap = -2 * MARGIN;            /* negative -> the cards overlap */
    var row = [], sumAR = 0;

    function flush(last) {
      if (!row.length) return;
      var n = row.length;
      var h = (W - gap * (n - 1)) / sumAR;      /* the row fills the width */
      if (last) h = Math.min(h, targetH);       /* do not blow up a half row */
      row.forEach(function (o) {
        o.s.style.width = (o.ar * h) + "px";
        o.s.style.height = h + "px";
      });
      row = []; sumAR = 0;
    }

    for (var i = 0; i < snaps.length; i++) {
      var ar = (parseFloat(snaps[i].dataset.w) || 3) / (parseFloat(snaps[i].dataset.h) || 2);
      row.push({ s: snaps[i], ar: ar }); sumAR += ar;
      if (sumAR * targetH + gap * (row.length - 1) >= W) flush(false);
    }
    flush(true);
  }

  layout();
  var t;
  window.addEventListener("resize", function () {
    clearTimeout(t); t = setTimeout(layout, 120);
  });
})();

/* --- Sharing straight out of an album -------------------------------------
   Creates a collection from the album's photographs on the server and shares
   it (see /api/albums/share). An admin shares the whole album, an account
   holder only their own photographs. The password is shown ONCE. */
(function () {
  "use strict";
  var btn = document.getElementById("share-album");
  if (!btn || !window.famPost) return;
  var fresh = document.getElementById("share-fresh"),
      txt = document.getElementById("share-text"),
      say = document.getElementById("share-say"),
      copy = document.getElementById("share-copy");

  var viewsSel = document.getElementById("share-views");
  btn.addEventListener("click", function () {
    btn.disabled = true; say.textContent = "…";
    var body = { year: btn.dataset.year, country: btn.dataset.country,
                 event: btn.dataset.event, days: 30 };
    if (viewsSel) body.max_views = viewsSel.value;   // only the admin gets the choice
    window.famPost("/api/albums/share", body).then(function (d) {
      btn.disabled = false; say.textContent = "";
      if (!d || !d.url) { say.textContent = "could not make the link"; return; }
      txt.textContent = d.url
        + "\nPassword:  " + d.password
        + "\nExpires:   " + (d.expires_at || "").slice(0, 10)
        + "\nOpens:     " + (d.max_views ? (d.max_views + " time" + (d.max_views === 1 ? "" : "s")) : "unlimited")
        + "\n" + d.n + " photograph" + (d.n === 1 ? "" : "s");
      fresh.hidden = false;
      btn.textContent = "Make another link";
    }).catch(function (err) {
      btn.disabled = false; say.textContent = "✗ " + err.message;
    });
  });

  if (copy) copy.addEventListener("click", function () {
    var s = txt.textContent;
    function fallback() {
      var t = document.createElement("textarea");
      t.value = s; document.body.appendChild(t); t.select();
      try { document.execCommand("copy"); say.textContent = "copied"; }
      catch (e) { say.textContent = "select the text above and copy it"; }
      t.remove();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(s)
        .then(function () { say.textContent = "copied"; }).catch(fallback);
    } else { fallback(); }
  });
})();

/* User menu (top right): open/close, click outside or Esc to shut. */
(function () {
  "use strict";
  var menu = document.getElementById("usermenu");
  if (!menu) return;
  var btn = document.getElementById("usermenu-btn");
  var panel = document.getElementById("usermenu-panel");
  if (!btn || !panel) return;

  function setOpen(on) {
    panel.hidden = !on;
    btn.setAttribute("aria-expanded", on ? "true" : "false");
  }
  btn.addEventListener("click", function (e) {
    e.stopPropagation();
    setOpen(panel.hidden);
  });
  document.addEventListener("click", function (e) {
    if (!panel.hidden && !menu.contains(e.target)) setOpen(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !panel.hidden) { setOpen(false); btn.focus(); }
  });
})();

/* Adding photographs (a member): they land in quarantine, the admin accepts
   them. Nothing goes straight onto the site -- the same path as a guest
   upload. Works for ANY number of album rows on a page (delegated via data
   attributes). */
(function () {
  "use strict";
  // "Add photos" -> shows the hidden .contribute box in that same row.
  document.addEventListener("click", function (e) {
    var trg = e.target.closest ? e.target.closest("[data-contribute]") : null;
    if (!trg) return;
    var scope = trg.closest("[data-album-row]") || document;
    var box = scope.querySelector(".contribute");
    if (!box) return;
    box.hidden = !box.hidden;
    if (!box.hidden) {
      var f = box.querySelector("[data-contribute-file]");
      if (f) f.focus();
    }
  });

  document.addEventListener("change", function (e) {
    var file = e.target;
    if (!file.matches || !file.matches("[data-contribute-file]")) return;
    var files = Array.prototype.slice.call(file.files || []);
    if (!files.length) return;
    var box = file.closest(".contribute") || file.parentNode;
    var say = box.querySelector("[data-contribute-say]");
    var y = file.dataset.year, c = file.dataset.country, ev = file.dataset.event;
    var url = "/y/" + encodeURIComponent(y) + "/" + encodeURIComponent(c) +
              "/" + encodeURIComponent(ev) + "/contribute";
    var done = 0, failed = 0, i = 0;
    function tell(t) { if (say) say.textContent = t; }
    tell("sending 1 of " + files.length + "…");

    function next() {
      if (i >= files.length) {
        file.value = "";
        tell(done + " added to the album — reload to see "
          + (done === 1 ? "it" : "them")
          + (failed ? " · " + failed + " could not be added" : ""));
        return;
      }
      var fd = new FormData();
      fd.append("file", files[i]);
      tell("sending " + (i + 1) + " of " + files.length + "…");
      fetch(url, { method: "POST", body: fd, credentials: "same-origin" })
        .then(function (r) {
          if (r.ok) { done++; } else {
            failed++;
            return r.json().then(function (d) {
              tell("✗ " + ((d && d.detail) || "could not send that file"));
            }).catch(function () {});
          }
        })
        .catch(function () { failed++; })
        .then(function () { i++; next(); });
    }
    next();
  });
})();
/* The opening film (splash): a journey across Europe. Plays ONCE per session on
   the front page, then fades out. Skip / click / escape closes it early. */
(function () {
  "use strict";
  var box = document.getElementById("splash");
  if (!box) return;
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    if (box.parentNode) box.parentNode.removeChild(box); return;
  }
  try {
    if (sessionStorage.getItem("splash-seen")) { if (box.parentNode) box.parentNode.removeChild(box); return; }
    sessionStorage.setItem("splash-seen", "1");
  } catch (e) {}
  var timer = null;
  function finish() {
    if (timer) { clearTimeout(timer); timer = null; }
    box.classList.add("is-done");
    try { document.body.style.overflow = ""; } catch (e) {}
    setTimeout(function () { if (box.parentNode) box.parentNode.removeChild(box); }, 650);
  }
  box.hidden = false;
  var svg = document.getElementById("splash-svg");
  try { if (svg && svg.setCurrentTime) svg.setCurrentTime(0); } catch (e) {}
  try { document.body.style.overflow = "hidden"; } catch (e) {}
  var skip = document.getElementById("splash-skip");
  if (skip) skip.addEventListener("click", function (e) { e.stopPropagation(); finish(); });
  box.addEventListener("click", finish);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !box.classList.contains("is-done")) finish(); });
  timer = setTimeout(finish, 6300);
})();
