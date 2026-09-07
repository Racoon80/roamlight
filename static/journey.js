/* The journey: when an album is opened, a vehicle drives or flies across our
   map from the departure to the destination (~3s), full screen, and then the
   album fades in.

   It can be MULTI-STOP: a chain of "by <mode> to <place>" (car to the airport,
   plane to Málaga, bus to the coast) -- the vehicle changes per leg. Car and
   bus follow the real road (computed on the server, `route`); plane and train
   take a stylised arc. Plays on opening, with a "skip". */
(function () {
  "use strict";
  var box = document.getElementById("journey");
  var data = document.getElementById("journey-data");
  if (!box || !data) return;

  var j;
  try { j = JSON.parse(data.textContent || "{}"); } catch (e) { j = {}; }
  if (!j.from || !j.legs || !j.legs.length) { box.parentNode.removeChild(box); return; }

  function finish() {
    box.classList.add("is-done");
    try { document.body.style.overflow = ""; } catch (e) {}
    setTimeout(function () { if (box.parentNode) box.parentNode.removeChild(box); }, 550);
  }
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    box.parentNode.removeChild(box); return;
  }
  var pg = new URLSearchParams(location.search).get("page");
  if (pg && pg !== "1") { box.parentNode.removeChild(box); return; }

  try { document.body.style.overflow = "hidden"; } catch (e) {}
  var skip = document.getElementById("journey-skip");
  if (skip) skip.addEventListener("click", finish);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") finish(); });
  if (!window.L) { finish(); return; }

  function glyph(mode) {
    return mode === "plane" ? "✈" : (mode === "train" ? "🚂" : (mode === "bus" ? "🚌" : "🚗"));
  }
  function arc(a, b, mode) {
    var dLat = b[0] - a[0], dLon = b[1] - a[1];
    var len = Math.sqrt(dLat * dLat + dLon * dLon) || 0.0001;
    var bow = len * (mode === "plane" ? 0.28 : (mode === "train" ? 0.14 : 0.08));
    var ctrl = [(a[0] + b[0]) / 2 + (-dLon / len) * bow,
                (a[1] + b[1]) / 2 + (dLat / len) * bow];
    var out = [];
    for (var i = 0; i <= 48; i++) {
      var t = i / 48, m = 1 - t;
      out.push([m * m * a[0] + 2 * m * t * ctrl[0] + t * t * b[0],
                m * m * a[1] + 2 * m * t * ctrl[1] + t * t * b[1]]);
    }
    return out;
  }

  // Build the legs: per leg the points (road or arc) + the running length.
  var segs = [], prev = j.from, allPts = [j.from];
  for (var li = 0; li < j.legs.length; li++) {
    var leg = j.legs[li], mode = leg.transport || "car";
    var pts = (leg.route && leg.route.length > 1) ? leg.route : arc(prev, leg.to, mode);
    var cum = [0];
    for (var k = 1; k < pts.length; k++) {
      cum.push(cum[k - 1] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]));
    }
    segs.push({ pts: pts, cum: cum, len: cum[cum.length - 1] || 0.0001, mode: mode });
    allPts = allPts.concat(pts);
    prev = leg.to;
  }

  var map = L.map(document.getElementById("journey-map"), {
    zoomControl: false, attributionControl: false, dragging: false,
    scrollWheelZoom: false, doubleClickZoom: false, boxZoom: false,
    keyboard: false, touchZoom: false, inertia: false
  });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(map);
  var bounds = L.latLngBounds(allPts);

  function pin(ll) {
    L.marker(ll, { icon: L.divIcon({ className: "", html: '<div class="map-pin"></div>', iconSize: [12, 12] }) }).addTo(map);
  }
  pin(j.from);
  for (var s = 0; s < segs.length; s++) {
    L.polyline(segs[s].pts, { color: "#6aa9e0", weight: 3, opacity: 0.9,
      dashArray: (segs[s].mode === "plane" || segs[s].mode === "train") ? "6 9" : null }).addTo(map);
    pin(j.legs[s].to);
  }

  function pointOnSeg(seg, dist) {
    var target = Math.max(0, Math.min(dist, seg.len));
    for (var i = 1; i < seg.pts.length; i++) {
      if (seg.cum[i] >= target) {
        var sg = seg.cum[i] - seg.cum[i - 1] || 1, st = (target - seg.cum[i - 1]) / sg;
        return [seg.pts[i - 1][0] + (seg.pts[i][0] - seg.pts[i - 1][0]) * st,
                seg.pts[i - 1][1] + (seg.pts[i][1] - seg.pts[i - 1][1]) * st];
      }
    }
    return seg.pts[seg.pts.length - 1];
  }
  var totalLen = 0;
  for (var t2 = 0; t2 < segs.length; t2++) totalLen += segs[t2].len;
  totalLen = totalLen || 1;

  var veh = L.marker(j.from, {
    icon: L.divIcon({ className: "journey__veh", html: "<span>" + glyph(segs[0].mode) + "</span>", iconSize: [34, 34] }),
    zIndexOffset: 1000
  }).addTo(map);
  var shownSeg = 0;

  var DURATION = Math.min(5200, 2400 + segs.length * 700), t0 = null;
  function frame(ts) {
    if (t0 === null) t0 = ts;
    var t = Math.min(1, (ts - t0) / DURATION);
    var e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;   // ease-in-out
    var d = e * totalLen, acc = 0, seg = segs.length - 1, local = 0;
    for (var i = 0; i < segs.length; i++) {
      if (d <= acc + segs[i].len || i === segs.length - 1) { seg = i; local = d - acc; break; }
      acc += segs[i].len;
    }
    if (seg !== shownSeg) {
      shownSeg = seg;
      veh.setIcon(L.divIcon({ className: "journey__veh", html: "<span>" + glyph(segs[seg].mode) + "</span>", iconSize: [34, 34] }));
    }
    veh.setLatLng(pointOnSeg(segs[seg], local));
    if (t < 1) requestAnimationFrame(frame);
    else setTimeout(finish, 650);
  }
  // ⚠ Fix the size FIRST, THEN `fitBounds` -- otherwise Leaflet computes the
  // zoom with a wrong (not-yet-finished) map size and zooms out too far, which
  // leaves a lot of empty space above and below. A small margin (40px) so the
  // journey fills the screen and the animation is big.
  setTimeout(function () {
    map.invalidateSize();
    // A small margin (30px) -> Leaflet can go one zoom step closer, so the
    // journey fills the screen (otherwise too much empty space stays).
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
    requestAnimationFrame(frame);
  }, 300);
})();
