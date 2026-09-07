/* The tagging machine.

   The goal: 200 photographs in one sitting. Which means: the hand stays on the
   keyboard, and the bar at the bottom never disappears. 200 clicks turn into
   20 key presses. */
(function () {
  "use strict";
  var grid = document.getElementById("grid");
  if (!grid) return;

  var cells = Array.prototype.slice.call(grid.querySelectorAll(".tcell"));
  var bar   = document.getElementById("tbar");
  var count = document.getElementById("tcount");
  var say   = document.getElementById("tsay");
  var anchor = -1;      // for shift ranges
  var focused = 0;
  var letzt = null;    // fir `R`

  function isPicked(el) { return el.getAttribute("aria-selected") === "true"; }
  function pick(el, on) { el.setAttribute("aria-selected", on ? "true" : "false"); }
  function selection() { return cells.filter(isPicked); }
  function ids() { return selection().map(function (c) { return +c.dataset.id; }); }

  function refresh() {
    var n = selection().length;
    count.textContent = n + " selected";
    bar.hidden = false;                 // ⚠ always there, even at 0
    count.classList.toggle("is-none", n === 0);
  }

  function setFokus(i) {
    if (i < 0 || i >= cells.length) return;
    focused = i;
    cells[i].scrollIntoView({ block: "nearest" });
    cells.forEach(function (c, k) { c.classList.toggle("is-cursor", k === i); });
  }

  function range(a, b, on) {
    var von = Math.min(a, b), bis = Math.max(a, b);
    for (var k = von; k <= bis; k++) pick(cells[k], on);
  }

  /* --- Maus ------------------------------------------------------------ */
  grid.addEventListener("click", function (e) {
    var el = e.target.closest(".tcell");
    if (!el) return;
    var i = cells.indexOf(el);
    if (e.shiftKey && anchor >= 0) {
      range(anchor, i, true);
    } else if (e.metaKey || e.ctrlKey) {
      pick(el, !isPicked(el)); anchor = i;
    } else {
      var wasOnly = isPicked(el) && selection().length === 1;
      cells.forEach(function (c) { pick(c, false); });
      pick(el, !wasOnly); anchor = i;
    }
    setFokus(i); refresh();
  });

  /* --- How many cells in a row? For ↑ ↓ ------------------------------ */
  function proRei() {
    if (!cells.length) return 1;
    var y = cells[0].getBoundingClientRect().top, n = 0;
    for (var k = 0; k < cells.length; k++) {
      if (Math.abs(cells[k].getBoundingClientRect().top - y) > 4) break;
      n++;
    }
    return n || 1;
  }

  /* --- Tastatur -------------------------------------------------------- */
  document.addEventListener("keydown", function (e) {
    var t = e.target.tagName;
    if (t === "INPUT" || t === "SELECT" || t === "TEXTAREA") return;

    var rei = proRei();
    if (e.key === "ArrowRight") { setFokus(focused + 1); e.preventDefault(); return; }
    if (e.key === "ArrowLeft")  { setFokus(focused - 1); e.preventDefault(); return; }
    if (e.key === "ArrowDown")  { setFokus(focused + rei); e.preventDefault(); return; }
    if (e.key === "ArrowUp")    { setFokus(focused - rei); e.preventDefault(); return; }

    if (e.key === " ") {
      var el = cells[focused];
      if (el) {
        if (e.shiftKey && anchor >= 0) range(anchor, focused, true);
        else { pick(el, !isPicked(el)); anchor = focused; }
        refresh();
      }
      e.preventDefault(); return;
    }
    if ((e.metaKey || e.ctrlKey) && (e.key === "a" || e.key === "A")) {
      cells.forEach(function (c) { pick(c, true); });
      anchor = 0; refresh(); e.preventDefault(); return;
    }
    if (e.key === "Escape") {
      cells.forEach(function (c) { pick(c, false); });
      anchor = -1; refresh(); return;
    }
    if (e.key === "r" || e.key === "R") {
      if (letzt) mark(letzt.kind, letzt.name, true);
      e.preventDefault(); return;
    }
    /* 1-9 sets the Nth most recent person, shift+1-9 takes them off.
       ⚠ With shift, a German or Luxembourgish keyboard sends `!` and not `1`
       -- so `e.code` (KeyDigit1) is used and not `e.key`. */
    var m = /^Digit([1-9])$/.exec(e.code || "");
    if (m && !e.metaKey && !e.ctrlKey && !e.altKey) {
      var keys = document.querySelectorAll("#tkeys .keycap");
      var k = keys[+m[1] - 1];
      if (k) mark(k.dataset.kind, k.dataset.name, !e.shiftKey);
      e.preventDefault();
    }
  });

  /* --- Zouweisen ------------------------------------------------------- */
  function mark(kind, name, on) {
    var listEl = ids();
    if (!listEl.length) { say.textContent = "nothing selected"; return; }
    letzt = { kind: kind, name: name };
    say.textContent = "…";
    /* `names` and not `name`: with a comma you can set more than one at once,
       and the server does the splitting. */
    window.famPost("/api/mark", { kind: kind, names: name, ids: listEl, on: !!on })
      .then(function (d) {
        say.textContent = (on ? "＋ " : "− ") + d.names.join(", ") + " · " + d.photos;
        d.names.forEach(function (n) { pills(kind, n, on, listEl); });
      })
      .catch(function (err) { say.textContent = "✗ " + err.message; });
  }

  /* Update the pills straight away -- reloading the page after every
     assignment is exactly what makes the session slow. */
  function pills(kind, name, on, listEl) {
    var klass = kind === "tag" ? "pill pill--tag" : (kind === "set" ? "pill pill--set" : "pill");
    listEl.forEach(function (id) {
      var cell = grid.querySelector('.tcell[data-id="' + id + '"]');
      if (!cell) return;
      var box = cell.querySelector(".tcell__who");
      var da = Array.prototype.filter.call(box.children, function (s) {
        return s.textContent === name && s.className === klass; });
      if (on && !da.length) {
        var s = document.createElement("span");
        s.className = klass; s.textContent = name; box.appendChild(s);
      } else if (!on) {
        da.forEach(function (s) { s.remove(); });
      }
    });
  }

  /* The default list: one click puts the tag on the whole selection.
     ⚠ The chip stays there and takes no row in the database -- a suggestion
     only becomes a tag once it really sits on a photograph. */
  Array.prototype.forEach.call(document.querySelectorAll(".tagbank [data-tag]"),
    function (chip) {
      chip.addEventListener("click", function () {
        mark("tag", chip.dataset.tag, true);
        chip.classList.add("chip--just");
        setTimeout(function () { chip.classList.remove("chip--just"); }, 900);
      });
    });

  Array.prototype.forEach.call(document.querySelectorAll("#tkeys .keycap"),
    function (b) {
      b.addEventListener("click", function () { mark(b.dataset.kind, b.dataset.name, true); });
    });

  document.getElementById("tnone").addEventListener("click", function () {
    cells.forEach(function (c) { pick(c, false); }); anchor = -1; refresh();
  });

  function neien(inputId, kind) {
    var inp = document.getElementById(inputId);
    var name = inp.value.trim();
    if (!name) { inp.focus(); return; }
    mark(kind, name, true);
    inp.value = "";
  }
  document.getElementById("addperson").addEventListener("click",
    function () { neien("newname", "person"); });
  document.getElementById("addtag").addEventListener("click",
    function () { neien("newtag", "tag"); });
  ["newname", "newtag"].forEach(function (id) {
    document.getElementById(id).addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        neien(id, id === "newtag" ? "tag" : "person");
      }
    });
  });

  /* --- Into a collection ----------------------------------------------- */
  document.getElementById("addset").addEventListener("click", function () {
    var sel = document.getElementById("setpick");
    var listEl = ids();
    if (!listEl.length) { say.textContent = "nothing selected"; return; }
    var body;
    if (sel.value === "__new") {
      var titel = window.prompt("Name for the new collection?");
      if (!titel) return;
      body = { action: "new", title: titel, ids: listEl };
    } else if (sel.value) {
      body = { action: "add", id: +sel.value, ids: listEl };
    } else {
      say.textContent = "pick a collection first"; return;
    }
    say.textContent = "…";
    window.famPost("/api/collections", body).then(function (d) {
      say.textContent = "→ " + d.title + " · " + d.n;
      pills("set", d.title, true, listEl);
      if (body.action === "new") {
        var o = document.createElement("option");
        o.value = d.id; o.textContent = d.title + " (" + d.n + ")";
        sel.insertBefore(o, sel.lastElementChild);
        sel.value = d.id;
      }
    }).catch(function (err) { say.textContent = "✗ " + err.message; });
  });

  setFokus(0);
  refresh();
})();
