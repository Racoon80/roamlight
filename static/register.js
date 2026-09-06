/* The register: select and act.
   A click selects, shift-click a range, cmd/ctrl-click adds a single one. */
(function () {
  "use strict";
  var grid = document.getElementById("reg");
  if (!grid) return;
  var bar = document.getElementById("reg-bar");
  var count = document.getElementById("reg-count");
  var out = document.getElementById("reg-out");
  var items = Array.prototype.slice.call(grid.querySelectorAll(".reg-item"));
  var last = -1;

  function selected() {
    return items.filter(function (el) { return el.getAttribute("aria-selected") === "true"; });
  }
  function refresh() {
    var n = selected().length;
    count.textContent = n + " selected";
    /* ⚠ The bar is ALWAYS there. It used to be hidden until something was
       selected -- and so nobody knew that it existed. */
    count.classList.toggle("is-none", n === 0);
    Array.prototype.forEach.call(bar.querySelectorAll("[data-act]"),
      function (b) { b.disabled = n === 0; });
  }
  function pick(el, on) { el.setAttribute("aria-selected", on ? "true" : "false"); }

  /* --- Keyboard: the same as on the tagging page --- */
  var fokus = 0;
  function setFokus(i) {
    if (i < 0 || i >= items.length) return;
    fokus = i;
    items[i].scrollIntoView({ block: "nearest" });
    items.forEach(function (c, k) { c.classList.toggle("is-cursor", k === i); });
  }
  function proRei() {
    if (!items.length) return 1;
    var y = items[0].getBoundingClientRect().top, n = 0;
    for (var k = 0; k < items.length; k++) {
      if (Math.abs(items[k].getBoundingClientRect().top - y) > 4) break;
      n++;
    }
    return n || 1;
  }
  document.addEventListener("keydown", function (e) {
    var tg = e.target.tagName;
    if (tg === "INPUT" || tg === "SELECT" || tg === "TEXTAREA") return;
    var rei = proRei();
    if (e.key === "ArrowRight") { setFokus(fokus + 1); e.preventDefault(); return; }
    if (e.key === "ArrowLeft") { setFokus(fokus - 1); e.preventDefault(); return; }
    if (e.key === "ArrowDown") { setFokus(fokus + rei); e.preventDefault(); return; }
    if (e.key === "ArrowUp") { setFokus(fokus - rei); e.preventDefault(); return; }
    if (e.key === " ") {
      var el = items[fokus];
      if (el) {
        if (e.shiftKey && last >= 0) {
          var a = Math.min(last, fokus), b = Math.max(last, fokus);
          for (var k = a; k <= b; k++) pick(items[k], true);
        } else {
          pick(el, el.getAttribute("aria-selected") !== "true");
          last = fokus;
        }
        refresh();
      }
      e.preventDefault(); return;
    }
    if ((e.metaKey || e.ctrlKey) && (e.key === "a" || e.key === "A")) {
      items.forEach(function (c) { pick(c, true); });
      last = 0; refresh(); e.preventDefault(); return;
    }
    if (e.key === "Escape") {
      items.forEach(function (c) { pick(c, false); });
      last = -1; refresh();
    }
  });

  document.getElementById("reg-all").addEventListener("click", function () {
    items.forEach(function (c) { pick(c, true); });
    last = 0; refresh();
  });

  grid.addEventListener("click", function (e) {
    var el = e.target.closest(".reg-item");
    if (!el) return;
    var i = items.indexOf(el);
    setFokus(i);
    if (e.shiftKey && last >= 0) {
      var a = Math.min(last, i), b = Math.max(last, i);
      for (var k = a; k <= b; k++) pick(items[k], true);
    } else if (e.metaKey || e.ctrlKey) {
      pick(el, el.getAttribute("aria-selected") !== "true");
    } else {
      var was = el.getAttribute("aria-selected") === "true";
      items.forEach(function (x) { pick(x, false); });
      pick(el, !was);
    }
    last = i;
    refresh();
  });

  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a" &&
        document.activeElement.closest && document.activeElement.closest("#reg")) {
      e.preventDefault(); items.forEach(function (x) { pick(x, true); }); refresh();
    }
    if (e.key === "Escape") { items.forEach(function (x) { pick(x, false); }); refresh(); }
  });

  document.getElementById("reg-none").addEventListener("click", function () {
    items.forEach(function (x) { pick(x, false); }); refresh();
  });

  bar.addEventListener("click", function (e) {
    var b = e.target.closest("[data-act]");
    if (!b) return;
    var act = b.dataset.act;
    var ids = selected().map(function (el) { return +el.dataset.id; });
    if (!ids.length) return;
    // A cover is ONE photograph: exactly one has to be selected.
    if (act === "cover" && ids.length !== 1) {
      out.textContent = "pick exactly one photograph for the cover";
      return;
    }
    if (act === "remove" && !window.confirm(
        ids.length + " photograph" + (ids.length === 1 ? "" : "s") +
        " will be taken off the site.\n\nThe originals are " +
        "NOT touched — this only removes the site's own copy.")) return;
    out.textContent = "working…";
    window.famPost("/api/photos/bulk", { action: act, ids: ids }).then(function (d) {
      if (d.detail) { out.textContent = d.detail; return; }
      out.textContent = "done";
      window.location.reload();
    }).catch(function (err) { out.textContent = err.message; });
  });
})();
